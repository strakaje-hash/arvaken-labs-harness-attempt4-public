"""Serving parameters, pinned with every run (founder ruling 2026-09-12).

Everything on the model's side of the harness that shapes what the model outputs is recorded in the environment and
pinned in the manifest: the context length, the tool-call parser, the seed, the other `vllm serve` arguments, and
what each target asks for per request (temperature, seed). Decisive attempt 2 was served with max_model_len 8192
and the hermes parser, and neither was recorded anywhere. On the single-call variant the OpenHands conversation
reached that window in most none replications, and no manifest could have told a reader why. This is the model-pin
custody finding again, one layer up.

The record is taken from three independent sources and all three are kept:
- the arguments run.sh declares (MARK_SERVING_ARGS);
- the arguments the `vllm serve` process actually runs with, read from the process table;
- what the live server reports (GET /v1/models: max_model_len; GET /version).
A disagreement between them is recorded, never resolved silently: a server started earlier by another run.sh can run
with arguments the environment no longer declares.

The serving condition (founder ruling 2026-09-12, after the Q3 replays). Temperature 0 and seed 7 pin nothing unless the
condition is pinned too: on vLLM 0.29.0, `max_num_seqs` alone changed the token path for 3 of 10 captured prompts at
concurrency 1, and batch composition for 2 of 10. The flags as declared cannot say what applied when a default did, so
the effective values come from the engine's own startup log (MARK_SERVING_LOG): non-default args, the engine config
line (prefix caching, eager mode, seed, CUDA-graph mode and capture sizes) and the scheduler line (chunked prefill,
max_num_batched_tokens). A value the server did not state is recorded as not stated, never filled in. The concurrency
actually in effect comes from the server's /metrics during the run (ServingSampler)."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
import threading
from pathlib import Path
from typing import Any

from mark_ledger.canonical import object_hash

# One source for what each target asks of the model per request. The targets read it; the environment record pins it.
REQUEST_PARAMS: dict[str, dict[str, Any]] = {
    "langgraph-ref": {"temperature": 0, "seed": 7, "timeout_s": 120, "max_retries": 1},
    "openhands-sdk": {"temperature": 0.0, "seed": None},
}

_FLAGS: dict[str, tuple[str, type]] = {
    "--max-model-len": ("max_model_len", int), "--tool-call-parser": ("tool_call_parser", str), "--seed": ("seed", int),
    "--gpu-memory-utilization": ("gpu_memory_utilization", float), "--served-model-name": ("served_model_name", str),
    "--dtype": ("dtype", str), "--quantization": ("quantization", str), "--max-num-seqs": ("max_num_seqs", int),
    "--generation-config": ("generation_config", str), "--chat-template": ("chat_template", str),
}
_SWITCHES: dict[str, tuple[str, bool]] = {"--enable-auto-tool-choice": ("enable_auto_tool_choice", True), "--enforce-eager": ("enforce_eager", True),
                                          "--enable-prefix-caching": ("enable_prefix_caching", True), "--no-enable-prefix-caching": ("enable_prefix_caching", False)}


def parse_serve_args(args: str | list[str]) -> dict[str, Any]:
    tokens = shlex.split(args) if isinstance(args, str) else list(args)
    out: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        key, sep, inline = tokens[i].partition("=")
        if key in _FLAGS:
            name, cast = _FLAGS[key]
            if sep:
                val = inline
            else:
                val = tokens[i + 1] if i + 1 < len(tokens) else None
                i += 1
            try:
                out[name] = cast(val)
            except (TypeError, ValueError):
                out[name] = val
        elif key in _SWITCHES:
            name, value = _SWITCHES[key]
            out[name] = value
        i += 1
    return out


# ---------------------------------------------------------------- the effective serving condition, from the engine log
CONDITION_FIELDS = ("engine_version", "max_num_seqs", "enable_prefix_caching", "chunked_prefill", "max_num_batched_tokens", "enforce_eager",
                    "cudagraph_mode", "cudagraph_capture_sizes", "seed")
_NONDEFAULT = re.compile(r"non-default args: (\{.*\})\s*$", re.M)
_ENGINE = re.compile(r"Initializing a V1 LLM engine \(v([^)]+)\) with config: (.*)$", re.M)
_CHUNKED = re.compile(r"Chunked prefill is enabled with max_num_batched_tokens=(\d+)")


def engine_log_facts(text: str) -> dict[str, Any]:
    """What the server itself stated at its most recent start in this log. Never raises; a field the log does not state
    is None (and `max_num_seqs_source` says so for max_num_seqs, whose default the log never prints)."""
    starts = [m.start() for m in _NONDEFAULT.finditer(text or "")]
    if not starts:
        return {"read": False, "reason": "no server start in the engine log (no 'non-default args' line)"}
    last = text[starts[-1]:]
    facts: dict[str, Any] = {"read": True, "server_starts_in_log": len(starts)}
    m = _NONDEFAULT.search(last)
    try:
        nd = ast.literal_eval(m.group(1)) if m else None
    except (ValueError, SyntaxError):
        nd = None
    facts["non_default_args"] = nd if isinstance(nd, dict) else None
    e = _ENGINE.search(last)
    cfg = e.group(2) if e else ""
    facts["engine_version"] = e.group(1) if e else None

    def flag(name: str) -> bool | None:
        mm = re.search(rf"\b{name}=(True|False)\b", cfg)
        return (mm.group(1) == "True") if mm else None

    facts["enable_prefix_caching"] = flag("enable_prefix_caching")
    facts["enforce_eager"] = flag("enforce_eager")
    sm = re.search(r"\bseed=(\d+)\b", cfg)
    facts["seed"] = int(sm.group(1)) if sm else None
    cm = re.search(r"'cudagraph_mode': <CUDAGraphMode\.([A-Z_]+)", cfg)
    facts["cudagraph_mode"] = cm.group(1) if cm else None
    cs = re.search(r"'cudagraph_capture_sizes': \[([0-9, ]*)\]", cfg)
    facts["cudagraph_capture_sizes"] = [int(x) for x in cs.group(1).split(",") if x.strip()] if cs else None
    ch = _CHUNKED.search(last)
    facts["chunked_prefill"] = True if ch else None
    facts["max_num_batched_tokens"] = int(ch.group(1)) if ch else None
    if isinstance(nd, dict) and nd.get("max_num_seqs") is not None:
        facts["max_num_seqs"], facts["max_num_seqs_source"] = int(nd["max_num_seqs"]), "non-default args"
    else:
        mm = re.search(r"\bmax_num_seqs=(\d+)\b", cfg)
        facts["max_num_seqs"] = int(mm.group(1)) if mm else None
        facts["max_num_seqs_source"] = "engine config" if mm else "not stated by the server (a vLLM default applied); pass --max-num-seqs so the log states it"
    return facts


def read_engine_log(path: str | None) -> dict[str, Any]:
    if not path:
        return {"read": False, "reason": "MARK_SERVING_LOG is not set"}
    p = Path(path)
    if not p.is_file():
        return {"read": False, "reason": f"engine log {path} not found", "path": path}
    data = p.read_bytes()
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), **engine_log_facts(data.decode(errors="replace"))}


def serving_condition(rec: dict[str, Any] | None) -> dict[str, Any]:
    """The effective serving condition a replay or live regeneration must match. `stated` is True only when the server
    stated every field; `missing` names the rest."""
    el = (rec or {}).get("engine_log") or {}
    if not el.get("read"):
        return {"stated": False, "reason": el.get("reason") or "no engine log in the serving record", "missing": list(CONDITION_FIELDS), "hash": None}
    cond = {k: el.get(k) for k in CONDITION_FIELDS}
    missing = [k for k in CONDITION_FIELDS if cond[k] is None]
    return {"stated": not missing, "missing": missing, **cond, "max_num_seqs_source": el.get("max_num_seqs_source"), "hash": object_hash(cond)}


class ServingSampler:
    """The concurrency actually in effect at the model server: vllm:num_requests_running from /metrics, sampled while the
    run is open. A failed sample is counted, never guessed. The gauge's name is an assumption about the engine version
    until a live server shows it (founder, 2026-09-12), so every request gauge the server emits is recorded by name, and
    a response without this one counts as `metric_absent`, not as zero."""

    METRIC = "vllm:num_requests_running"
    _GAUGE_FAMILY = "vllm:num_requests"

    def __init__(self, llm_url: str, interval_s: float = 0.2, gate: Any = None) -> None:
        # C2: every sample is a declared call through the harness's gate (vllm.metrics, counted); a sampler built without one
        # makes the same declared call through a gate with no log (tools outside a run)
        from .permitted_calls import CallGate, load_declaration

        self.gate = gate if gate is not None else CallGate(load_declaration())
        base = llm_url.rstrip("/")
        self.root = base[: -len("/v1")] if base.endswith("/v1") else base
        self.interval_s = interval_s
        self.samples = 0
        self.failures = 0
        self.metric_absent = 0
        self.names_seen: set[str] = set()
        self.counts: dict[int, int] = {}
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, name="serving-sampler", daemon=True)

    def _read(self) -> float | None:
        r = self.gate.call("vllm.metrics", "GET", self.root + "/metrics", timeout_s=2.0)
        if r.status != 200:
            return None
        total = None
        for line in r.text().splitlines():
            if line.startswith(self._GAUGE_FAMILY):
                self.names_seen.add(re.split(r"[{\s]", line, maxsplit=1)[0])
            if line.startswith(self.METRIC + "{") or line.startswith(self.METRIC + " "):
                try:
                    total = (total or 0.0) + float(line.rsplit(" ", 1)[1])
                except ValueError:
                    pass
        if total is None:
            self.metric_absent += 1
        return total

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                v = self._read()
            except Exception:  # noqa: BLE001
                v = None
            if v is None:
                self.failures += 1
            else:
                n = int(round(v))
                self.samples += 1
                self.counts[n] = self.counts.get(n, 0) + 1
            self._stop.wait(self.interval_s)

    def start(self) -> "ServingSampler":
        self._t.start()
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._t.join(timeout=5)
        return self.summary()

    def summary(self) -> dict[str, Any]:
        busy = {k: v for k, v in self.counts.items() if k > 0}
        return {"metric": self.METRIC, "interval_s": self.interval_s, "observed": self.samples > 0, "samples": self.samples, "failed_samples": self.failures,
                "metric_absent_samples": self.metric_absent, "request_gauges_seen": sorted(self.names_seen),
                "max_running": max(self.counts) if self.counts else None,
                "mean_running_while_busy": (sum(k * v for k, v in busy.items()) / sum(busy.values())) if busy else None,
                "samples_by_running": {str(k): v for k, v in sorted(self.counts.items())}}


def serve_cmdlines(proc_root: str | Path = "/proc") -> list[list[str]]:
    """The argument vectors of every running `vllm serve` process, from the process table (not from anything the
    harness wrote). Empty where there is no /proc (the laptop)."""
    root = Path(proc_root)
    if not root.is_dir():
        return []
    found: list[list[str]] = []
    for d in root.iterdir():
        if not d.name.isdigit():
            continue
        try:
            parts = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [p.decode(errors="replace") for p in parts if p]
        if any(a == "vllm" or a.endswith("/vllm") for a in args) and "serve" in args and args not in found:
            found.append(args)
    return found


def live_server_facts(llm_url: str, timeout_s: float = 3.0, gate: Any = None) -> dict[str, Any]:
    # C2: two declared calls through the harness's gate (vllm.models, vllm.version)
    import json as _json

    from .permitted_calls import CallGate, load_declaration

    gate = gate if gate is not None else CallGate(load_declaration())
    base = llm_url.rstrip("/")
    facts: dict[str, Any] = {"reachable": False}
    r = gate.call("vllm.models", "GET", base + "/models", timeout_s=timeout_s)
    try:
        if not r.ok:
            raise RuntimeError(r.error or f"status {r.status}")
        facts["models"] = [{"id": m.get("id"), "root": m.get("root"), "max_model_len": m.get("max_model_len")} for m in (_json.loads(r.text()).get("data") or [])]
        facts["reachable"] = True
    except Exception as e:  # noqa: BLE001
        facts["error"] = f"{type(e).__name__}: {e}"[:200]
        return facts
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    v = gate.call("vllm.version", "GET", root + "/version", timeout_s=timeout_s)
    try:
        if v.status == 200:
            facts["version"] = _json.loads(v.text()).get("version")
    except Exception:  # noqa: BLE001
        pass
    return facts


def serving_record(llm_url: str, llm_model: str, gate: Any = None) -> dict[str, Any]:
    declared_args = os.environ.get("MARK_SERVING_ARGS")
    declared = parse_serve_args(declared_args) if declared_args else None
    running = serve_cmdlines()
    process = parse_serve_args(running[0][running[0].index("serve") + 1:]) if running else None
    live = live_server_facts(llm_url, gate=gate)
    served = next((m for m in live.get("models", []) if m.get("id") == llm_model), None)
    sources = {k: v for k, v in {"declared": (declared or {}).get("max_model_len"), "process": (process or {}).get("max_model_len"),
                                  "live": (served or {}).get("max_model_len")}.items() if v is not None}
    return {"engine": "vllm", "llm_url": llm_url, "model": llm_model, "declared_args": declared_args, "declared": declared,
            "process_args": running[0] if running else None, "process_count": len(running), "process": process, "live": live,
            "max_model_len_sources": sources, "consistent": len(set(sources.values())) <= 1, "request_params": REQUEST_PARAMS,
            "engine_log": read_engine_log(os.environ.get("MARK_SERVING_LOG"))}


def serving_pin(rec: dict[str, Any] | None) -> dict[str, Any]:
    if not rec:
        return {"recorded": False, "reason": "no serving record in this run's environment"}
    params = rec.get("process") or rec.get("declared") or {}
    sources = rec.get("max_model_len_sources") or {}
    max_len = sources.get("live") or sources.get("process") or sources.get("declared")
    return {"recorded": max_len is not None, "engine": rec.get("engine"), "version": (rec.get("live") or {}).get("version"), "max_model_len": max_len,
            "max_model_len_sources": sources, "max_model_len_consistent": rec.get("consistent"), "tool_call_parser": params.get("tool_call_parser"),
            "enable_auto_tool_choice": params.get("enable_auto_tool_choice"), "seed": params.get("seed"),
            "args_hash": object_hash({"process": rec.get("process_args"), "declared": rec.get("declared_args")}),
            "request_params_hash": object_hash(rec.get("request_params") or {}),
            "condition": serving_condition(rec)}
