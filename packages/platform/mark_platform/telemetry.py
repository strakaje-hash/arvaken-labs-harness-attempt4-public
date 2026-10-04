"""OpenTelemetry everywhere (Task 4.1), with the properties the measurement depends on made explicit:

  - one trace per scenario: the harness starts the scenario span and propagates `traceparent` + `baggage`
    (mark.scenario_id) into the agent process (env OTEL_TRACEPARENT / MARK_SCENARIO_ID) and into every HTTP call;
  - no sampling: the SDK is configured with ALWAYS_ON and the collector (pod/otel-collector.yaml) has no sampler;
  - every span carries `mark.mono_ns` at start and end (monotonic, host-wide) so span arithmetic never touches
    the wall clock;
  - exporter: OTLP/HTTP to the in-pod collector when OTEL_EXPORTER_OTLP_ENDPOINT is set, else an in-memory
    exporter (tests) plus an optional JSONL file exporter (the trace archive in the run bundle).

`integrity.py` does the checks (span-drop, ordering, calibration); this module only produces spans.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from opentelemetry import baggage, context, trace
from opentelemetry.propagate import inject, extract
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Span, SpanKind

from .clock import mono_ns, wall_ns

_lock = threading.Lock()
_state: dict[str, Any] = {"provider": None, "memory": None, "jsonl": [], "service": None}


def span_to_json(s: ReadableSpan) -> dict[str, Any]:
    ctx = s.get_span_context()
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(s.parent.span_id, "016x") if s.parent else None,
        "name": s.name,
        # a span may name its logical service (the mock world runs inside the harness process but is its own service)
        "service": (s.attributes or {}).get("mark.service") or (s.resource.attributes.get("service.name") if s.resource else None),
        "start_wall_ns": s.start_time,
        "end_wall_ns": s.end_time,
        "attributes": dict(s.attributes or {}),
        "events": [{"name": e.name, "wall_ns": e.timestamp, "attributes": dict(e.attributes or {})} for e in (s.events or [])],
        "status": str(s.status.status_code.name) if s.status else None,
    }


class MemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def as_json(self) -> list[dict[str, Any]]:
        with self._lock:
            return [span_to_json(s) for s in self.spans]

    def clear(self) -> None:
        with self._lock:
            self.spans.clear()


class JsonlExporter(SpanExporter):
    """One span per line, written synchronously (SimpleSpanProcessor) and flushed per export; the file is the
    trace archive. `dropped` counts spans that could not be written; the runner fails a run when it is non-zero
    (Task 4.1 exporter rule) and the count is written next to the archive (`<archive>.dropped`)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.dropped = 0
        self.exported = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with self._lock, self.path.open("a", encoding="utf-8") as f:
                for s in spans:
                    f.write(json.dumps(span_to_json(s), sort_keys=True, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.exported += len(spans)
            return SpanExportResult.SUCCESS
        except OSError:
            self.dropped += len(spans)
            try:
                self.path.with_suffix(self.path.suffix + ".dropped").write_text(str(self.dropped), encoding="utf-8")
            except OSError:
                pass
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self.path.with_suffix(self.path.suffix + ".dropped").write_text(str(self.dropped), encoding="utf-8")
        except OSError:
            pass

def init(service_name: str, *, jsonl_path: str | os.PathLike[str] | None = None, otlp_endpoint: str | None = None, memory: bool = True) -> TracerProvider:
    """Idempotent per process. Sampling is ALWAYS_ON by construction; there is no option to change it."""
    with _lock:
        jsonl = jsonl_path or os.environ.get("MARK_TRACE_JSONL")
        if _state["provider"] is not None:
            # Already initialised in this process (e.g. a test process that opens several runs): a new archive
            # path gets its own synchronous exporter rather than being silently ignored.
            if jsonl and str(Path(jsonl)) not in {str(j.path) for j in _state["jsonl"]}:
                je = JsonlExporter(Path(jsonl))
                sp = SimpleSpanProcessor(je)
                _state["provider"].add_span_processor(sp)
                _state.setdefault("processors", []).append(sp)   # ours: the instrument check must not call it foreign
                _state["jsonl"].append(je)
            return _state["provider"]
        provider = TracerProvider(sampler=ALWAYS_ON, resource=Resource.create({"service.name": service_name, "mark.pid": os.getpid()}))
        ours: list[Any] = []
        if memory:
            mem = MemoryExporter()
            sp = SimpleSpanProcessor(mem)
            provider.add_span_processor(sp)
            ours.append(sp)
            _state["memory"] = mem
        if jsonl:
            je = JsonlExporter(Path(jsonl))
            sp = SimpleSpanProcessor(je)
            provider.add_span_processor(sp)
            ours.append(sp)
            _state["jsonl"].append(je)
        endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            sp = SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"))
            provider.add_span_processor(sp)
            ours.append(sp)
        trace.set_tracer_provider(provider)
        _state["provider"] = provider
        _state["service"] = service_name
        _state["processors"] = ours
        return provider


# Third-party tracer SDKs a target may carry. "present" is an import (langchain always imports langsmith);
# "active" is the SDK switched on, which is what makes it a second instrument.
_FOREIGN_SDKS: dict[str, Any] = {
    "lmnr": lambda m: bool(getattr(getattr(m, "Laminar", None), "is_initialized", lambda: False)()),
    "langsmith": lambda m: str(os.environ.get("LANGSMITH_TRACING", os.environ.get("LANGCHAIN_TRACING_V2", ""))).lower() in ("1", "true", "yes", "on"),
    "sentry_sdk": lambda m: bool(getattr(m, "is_initialized", lambda: False)()),
    "logfire": lambda m: bool(getattr(getattr(m, "DEFAULT_LOGFIRE_INSTANCE", None), "config", None) and getattr(m.DEFAULT_LOGFIRE_INSTANCE.config, "send_to_logfire", False)),
    "traceloop.sdk": lambda m: True,        # importing it is initialising it (Traceloop.init is the import's purpose)
    "openinference.instrumentation": lambda m: True,   # instrumentors register on the global provider; caught below too
    "opentelemetry.exporter.otlp.proto.grpc": lambda m: True,   # the harness exports over HTTP only; a gRPC exporter is someone else's
}


def instrument_check() -> dict[str, Any]:
    """Single-instrument precondition (founder review 2026-09-12): the harness must be the only instrument that
    emitted spans. A target may carry its own exporter (the OpenHands SDK switched Laminar on when it saw the
    collector endpoint); the harness disables what it knows and records, per process, (1) whether the global
    tracer provider is still the harness's, (2) every span processor on it that the harness did not add, and
    (3) every known third-party tracer SDK that is present and whether it is active. ok = nothing foreign is live."""
    import sys

    provider = _state["provider"]
    out: dict[str, Any] = {"provider_is_ours": provider is not None and trace.get_tracer_provider() is provider, "foreign_processors": [], "sdks": {}, "ok": True}
    if provider is not None:
        try:
            procs = list(getattr(getattr(provider, "_active_span_processor", None), "_span_processors", ()) or ())
        except Exception:  # noqa: BLE001
            procs = []
        ours = {id(p) for p in _state.get("processors") or ()}
        for p in procs:
            if id(p) not in ours:
                exp = getattr(p, "span_exporter", None)
                out["foreign_processors"].append(f"{type(p).__module__}.{type(p).__name__}" + (f"({type(exp).__module__}.{type(exp).__name__})" if exp is not None else ""))
    for name, active in _FOREIGN_SDKS.items():
        m = sys.modules.get(name)
        if m is None:
            continue
        try:
            a = bool(active(m))
        except Exception:  # noqa: BLE001
            a = True   # cannot tell = treat as live (fail closed)
        out["sdks"][name] = "active" if a else "present"
    out["foreign_active"] = sorted(k for k, v in out["sdks"].items() if v == "active")
    out["ok"] = bool(out["provider_is_ours"] or provider is None) and not out["foreign_processors"] and not out["foreign_active"]
    return out


def memory_spans() -> list[dict[str, Any]]:
    mem = _state["memory"]
    return mem.as_json() if mem else []


def clear_memory() -> None:
    if _state["memory"]:
        _state["memory"].clear()


def force_flush() -> None:
    if _state["provider"]:
        _state["provider"].force_flush()
    for je in _state["jsonl"]:
        je.shutdown()


def dropped_spans() -> int:
    return sum(je.dropped for je in _state["jsonl"])


def tracer() -> trace.Tracer:
    if _state["provider"] is None:
        init(os.environ.get("MARK_SERVICE_NAME", "mark"))
    return trace.get_tracer("mark")


def _mark(span: Span, prefix: str) -> None:
    span.set_attribute(f"mark.{prefix}_mono_ns", mono_ns())
    span.set_attribute(f"mark.{prefix}_wall_ns", wall_ns())


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None, kind: SpanKind = SpanKind.INTERNAL) -> Iterator[Span]:
    with tracer().start_as_current_span(name, kind=kind, attributes=attributes or {}) as s:
        _mark(s, "start")
        try:
            yield s
        finally:
            _mark(s, "end")


def event(name: str, attributes: dict[str, Any] | None = None) -> dict[str, int]:
    """Add an event to the current span with both clocks; returns the stamp for the caller's record."""
    st = {"mono_ns": mono_ns(), "wall_ns": wall_ns()}
    cur = trace.get_current_span()
    cur.add_event(name, {**(attributes or {}), "mark.mono_ns": st["mono_ns"], "mark.wall_ns": st["wall_ns"]})
    return st


def scenario_context(scenario_id: str) -> None:
    """Attach the scenario id as baggage on the current context (propagates with `inject`)."""
    ctx = baggage.set_baggage("mark.scenario_id", scenario_id)
    context.attach(ctx)


def current_headers() -> dict[str, str]:
    h: dict[str, str] = {}
    inject(h)
    sid = baggage.get_baggage("mark.scenario_id")
    if sid:
        h["X-Scenario-Id"] = str(sid)
    return h


def env_for_child(scenario_id: str) -> dict[str, str]:
    """Environment that carries the current trace and scenario into a subprocess (the agent, a sub-agent)."""
    h = current_headers()
    env = {"MARK_SCENARIO_ID": scenario_id}
    if "traceparent" in h:
        env["OTEL_TRACEPARENT"] = h["traceparent"]
    if "baggage" in h:
        env["OTEL_BAGGAGE"] = h["baggage"]
    return env


def attach_from_env() -> str | None:
    """In a child process: continue the parent's trace from OTEL_TRACEPARENT / OTEL_BAGGAGE. Returns the scenario id."""
    carrier: dict[str, str] = {}
    if os.environ.get("OTEL_TRACEPARENT"):
        carrier["traceparent"] = os.environ["OTEL_TRACEPARENT"]
    if os.environ.get("OTEL_BAGGAGE"):
        carrier["baggage"] = os.environ["OTEL_BAGGAGE"]
    if carrier:
        context.attach(extract(carrier))
    sid = os.environ.get("MARK_SCENARIO_ID") or baggage.get_baggage("mark.scenario_id")
    if sid:
        scenario_context(str(sid))
    return str(sid) if sid else None


@contextmanager
def span_from_headers(headers: dict[str, str], name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span]:
    """Server side: a span that continues the caller's trace (mock world, control channel)."""
    ctx = extract({k.lower(): v for k, v in headers.items()})
    token = context.attach(ctx)
    try:
        with tracer().start_as_current_span(name, kind=SpanKind.SERVER, attributes=attributes or {}) as s:
            _mark(s, "start")
            try:
                yield s
            finally:
                _mark(s, "end")
    finally:
        context.detach(token)
