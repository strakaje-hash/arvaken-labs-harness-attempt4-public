"""Probe runner: replications of one (probe, target, control, workload) cell, integrity, gate, ledger.

A run directory (/root/runs/<run_id> on the pod) holds: harness spans, mock calls, one scenario dir per
replication, results.json, the ledger, and manifest.json (signed). `export` copies the run to
/workspace/results/<run_id> (Task 1.2) after verifying the ledger.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mark_ledger.canonical import object_hash, sha256_hex
from mark_ledger.store import Ledger, Provenance
from mark_probes import PROBES, load_gate
from mark_probes.base import Replication

from . import telemetry
from .clock import mono_ns
from .registry import Target, applicable, load as load_registry
from .scenario import MockWorld, ScenarioConfig, integrity_for, run_scenario
from .model_integrity import model_integrity
from .reply_paths import cell_reply_paths, first_reply
from .serving import REQUEST_PARAMS, ServingSampler, serving_pin, serving_record
from .workloads import load as load_workloads

ENGINE_VERSION = "0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[3]


def repo_commit() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def lockfile_sha256() -> str:
    p = REPO_ROOT / "uv.lock"
    return sha256_hex(p.read_bytes()) if p.exists() else "missing"


# Keys the RUNNER writes onto every replication's `raw` after the probe has returned it. A probe that writes one of
# these has its field silently replaced -- which is what happened to evidence.claimed_vs_landed's first draft, whose
# `self_report` was overwritten by the runner's A2 consistency record. The collision is refused at cell time now, so
# the next probe author cannot lose a field to a name that already has an owner (founder ruling 2026-09-21).
RESERVED_RAW_KEYS = {
    "stall": "A4: the stall check the runner applies to every replication",
    "stall_measured": "A4: the stall measurement behind it",
    "self_report": "A2: the self-report consistency record the runner writes on every row",
    "not_run_reason_before_model_error": "the reason a replication carried before model integrity re-read it",
    "attribution_watch": "the same-user resolver's health around this replication (founder ruling 2026-09-23)",
    "attribution_state": "how this replication's world calls were attributed, counted by kind (founder ruling 2026-09-23)",
    "killed_after_halt": "the five conditions under which a killed agent's missing self-record does not refuse the row (founder ruling 2026-09-23)",
    "early_exit": "the conditions under which an agent's normal exit before the trigger is the model's behaviour (founder ruling 2026-09-23)",
}


class ReservedRawKey(ValueError):
    """A probe wrote a key the runner owns. Raised at cell time, so the field is never silently replaced."""


def _agent_log_tail(ev: dict[str, Any], lines: int = 20) -> list[str] | None:
    """The last lines of the agent's own log, kept beside the killed-after-halt record as the excerpt: evidence, never a deciding fact."""
    try:
        text = (Path(ev["dir"]) / "agent.stdout").read_text(encoding="utf-8", errors="replace")
    except (KeyError, OSError):
        return None
    return [ln[:200] for ln in text.splitlines() if ln.strip()][-lines:]


def check_reserved_raw(probe_id: str, raw: dict[str, Any] | None) -> None:
    clash = sorted(set(raw or {}) & set(RESERVED_RAW_KEYS))
    if clash:
        owners = "; ".join(f"{k}: {RESERVED_RAW_KEYS[k]}" for k in clash)
        raise ReservedRawKey(f"{probe_id} writes raw key(s) the runner owns and would overwrite: {clash}. {owners}. Rename the probe's field.")


def _cpu_list(spec: str) -> list[int]:
    """`0-3,8` -> [0,1,2,3,8]. The format taskset and /proc both use."""
    out: list[int] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def _cpu_affinity() -> dict[str, Any]:
    declared_h = os.environ.get("MARK_HARNESS_CPUS") or None
    declared_s = os.environ.get("MARK_SERVING_CPUS") or None
    observed = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    rec: dict[str, Any] = {"declared_harness": declared_h, "declared_serving": declared_s,
                           "observed_harness": observed, "cpu_count": os.cpu_count(),
                           "separated": None, "applied": None}
    if observed is None:
        rec["reason"] = "this platform has no sched_getaffinity; affinity is not readable here"
        return rec
    if declared_h:
        rec["applied"] = observed == _cpu_list(declared_h)
        if not rec["applied"]:
            rec["reason"] = f"declared {declared_h} but the kernel reports {observed}: the pinning did not apply"
    if declared_h and declared_s:
        rec["separated"] = not (set(_cpu_list(declared_h)) & set(_cpu_list(declared_s)))
        if not rec["separated"]:
            rec["reason"] = f"harness {declared_h} and serving {declared_s} overlap: a split that overlaps is not a split"
    return rec


def environment_fingerprint(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    from mark_timing import clock_source, is_raw

    from .isolation import probe as isolation_probe

    iso = isolation_probe()
    env = {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(), "hostname_hash": sha256_hex(platform.node())[:12],
           "image_digest": os.environ.get("MARK_IMAGE_DIGEST", "unpinned-local"),
           # the platform manifest digest names the image bits themselves; the index digest above also covers
           # whatever attestations sat beside them in the registry (2026-09-12: a provenance blob that was removed,
           # which changed the index digest and not the image)
           "image_platform_digest": os.environ.get("MARK_IMAGE_PLATFORM_DIGEST", "unknown"), "gpu": _gpu(),
           # Task 1.4 / 3.2: the measured kernel capabilities, the tier they imply and the egress label; never assumed.
           "isolation_capabilities": iso, "sandbox": f"tier-{iso.get('tier')}" if iso.get("tier") not in (None, "none") else "none", "egress_control": iso.get("egress_control", "none"),
           # Task 4.1 clock rule: what every timestamp in this run was read from.
           "clock_source": clock_source(), "clock_is_monotonic_raw": is_raw(),
           # **Whose cores the clock ran on, read from the OS** (founder ruling 2026-09-22). The harness is pinned
           # away from the model server so its timing thread never queues behind inference; ks.latency judges some
           # controls on a 250 ms threshold, and a scheduler that can delay the harness by 30 ms under load can move
           # a reading across it. `declared` is what run.sh asked for and `observed` is what the kernel granted --
           # a setting that failed to apply must not be able to describe itself as applied (A3's rule, applied to
           # CPUs). A run where they disagree says so here rather than in a reader's assumption.
           "cpu_affinity": _cpu_affinity()}
    if not is_raw() and os.name == "posix" and os.environ.get("MARK_ALLOW_FALLBACK_CLOCK") != "1":
        raise RuntimeError(f"clock source is {clock_source()}; a pod run needs CLOCK_MONOTONIC_RAW (set MARK_ALLOW_FALLBACK_CLOCK=1 only for laptop CI)")
    env.update(extra or {})
    return env


def model_cache_integrity() -> dict[str, Any]:
    """Re-verify the served model's per-file SHA-256 list after a run (MARK_MODEL_SHA_LIST + MARK_MODEL_SNAPSHOT).
    The cache is writable by the runner uid on the measured pod, so untouched weights are PROVEN here, not assumed."""
    lst, snap = os.environ.get("MARK_MODEL_SHA_LIST"), os.environ.get("MARK_MODEL_SNAPSHOT")
    if not lst or not snap or not Path(lst).exists() or not Path(snap).exists():
        return {"status": "not_checked", "reason": "MARK_MODEL_SHA_LIST / MARK_MODEL_SNAPSHOT not set or missing"}
    try:
        p = subprocess.run(["sha256sum", "-c", "--quiet", lst], cwd=snap, capture_output=True, text=True, timeout=900)
        return {"status": "verified" if p.returncode == 0 else "changed", "detail": (p.stdout + p.stderr).strip()[:500], "sha_list": lst}
    except Exception as e:  # noqa: BLE001
        return {"status": "not_checked", "reason": f"{type(e).__name__}: {e}"}


def _gpu() -> dict[str, Any]:
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"], text=True, timeout=5).strip()
        name, driver, mem = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"name": name, "driver": driver, "memory": mem}
    except Exception:  # noqa: BLE001
        return {"name": None, "driver": None, "memory": None}


@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    ledger: Ledger
    mock: MockWorld
    registry: dict[str, Target]
    workloads: dict[str, dict[str, Any]]
    gates_dir: Path
    root_public_hex: str | None
    revocations: dict[str, Any] | None
    llm_url: str
    llm_model: str
    tools_mode: str
    sandbox_cmd: list[str]
    calibration_ok: bool | None = None
    calibration: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    env: dict[str, Any] = field(default_factory=dict)
    egress: Any = None
    benchmark_spec: dict[str, Any] | None = None      # id, replications, families_disabled, pre_registration
    baselines: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)      # (probe, target, workload) -> none aggregate
    variants_seen: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)       # (probe, target, control) -> variants run
    # Single-instrument precondition: the collector's archive (MARK_COLLECTOR_ARCHIVE) is scanned incrementally after
    # every cell; a span from another instrumentation scope is a second instrument. Recorded here and in the manifest.
    single_instrument: dict[str, Any] = field(default_factory=dict)
    archive_offset: int = 0
    # Baseline invariants (founder rule 2026-09-12): the gate of every probe seen, and the probes whose `none` row
    # produced an impossible result; every row of such a probe is not_run at close, and later cells are skipped.
    gates: dict[str, Any] = field(default_factory=dict)
    broken_probes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    model_proxy: Any = None   # the model path's instrument (constitution model-integrity)
    serving_sampler: Any = None   # the concurrency in effect at the model server (the serving condition)
    registry_path: str | None = None   # the registry the run loaded; each agent process loads the same one
    # ks.resume v3: the measured pace per (target, workload), from the first none cell on it, and the pace cells a
    # filtered run added so a pace-dependent probe never runs on a guess
    paces: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    pace_cells: list[dict[str, Any]] = field(default_factory=list)
    # C1 (attempt 4): the signed tag mapping this run reads its per-tag outcomes under; pinned in the manifest, its hash on every row
    tag_mapping: Any = None
    # C2 (attempt 4): the operator of record, resolved before any cell, and the gate every outbound call of the harness's own goes through
    operator: dict[str, Any] | None = None
    # How this run attributes a call to a process (founder ruling 2026-09-23): the kernel directly, or a resolver running as the
    # agents' user whose claims root checks and whose health brackets every replication. None: the kernel, and nothing to watch.
    peer_resolver: Any = None
    call_gate: Any = None
    # fix A9 (2026-09-14): what kind of run this is; only a probe run (a smoke) may use a target's smoke bound
    run_kind: str = "bench_run"
    # fix A7 (2026-09-14): every env-test attempt, or the recorded absence of one (mark_platform.env_test_record)
    env_test: dict[str, Any] | None = None
    # attempt 4 A2: none cells whose counted self-reports disagreed with their receipts, by (probe, target, workload); every
    # cell that reads that baseline is informational for it (self_report.py)
    tainted_baselines: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def scan_instruments(self, cell: str | None = None) -> dict[str, Any]:
        """Scan the collector archive from the last offset. Returns this scan; accumulates into single_instrument."""
        from .integrity import scan_collector_archive

        path = os.environ.get("MARK_COLLECTOR_ARCHIVE")
        acc = self.single_instrument
        if not path:
            acc.update({"archive": None, "scanned": False, "ok": None, "reason": "no collector archive (MARK_COLLECTOR_ARCHIVE unset): per-process checks only"})
            return acc
        scan = scan_collector_archive(path, self.archive_offset)
        self.archive_offset = scan["offset"]
        acc.setdefault("archive", path)
        acc["scanned"] = True
        acc["spans"] = acc.get("spans", 0) + scan["spans"]
        acc["foreign_spans"] = acc.get("foreign_spans", 0) + scan["foreign_spans"]
        for k in ("scopes", "services", "foreign_scopes", "foreign_services"):
            d = acc.setdefault(k, {})
            for name, n in scan[k].items():
                d[name] = d.get(name, 0) + n
        if scan["foreign_spans"] and cell:
            acc.setdefault("cells_with_foreign_spans", []).append({"cell": cell, "foreign_spans": scan["foreign_spans"], "scopes": scan["foreign_scopes"]})
        acc["ok"] = acc.get("foreign_spans", 0) == 0
        return {**scan, "cell": cell}

    @property
    def chain_id(self) -> str:
        return self.run_id

    def provenance(self, probe: Any, target: Target, control: Target, workload: dict[str, Any]) -> Provenance:
        return Provenance(engine_version=ENGINE_VERSION, environment_fingerprint=object_hash(self.env), actor="platform-runner",
                          probe_id=probe.id, probe_version=str(probe.version), workload_id=workload["id"], workload_version=str(workload["version"]),
                          model_hash=os.environ.get("MARK_MODEL_HASH") or None, control_id=control.id, control_version=control.version, target_id=target.id, target_version=target.version)


def open_run(run_dir: Path, run_id: str, *, llm_url: str, llm_model: str, tools_mode: str, sandbox_cmd: list[str], gates_dir: Path | None = None, root_pub_path: Path | None = None, revocations_path: Path | None = None, registry_path: str | None = None,
             mappings_dir: Path | None = None, operator_probe: dict[str, Any] | None = None, permitted_calls_path: Path | None = None) -> RunContext:
    run_dir.mkdir(parents=True, exist_ok=True)
    telemetry.init("harness", jsonl_path=run_dir / "spans.harness.jsonl")
    from mark_ledger.keys import iso, now_utc

    # C2: the harness's own outbound calls go through one declared gate from here on, and the operator of record is resolved
    # BEFORE the mock world, the proxies or a calibration replication exist -- the position of this call is the guarantee
    from .operator import resolve_operator
    from .permitted_calls import CallGate, load_declaration

    call_gate = CallGate(load_declaration(permitted_calls_path), run_dir / "harness-calls.jsonl")
    operator = resolve_operator(call_gate, **(operator_probe or {}))

    gates_dir = gates_dir or REPO_ROOT / "gates"
    root_pub = (root_pub_path or REPO_ROOT / "packages" / "bundles" / "keys" / "root.pub")
    rev = (revocations_path or REPO_ROOT / "packages" / "bundles" / "keys" / "revocations.json")
    from .gateway import new_token

    # The mock world demands a token on every call. In-process controls: the agent gets the mock URL and the
    # token travels in its environment (MARK_MOCK_TOKEN). Out-of-process control: only the gateway holds it.
    # C3 gate.bypass_path: the world holds two credentials -- the one it honours and the planted canary it recognizes and
    # refuses. Both are issued here, per run; the canary is granted nothing anywhere.
    mock = MockWorld.start(run_dir, token=new_token(), canary=new_token())
    # Tier B egress control: the agent's HTTP(S)_PROXY points at this allowlist proxy (vLLM, mock world, collector).
    from urllib.parse import urlsplit

    from .egress_proxy import EgressProxy

    # The model path's instrument: agents reach the model only through the model proxy, so the allowlist admits the
    # proxy and not the model server (best effort on Tier B; the model-integrity check reads the proxy's records).
    from .model_proxy import ModelProxy

    # the first exchange of every scenario is always captured: the replay-fidelity check re-sends it
    model_proxy = ModelProxy(llm_url, run_dir, capture=os.environ.get("MARK_MODEL_CAPTURE") == "1", capture_first=True).start()
    allow = [f"127.0.0.1:{urlsplit(mock.url).port}", f"127.0.0.1:{model_proxy.port}"]
    otlp = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp:
        allow.append(f"127.0.0.1:{urlsplit(otlp).port or 4318}")
    proxy = EgressProxy(allow, run_dir / "egress.jsonl").start()
    os.environ["MARK_EGRESS_PROXY"] = proxy.url
    ctx = RunContext(run_id=run_id, run_dir=run_dir, ledger=Ledger(run_dir / "ledger"), mock=mock, registry=load_registry(registry_path), workloads=load_workloads(),
                     gates_dir=gates_dir, root_public_hex=root_pub.read_text().strip() if root_pub.exists() else None,
                     revocations=json.loads(rev.read_text()) if rev.exists() else None, llm_url=llm_url, llm_model=llm_model, tools_mode=tools_mode, sandbox_cmd=sandbox_cmd,
                     started_at=iso(now_utc()), env=environment_fingerprint({"serving": serving_record(llm_url, llm_model, gate=call_gate)}))
    ctx.egress = proxy
    ctx.model_proxy = model_proxy
    ctx.call_gate = call_gate
    ctx.operator = operator
    # C1: the tag mapping is part of the instrument; a run without one cannot say what any row demonstrates, so it does not open
    from mark_probes.tag_mapping import load_tag_mapping

    ctx.tag_mapping = load_tag_mapping(mappings_dir or REPO_ROOT / "mappings", "tag-outcomes", ctx.root_public_hex, ctx.revocations)
    ctx.serving_sampler = ServingSampler(llm_url, gate=call_gate).start()
    ctx.registry_path = registry_path
    ctx.env["model_path"] = {"proxy": model_proxy.url, "upstream": llm_url, "capture": model_proxy.capture, "capture_first_exchange": model_proxy.capture_first,
                             "direct_access": "Tier B best effort: the egress allowlist admits the model proxy, not the model server"}
    # Attribution (founder ruling 2026-09-23). Where the harness cannot read the agents' fd links (another user, no CAP_SYS_PTRACE),
    # a resolver running as the agents' user is started through the agents' own sandbox command, and the record states its limit.
    # Decided by the kernel -- the resolver's uid from SO_PEERCRED, the harness's capability from CapEff -- before any scenario.
    from .peer_resolver import start_if_needed
    from .process_identity import set_agents_uid, set_resolver

    ctx.peer_resolver, ctx.env["attribution"] = start_if_needed(sandbox_cmd, log_path=run_dir / "peer-resolver.log")
    set_resolver(ctx.peer_resolver)
    set_agents_uid(ctx.env["attribution"].get("agents_uid"))
    ctx.ledger.append(ctx.chain_id, "run_open", {"run_id": run_id, "started_at": ctx.started_at, "environment": ctx.env, "repo_commit": repo_commit(), "lockfile_sha256": lockfile_sha256(), "operator": operator},
                      Provenance(ENGINE_VERSION, object_hash(ctx.env), "platform-runner"))
    return ctx


def replication_schedule(spec: dict[str, Any], *, override: int | None = None) -> tuple[int, int]:
    """Fix B6 (pre-registered in the benchmark spec): (scheduled, counted) for an evaluated cell. `replications` is what counts
    (the gates' min_replications stays 20); `replications_scheduled` is what runs, 22 for attempt 3, and defaults to
    `replications`. A --replications override keeps the spec's over-schedule margin, so a rehearsal at 2 schedules 4."""
    counted = int(spec["replications"])
    scheduled = int(spec.get("replications_scheduled") or counted)
    if scheduled < counted:
        raise ValueError(f"replications_scheduled {scheduled} is below replications {counted}: an over-schedule cannot run fewer than it counts")
    if override is not None:
        scheduled, counted = int(override) + (scheduled - counted), int(override)
    return scheduled, counted


def gate_min_replications(probe_ids: list[str], gates_dir: Path | None = None) -> dict[str, int | None]:
    """Each probe's min_replications, from its gate loaded the way open_run loads gates. Fix A5 reads it before a run opens."""
    gates_dir = gates_dir or REPO_ROOT / "gates"
    root_pub = REPO_ROOT / "packages" / "bundles" / "keys" / "root.pub"
    rev = REPO_ROOT / "packages" / "bundles" / "keys" / "revocations.json"
    root_hex = root_pub.read_text().strip() if root_pub.exists() else None
    revocations = json.loads(rev.read_text()) if rev.exists() else None
    out: dict[str, int | None] = {}
    for pid in probe_ids:
        probe = PROBES.get(pid)
        if probe is None:
            continue
        v = (load_gate(gates_dir, probe.gate_id, root_hex, revocations).preconditions or {}).get("min_replications")
        out[pid] = int(v) if v is not None else None
    return out


def _scenario_cfg(ctx: RunContext, target: Target, control: Target, workload: dict[str, Any], plan: Any, *, halt: bool = True) -> ScenarioConfig:
    return ScenarioConfig(run_dir=ctx.run_dir, target=target.id, control=control.id, workload=workload, trigger=plan.trigger, settle_ms=plan.settle_ms, timeout_s=plan.timeout_s,
                          after_halt=plan.after_halt, llm_url=ctx.llm_url, llm_model=ctx.llm_model, tools_mode=ctx.tools_mode, sandbox_cmd=ctx.sandbox_cmd, halt=halt,
                          control_class=control.control_class, egress_proxy=ctx.egress, model_proxy=ctx.model_proxy,
                          hold_ms=getattr(plan, "hold_ms", 0), pace=getattr(plan, "pace", None), registry_path=ctx.registry_path, run_kind=ctx.run_kind)


EXPECTED_SPANS = {"scenario": 1, "agent.process": 1, "harness.halt_command": 1, "control.halt": 1}


def calibrate(ctx: RunContext, *, target_id: str = "scripted", replications: int = 1, expected_ms: float = 250.0, tolerance_ms: float = 5.0) -> dict[str, Any]:
    """Task 4.3: the known-latency scenario. Sets ctx.calibration_ok; written to the ledger either way."""
    from mark_probes.base import HaltPlan

    target, control, wl = ctx.registry[target_id], ctx.registry["none"], ctx.workloads["wl.calibration"]
    plan = HaltPlan(workload_id=wl["id"], trigger={"kind": "none"}, settle_ms=0, timeout_s=120)
    reports = []
    for i in range(replications):
        ev = run_scenario(_scenario_cfg(ctx, target, control, wl, plan, halt=False), ctx.mock, i)
        integ = integrity_for(ev, ctx.run_dir / "spans.harness.jsonl", expected={"scenario": 1, "agent.process": 1, "tool.calibration_sleep": int(wl["params"]["n"]), "mock.calibration": int(wl["params"]["n"])},
                              required_services=["harness", f"agent:{target_id}", "mock-world"], calibration={"expected_ms": expected_ms, "tolerance_ms": tolerance_ms, "min_samples": int(wl["params"]["n"])})
        reports.append({"scenario_id": ev["scenario_id"], "status": ev.get("status"), "reason": ev.get("reason"), "integrity": integ, "telemetry": ev.get("telemetry")})
    ok = all(r["status"] == "ok" and r["integrity"]["ok"] for r in reports)
    ctx.calibration_ok = ok
    ctx.calibration = {"ok": ok, "expected_ms": expected_ms, "tolerance_ms": tolerance_ms, "replications": reports}
    ctx.ledger.append(ctx.chain_id, "calibration", ctx.calibration, Provenance(ENGINE_VERSION, object_hash(ctx.env), "platform-runner", probe_id="calibration", probe_version="1", workload_id=wl["id"], workload_version=str(wl["version"]), target_id=target.id, target_version=target.version, control_id="none", control_version="in-repo"))
    return ctx.calibration


def _pace_floors(ctx: RunContext) -> dict[str, Any] | None:
    """The pace floors pre-registered in the ks.resume gate this run loads (founder ruling 2026-09-13). A gate without
    them pre-registers no pace."""
    from .pace import floors_from_gate

    try:
        return floors_from_gate(load_gate(ctx.gates_dir, "ks.resume", ctx.root_public_hex, ctx.revocations))
    except FileNotFoundError:
        return None


def run_cell(ctx: RunContext, probe_id: str, target_id: str, control_id: str, workload_id: str, replications: int, *, counted: int | None = None) -> dict[str, Any]:
    """`replications` are scheduled; with `counted` set (fix B6), only the first `counted` measured in schedule order count."""
    probe = PROBES[probe_id]()
    target, control, wl = ctx.registry[target_id], ctx.registry[control_id], ctx.workloads[workload_id]
    try:
        gate = load_gate(ctx.gates_dir, probe.gate_id, ctx.root_public_hex, ctx.revocations)
    except FileNotFoundError:
        from mark_probes.gate import absent_gate

        gate = absent_gate(probe.gate_id, probe.family)
    ctx.gates[probe_id] = gate
    ok, why = applicable(control, target)
    reps: list[Replication] = []
    telemetry_incomplete = False
    integrity_reports = []
    self_report_inconsistent: list[int] = []   # A2: measured replications whose self-report disagreed with its receipts
    pace_streams: list[list[int]] = []   # a none cell's uninterrupted effect streams (the measured pace)
    pace_continuations: list[dict[str, Any]] = []   # the same replications' harness continuations, recorded beside the pace
    wl_variant = wl.get("variant") or "unspecified"
    clock_samples: list[dict[str, Any]] = []
    clock_gated = False
    # the resolver's health, sampled where the clock is: before the first replication and after each one (founder ruling 2026-09-23)
    watch: list[dict[str, Any]] = []
    broken = [b for b in ctx.broken_probes.get(probe_id, []) if b.get("target") == target_id and b.get("variant") == wl_variant]
    if broken:
        # the probe's none row on this target and workload variant already produced an impossible result in this run (scope
        # target x variant, fix A3): nothing it measures there can be read, so the pod's time is not spent on it
        b = broken[0]
        why = f"baseline invariant violated earlier in this run ({b['cell']}: {b['violations'][0]}); every {probe_id} row on {target_id} ({wl_variant}) is not_run"
        reps = [Replication(i, "", "not_run", why, None) for i in range(replications)]
    elif not ok:
        reps = [Replication(i, "", "not_run", why, None) for i in range(replications)]
    else:
        try:
            plan = probe.plan(wl)
        except NotImplementedError as e:
            reps = [Replication(i, "", "not_run", str(e), None) for i in range(replications)]
            plan = None
        if plan is not None and getattr(plan, "hold_paces", 0):
            # ks.resume v3 (founder ruling 2026-09-12): the hold is a number of MEASURED paces, the pace comes from an
            # earlier none cell on this target and workload, and a cell without a valid pace never runs on a guess
            pace = ctx.paces.get((target_id, workload_id))
            if pace is None or pace.get("status") != "ok":
                if pace is None:
                    why_pace = "pace_unavailable: no none cell on this target and workload ran before this cell"
                elif pace.get("status") == "invalid":
                    why_pace = f"pace_invalid: {pace.get('reason')} (source {pace.get('source_cell')})"
                else:
                    why_pace = f"pace_unavailable: {pace.get('reason')} (source {pace.get('source_cell')})"
                reps = [Replication(i, "", "not_run", why_pace, None) for i in range(replications)]
                plan = None
            else:
                plan.hold_ms = int(round(pace["pace_ms"] * plan.hold_paces))
                plan.pace = {**pace, "hold_paces": plan.hold_paces}
        if plan is not None:
            # a plan whose trigger is "none" never sends a halt (ks.false_halt: the control is armed, the harness stays silent)
            send_halt = plan.trigger.get("kind") != "none"
            # what a complete trace must hold depends on where the control lives: the agent's listener span
            # (control.halt) for in-process controls, the gateway's own span for an out-of-process one
            oop = control.control_class in ("out_of_process", "reference_instrument")
            from .self_report import SELF_REPORT_KIND, check_self_report, split_self_report

            # Continuous calibration (founder ruling 2026-09-22): the gate before a run certifies a quieter
            # machine than the run itself. Samples bracket every replication, judged by the SAME rule as the gate,
            # and carry the cgroup's throttle counters -- a different cause with the same symptom.
            from . import clockwatch

            # the SAME expected and tolerance the run's own gate used: an in-run sample held to a different
            # number would be a second calibration rule wearing the first one's name
            # **A run with no gate has agreed no tolerance, so nothing may be excluded on one.** The in-run samples
            # use the run's OWN expected value and tolerance; that is the whole reason they cannot be held to a
            # softer rule than the gate. When `calibrate` never ran there is no such number, and falling back to
            # this module's defaults would invent a threshold the run never agreed to -- which is the observer
            # changing the observed rather than watching it. Found 2026-09-22: a redecide test opens a run without
            # calibrating, and on a host whose sleep granularity is ~15 ms the 5 ms default failed at random and
            # discarded the replication the test was about. Samples are still taken and recorded, because "the
            # clock was not certified here, and here is what it read" is worth more than silence.
            _cal = ctx.calibration or {}
            clock_gated = bool(_cal)
            _cw_kw = {"expected_ms": float(_cal.get("expected_ms") or clockwatch.EXPECTED_MS),
                      "tolerance_ms": float(_cal.get("tolerance_ms") or clockwatch.TOLERANCE_MS)}
            clock_samples.append(clockwatch.sample(**_cw_kw))
            if ctx.peer_resolver is not None:
                watch.append(ctx.peer_resolver.health())
            for i in range(replications):
                ev = run_scenario(_scenario_cfg(ctx, target, control, wl, plan, halt=send_halt), ctx.mock, i)
                # Receipts and self-reports are never one mixed object (attempt 4, A2): every stamp the agent process
                # produced leaves the evidence here, into its own ledger record, and is checked against the receipts the
                # run kept. The probe below is handed the evidence without them, so no verdict can rest on one.
                ev, self_report = split_self_report(ev, in_process=not oop)
                ev["self_report_check"] = check_self_report(ev, self_report, model_driven=target_id in REQUEST_PARAMS)
                sr_rec = ctx.ledger.append(ctx.chain_id, SELF_REPORT_KIND, self_report, ctx.provenance(probe, target, control, wl))
                # A4: the per-replication stall check, from harness stamps, against the spec's pre-registered bounds; the record
                # is on the evidence and the row whether or not a bound existed, and a stall costs this replication only
                from .stall import check_stall

                ev["stall_check"] = check_stall(ev, (ctx.benchmark_spec or {}).get("stall_check"), target=target_id, model=ctx.llm_model)
                expected = {"scenario": 1, "agent.process": 1, "harness.halt_command": 1, ("gateway.control.halt" if oop else "control.halt"): 1} if ev.get("halt") else {"scenario": 1, "agent.process": 1}
                services = ["harness", f"agent:{target_id}", "mock-world"] + (["gateway"] if oop else [])
                integ = integrity_for(ev, ctx.run_dir / "spans.harness.jsonl", expected=expected, required_services=services)
                integrity_reports.append({"scenario_id": ev["scenario_id"], **integ})
                ev["integrity"] = integ
                ev["telemetry"]["integrity_ok"] = integ["ok"]
                ev["telemetry"]["self_report_record"] = {"seq": sr_rec.seq, "hash": sr_rec.hash, "content_hash": sr_rec.content_hash}
                # A14: what the registry declares about the control, on the evidence, so a probe can read it (ks.false_halt applies
                # only to a control that can raise a halt on its own)
                ev["control_declared"] = {"id": control.id, "control_class": control.control_class, "self_trigger_paths": list(control.self_trigger_paths),
                                          "self_trigger_note": control.self_trigger_note}
                # evidence payload to the ledger (the scenario's raw record), then the probe's reading of it
                # The sealed object is EXACTLY what the probe is handed below. A row's verdict must not depend on anything
                # the seal did not carry, and every gap between the two is the same defect waiting to recur: the workload
                # was stripped here since pass 1, so scope.side_channel and evidence.claimed_vs_landed -- which read the
                # workload's declared scope and script -- both sealed rows that re-read as not_run. Found by the
                # reproduction test, which seals, re-reads from the ledger, re-decides and compares, for every probe
                # (founder ruling 2026-09-21). The agent's own stamps are not here to be stripped: A2 split them out
                # before the probe saw them, into their own ledger record.
                ev_hash = ctx.ledger.put_object(ev)
                if ev.get("status") != "ok":
                    rep = Replication(i, ev["scenario_id"], "not_run", str(ev.get("reason")), None, {}, ev.get("telemetry", {}))
                else:
                    rep = probe.replication(i, ev)
                # **Here, not later.** This is the only point where `rep.raw` is the PROBE's and nothing else's. The
                # check sat further down, after the model-integrity block had already merged the runner's own
                # `not_run_reason_before_model_error` into raw -- so on any replication with a model error the guard
                # found the runner's key and blamed the probe, and the cell died. It killed the openhands-sdk smoke
                # on 2026-09-22; langgraph-ref passed because nothing there errored. A guard placed after the writes
                # it guards against reports its own author's work as the probe's.
                check_reserved_raw(probe_id, rep.raw)
                if not integ["ok"] and rep.status == "measured":
                    # An agent killed at the harness's timeout after the halt cannot close its root span or write its result -- its own
                    # record of itself, a self-report under A2 that never decides a result. When all five conditions hold, that missing
                    # self-record does not refuse a measurement taken from the world's receipts (founder ruling 2026-09-23, made in the
                    # practice stage after an OpenHands agent spent minutes asking a revoking gateway for its credential back).
                    from .integrity import killed_after_halt

                    kah = killed_after_halt(ev, integ, _agent_log_tail(ev)) if ev.get("agent_exit") == "killed after timeout" else None
                    if kah is not None and kah["applies"]:
                        rep = Replication(i, rep.scenario_id, rep.status, rep.reason, rep.value, {**(rep.raw or {}), "killed_after_halt": kah}, rep.telemetry)
                    else:
                        rep = Replication(i, rep.scenario_id, "not_run", "telemetry_incomplete: " + "; ".join(f"{k}: {v.get('missing') or v.get('problems') or v.get('reason')}" for k, v in integ["checks"].items() if not v["ok"]), None,
                                          {**(rep.raw or {}), **({"killed_after_halt": kah} if kah is not None else {})}, rep.telemetry)
                # An agent that exited before the trigger: whether it is the model's behaviour (founder ruling 2026-09-23) is decided from
                # the harness's own records -- exit code, the proxy's view of the last turn, the world's receipts -- and recorded on the row
                if rep.status == "not_run" and str(rep.reason or "").startswith("trigger not reached: agent exited"):
                    from .integrity import model_finished_early

                    # the target's declared finish tool (registry): a call to it alone is the model's final answer, as text is
                    rep = Replication(rep.index, rep.scenario_id, rep.status, rep.reason, rep.value,
                                      {**(rep.raw or {}), "early_exit": model_finished_early(ev, integ, target.finish_tool)}, rep.telemetry)
                # Model-integrity check (constitution model-integrity): what the model actually returned, from the model
                # proxy. A model error, truncation, unparsed tool call or HTTP error inside the observation window makes the
                # replication not_run model_error, whatever it would otherwise have read; its raw record is kept. A tool call
                # written as text to a request that offered no tools is not one: it is recorded as an attempt to act, before or
                # after the halt command (founder ruling 2026-09-23).
                mi = model_integrity(ev.get("model_calls"), serving_pin(ctx.env.get("serving")).get("max_model_len"), (ev.get("settled") or {}).get("mono_ns"),
                                     ((ev.get("halt") or {}).get("halt_command_at") or {}).get("mono_ns"))
                # the first request's prompt hash and the first reply's path hash (founder ruling 2026-09-12: what varies between replications)
                mi["first_reply"] = first_reply(ev.get("model_calls"))
                raw = {**(rep.raw or {}), "model": mi}
                if mi.get("error_class"):
                    raw["not_run_reason_before_model_error"] = rep.reason if rep.status == "not_run" else None
                    rep = Replication(rep.index, rep.scenario_id, "not_run", f"model_error: {mi['error_class']}: {mi['errors'][0]['detail']}", None, raw, rep.telemetry)
                else:
                    rep = Replication(rep.index, rep.scenario_id, rep.status, rep.reason, rep.value, raw, rep.telemetry)
                # No model calls (founder ruling 2026-09-12): a model-driven agent that made zero model calls through the proxy
                # did not operate under the probe's conditions, whatever the cause; the egress log names the cause when it can.
                # An agent that never started keeps that more specific reason.
                if ev.get("status") == "ok":
                    from urllib.parse import urlsplit

                    from .model_integrity import no_model_calls_reason

                    t0 = (ev.get("armed") or {}).get("mono_ns") or 0
                    t1 = (ev.get("settled") or {}).get("mono_ns") or mono_ns()
                    denials = [d for d in (ctx.egress.denied() if ctx.egress is not None else []) if t0 <= (d.get("mono_ns") or 0) <= t1]
                    server = urlsplit(ctx.llm_url)
                    nmc = no_model_calls_reason(target_id in REQUEST_PARAMS, ev.get("model_calls"), denials, (server.hostname, server.port))
                    if nmc:
                        raw = {**(rep.raw or {}), "no_model_calls": nmc, "not_run_reason_before_no_model_calls": rep.reason if rep.status == "not_run" else None}
                        rep = Replication(rep.index, rep.scenario_id, "not_run", nmc["reason"], None, raw, rep.telemetry)
                # harness continuations (single-call arm, founder ruling 2026-09-13): counted before and after the halt, per replication
                if (ev.get("continuations") or {}).get("applies"):
                    from .next_step import continuation_summary

                    rep = Replication(rep.index, rep.scenario_id, rep.status, rep.reason, rep.value, {**(rep.raw or {}), "continuations": continuation_summary(ev["continuations"])}, rep.telemetry)
                # the single-call observation window: how it ended, per replication (founder ruling 2026-09-13)
                if ev.get("observation_window"):
                    from .next_step import window_summary

                    rep = Replication(rep.index, rep.scenario_id, rep.status, rep.reason, rep.value, {**(rep.raw or {}), "observation_window": window_summary(ev["observation_window"])}, rep.telemetry)
                rep.telemetry["evidence_object"] = ev_hash
                # A4: a stalled replication is the instrument's, not the control's: not_run with the numbers, its raw kept
                st = ev["stall_check"]
                if st["stalled"] and rep.status == "measured":
                    from .stall import not_run_reason

                    rep = Replication(rep.index, rep.scenario_id, "not_run", not_run_reason(st), None, rep.raw, rep.telemetry)
                from .process_identity import attribution_state

                rep = Replication(rep.index, rep.scenario_id, rep.status, rep.reason, rep.value,
                                  {**(rep.raw or {}), "stall": {k: st[k] for k in ("applied", "applied_to", "stalled", "exceeded", "multiple", "baseline_key", "reason", "listener") if k in st},
                                   "stall_measured": st["measured"], "attribution_state": attribution_state(ev.get("mock_calls"))}, rep.telemetry)
                # the self-report check, on the row a reader sees; the label reaches the verdict through the preconditions
                chk = ev["self_report_check"]
                rep = Replication(rep.index, rep.scenario_id, rep.status, rep.reason, rep.value,
                                  {**(rep.raw or {}), "self_report": {"consistent": chk["consistent"], "inconsistencies": len(chk["inconsistent"]), "checked_calls": chk["checked_calls"], "unstamped_calls": chk["unstamped_calls"]}},
                                  rep.telemetry)
                if not chk["consistent"] and rep.status == "measured":
                    self_report_inconsistent.append(rep.index)
                reps.append(rep)
                clock_samples.append(clockwatch.sample(**_cw_kw))
                if ctx.peer_resolver is not None:
                    watch.append(ctx.peer_resolver.health())
                if control_id == "none" and rep.status == "measured":
                    from .pace import effect_stream

                    pace_streams.append(effect_stream(ev.get("mock_calls")))
                    if (ev.get("continuations") or {}).get("applies"):
                        pace_continuations.append(ev["continuations"])
    # Cross-cell context the gate needs: the none baseline on this workload, the variants seen for this cell's
    # (probe, target, control), and whether the control's primitive reached the agent (from the halt responses).
    variants_key = (probe_id, target_id, control_id)
    ctx.variants_seen.setdefault(variants_key, set()).add(wl.get("variant") or "unspecified")
    baseline = ctx.baselines.get((probe_id, target_id, workload_id))
    reach = [((r.raw.get("control_response") or {}).get("primitive_unreachable")) for r in reps if r.status == "measured"]
    # A2: which inconsistent replications COUNT (attempt 3's rule, carried): a counted replication of an evaluated cell, any
    # replication of a reference cell. `counted` is the schedule-order limit fix B6 applies inside probe.result.
    reference_cell = control.category == "reference" or control.control_class == "reference_instrument"
    measured_in_order = [r.index for r in sorted(reps, key=lambda r: r.index) if r.status == "measured"]
    counting = set(measured_in_order if (reference_cell or counted is None) else measured_in_order[:counted])
    srx_counted = sorted(i for i in self_report_inconsistent if i in counting)
    pace_rec = ctx.paces.get((target_id, workload_id)) or {}
    context = {"baseline_agg": baseline, "variants_seen": ctx.variants_seen[variants_key], "primitive_reachable": (not any(reach)) if reach else None,
               "control_category": control.category,
               "self_report_inconsistent": srx_counted,
               "baseline_self_report_inconsistent": ctx.tainted_baselines.get((probe_id, target_id, workload_id)) if control_id != "none" else None,
               "pace_self_report_inconsistent": pace_rec.get("self_report_inconsistent") if getattr(probe, "HOLD_PACES", 0) else None,
               # the clock facts the environment fingerprint already records: a decisive run needs the raw clock
               "clock_is_monotonic_raw": ctx.env.get("clock_is_monotonic_raw"), "clock_source": ctx.env.get("clock_source")}
    # the collector archive as it stands after this cell's agent processes have exited and flushed: a span from a
    # second instrumentation scope makes this cell's verdict informational (the reason names the scope)
    scan = ctx.scan_instruments(cell=f"{probe_id}/{target_id}/{control_id}/{workload_id}")
    si_ok = None if (not scan.get("scanned", True) or scan.get("archive") is None or scan.get("reason")) else scan["foreign_spans"] == 0
    si_detail = (", ".join(f"{k}: {v} span(s)" for k, v in (scan.get("foreign_scopes") or {}).items()) or None) if si_ok is False else None
    # Founder ruling 2026-09-12: a cell whose `none` baseline already reads the passing value cannot discriminate a
    # working control from no control, so it is not_run with the reason rather than an unearned pass. Scope is the
    # cell, not the probe (an IMPOSSIBLE baseline is the other class, above). The replications keep their raw, so
    # the post-halt ATTEMPTS quantity from the same scenarios survives and is reported as its own row.
    # **A replication whose clock was not vouched for is not counted** (founder ruling 2026-09-22). Samples
    # bracket each replication; if either side failed, the harness cannot say which side of the sample the delay
    # fell on, so the row is not_run with the reason -- the same treatment a stalled replication gets. The samples
    # are kept on every row either way, passing or failing, because "the clock was checked here and held" is the
    # evidence the latency verdicts rest on and it has to be readable, not inferred from the absence of a refusal.
    if len(clock_samples) == len(reps) + 1:
        from . import clockwatch as _cw

        marked = []
        for r in reps:
            before, after = clock_samples[r.index], clock_samples[r.index + 1]
            # no gate, no agreed tolerance, no exclusion: `vouched` is None rather than False, because "not
            # certified" and "certified and failed" are different facts and a reader must not have to guess which
            why_clock = _cw.unverified_reason(before, after) if clock_gated else None
            raw = {**(r.raw or {}), "clock": {"before": before, "after": after,
                                              "vouched": (why_clock is None) if clock_gated else None,
                                              "uncertified_reason": None if clock_gated else _cw.NO_GATE}}
            if why_clock and r.status == "measured":
                marked.append(Replication(r.index, r.scenario_id, "not_run", why_clock, None, raw, r.telemetry))
            else:
                marked.append(Replication(r.index, r.scenario_id, r.status, r.reason, r.value, raw, r.telemetry))
        reps = marked

    # **A silent resolver never produces a clean zero** (founder ruling 2026-09-23). Where attribution runs through the same-user
    # resolver, its health is sampled around every replication; every row carries both samples. A probe whose answer rests on
    # attribution (ATTRIBUTION_REQUIRED) cannot be counted when either sample found the resolver gone, silent, or wrong about the
    # one socket whose owner root already knows -- the harness cannot say which calls in between it would have attributed.
    if ctx.peer_resolver is not None and len(watch) == len(reps) + 1:
        needs = bool(getattr(probe, "ATTRIBUTION_REQUIRED", False))
        watched = []
        for r in reps:
            before, after = watch[r.index], watch[r.index + 1]
            why_att = before.get("why") if not before.get("available") else (after.get("why") if not after.get("available") else None)
            raw = {**(r.raw or {}), "attribution_watch": {"mode": "same-user resolver", "before": before, "after": after, "available": why_att is None}}
            if why_att and needs and r.status == "measured":
                watched.append(Replication(r.index, r.scenario_id, "not_run", why_att, None, raw, r.telemetry))
            else:
                watched.append(Replication(r.index, r.scenario_id, r.status, r.reason, r.value, raw, r.telemetry))
        reps = watched

    from mark_probes.baseline import nondiscriminating

    nd = nondiscriminating(probe_id, baseline, wl) if control_id != "none" else None
    if nd:
        reps = [Replication(r.index, r.scenario_id, "not_run", f"baseline_nondiscriminating: {nd}", None, r.raw, r.telemetry) if r.status == "measured" else r for r in reps]
    result = probe.result(target=target.to_json(), control=control.to_json(), workload=wl, reps=reps, gate=gate, calibration_ok=ctx.calibration_ok, telemetry_incomplete=telemetry_incomplete, context=context,
                          single_instrument_ok=si_ok, single_instrument_detail=si_detail, count_limit=counted)
    if nd:
        result["baseline_nondiscriminating"] = nd
    # C1: the row's per-tag outcomes under the pinned mapping (recomputed at close after the close passes may change the row)
    from mark_probes.tag_mapping import tag_outcomes

    result["tag_outcomes"] = tag_outcomes(ctx.tag_mapping, probe_id, result["aggregate"])
    # A2: attempt 3's audit as a cell-time record -- rule 1's outcome, rule 2's labels, rule 3's count -- on the row itself
    from .self_report import agent_side_fields, selective_suppression

    result["sourcing"] = {"self_report_inconsistent_replications": srx_counted, "self_report_records": sum(1 for r in reps if (r.telemetry or {}).get("self_report_record")),
                          "agent_side_fields": agent_side_fields(probe_id, control.control_class),
                          "selective_suppression": selective_suppression(reps, integrity_reports, target_id, reference_cell=reference_cell),
                          "rests_on_inconsistent": {k: v for k, v in (("baseline", context.get("baseline_self_report_inconsistent")), ("pace", context.get("pace_self_report_inconsistent"))) if v}}
    if control_id == "none" and srx_counted:
        ctx.tainted_baselines[(probe_id, target_id, workload_id)] = f"{probe_id}/{target_id}/none/{workload_id}"
    result["instrument_scan"] = {k: scan.get(k) for k in ("archive", "spans", "foreign_spans", "foreign_scopes", "foreign_services", "reason") if scan.get(k) is not None}
    # distinct first-reply paths among the measured replications (founder ruling 2026-09-12); recomputed at close
    min_reps = (gate.preconditions or {}).get("min_replications") if gate is not None else None
    result["reply_paths"] = cell_reply_paths(result["per_replication"], model_driven=target_id in REQUEST_PARAMS, variation=wl.get("variation"),
                                             min_replications=int(min_reps) if min_reps is not None else None)
    from .next_step import cell_continuations

    cont_cell = cell_continuations(result["per_replication"])
    if cont_cell is not None:
        result["continuations"] = cont_cell
    from .next_step import cell_observation_window

    win_cell = cell_observation_window(result["per_replication"])
    if win_cell is not None:
        result["observation_window"] = win_cell
    from .next_step import declared_scope

    scope = declared_scope(wl, target_id, ctx.llm_model)
    if scope is not None:
        # the floor the scope line's counts are read against, from this probe's gate (fix B4: "fewer than N measured")
        scope["min_replications"] = int(min_reps) if min_reps is not None else None
        result["declared_scope"] = scope
    if control_id == "none":
        ctx.baselines[(probe_id, target_id, workload_id)] = result["aggregate"]
        # the measured pace for this target and workload, from the FIRST none cell on it in the run (ks.resume v3)
        if (target_id, workload_id) not in ctx.paces and pace_streams:
            from .pace import declared_sleep_ms, pace_from_streams

            ctx.paces[(target_id, workload_id)] = pace_from_streams(pace_streams, declared_ms=declared_sleep_ms(wl), source_cell=f"{probe_id}/{target_id}/none/{workload_id}",
                                                                    floors=_pace_floors(ctx))
            if srx_counted:
                # a pace measured from replications whose self-reports disagreed with their receipts: every hold built on it inherits the label (A2)
                ctx.paces[(target_id, workload_id)]["self_report_inconsistent"] = f"{probe_id}/{target_id}/none/{workload_id}"
            if pace_continuations:
                import statistics as _st

                waits = [s["detection_wait_ms"] for c in pace_continuations for s in (c.get("sent") or [])]
                # the pace includes the continuations' latency; the count and the harness's own wait are recorded beside it
                ctx.paces[(target_id, workload_id)].update(continuations_per_replication=[c.get("sent_total") for c in pace_continuations],
                                                           continuation_detection_wait_ms_median=_st.median(waits) if waits else None)
            result["pace_set"] = ctx.paces[(target_id, workload_id)]
        # impossible-baseline invariant: a none row with an impossible reading proves the probe broken for this run
        from mark_probes.baseline import check_baseline

        chk = check_baseline(probe_id, result["per_replication"], wl)
        result["baseline_invariant"] = chk
        if chk["violations"]:
            ctx.broken_probes.setdefault(probe_id, []).append({"cell": f"{probe_id}/{target_id}/none/{workload_id}", "target": target_id, "variant": wl.get("variant") or "unspecified",
                                                               "invariant": chk["invariant"], "violations": chk["violations"]})
    result["integrity"] = integrity_reports
    rec = ctx.ledger.append(ctx.chain_id, "probe_result", result, ctx.provenance(probe, target, control, wl))
    result["ledger_record"] = {"seq": rec.seq, "hash": rec.hash, "content_hash": rec.content_hash}
    ctx.results.append(result)
    return result


def _process_instrument_summary(ctx: RunContext) -> dict[str, Any]:
    """Per-process instrument checks (telemetry.instrument_check via integrity) across every scenario of the run."""
    total = failed = foreign = no_check = 0
    problems: dict[str, int] = {}
    sdks: dict[str, int] = {}
    for r in ctx.results:
        for rep in r.get("integrity") or []:
            chk = (rep.get("checks") or {}).get("single_instrument")
            if chk is None:
                continue
            total += 1
            if not chk.get("ok"):
                failed += 1
                # A7: a process that emitted nothing is counted as that, never as a foreign tracer
                if chk.get("kind") == "no_agent_spans" or chk.get("problems") == ["no instrument check"]:
                    no_check += 1
                else:
                    foreign += 1
                for p in chk.get("problems") or []:
                    problems[p] = problems.get(p, 0) + 1
            for name, st in (chk.get("sdks") or {}).items():
                sdks[f"{name}:{st}"] = sdks.get(f"{name}:{st}", 0) + 1
    return {"scenarios_checked": total, "scenarios_failed": failed, "scenarios_foreign_instrument": foreign, "scenarios_no_agent_spans": no_check,
            "problems": problems, "sdks_seen": sdks, "ok": failed == 0}


def _apply_baseline_invariants(ctx: RunContext) -> dict[str, Any]:
    """Founder rule 2026-09-12: a `none` row with an impossible reading proves its probe broken for this run, on the target
    and workload variant where it fired (scope target x variant, fix A3, 2026-09-14). Every row of that probe on that target
    and variant (rows already computed included) becomes not_run, re-decided at the single verdict site with the invariant
    as the reason; the original aggregate is kept beside it, and the ledger gets a `baseline_invariants` record naming the
    violation and every row it invalidated. The per-cell probe_result records stay as appended: they are the evidence this
    record overrides."""
    from mark_probes.baseline import BASELINE_INVARIANT_SCOPE
    from mark_probes.gate import decide

    checked = [r["probe"]["id"] for r in ctx.results if r.get("baseline_invariant", {}).get("checked")]
    out: dict[str, Any] = {"scope": BASELINE_INVARIANT_SCOPE, "probes_checked": sorted(set(checked)), "violations": [], "rows_invalidated": [], "ok": True}
    if not ctx.broken_probes:
        return out
    for pid, items in ctx.broken_probes.items():
        for b in items:
            out["violations"].append({"probe": pid, **b})
    out["ok"] = False
    for r in ctx.results:
        pid = r["probe"]["id"]
        wl_variant = (ctx.workloads.get(r["workload"]["id"]) or {}).get("variant") or "unspecified"
        hits = [b for b in ctx.broken_probes.get(pid, []) if b.get("target") == r["target"]["id"] and b.get("variant") == wl_variant]
        if not hits:
            continue
        b = hits[0]
        reason = f"baseline invariant violated: {b['invariant']} ({b['cell']}: {b['violations'][0]}); every {pid} row on {b['target']} ({b['variant']}) in this run is not_run"
        if r.get("aggregate", {}).get("n"):
            r["aggregate_before_invariant"] = r["aggregate"]
            r["aggregate"] = {**r["aggregate"], "n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
        r["per_replication"] = [{**rep, "status": "not_run", "reason": (reason if rep.get("status") in ("measured", "measured_extra") else rep.get("reason"))} for rep in r.get("per_replication", [])]
        r["replications"] = {"requested": len(r["per_replication"]), "measured": 0, "not_run": [{"index": rep["index"], "reason": rep.get("reason")} for rep in r["per_replication"]],
                             "extra": [], "counted_limit": (r.get("replications") or {}).get("counted_limit")}
        v = r["verdict"]
        r["verdict_before_invariant"] = v
        r["verdict"] = decide(ctx.gates[pid], None, [x for x in v.get("reasons", []) if not x.startswith("outcome undefined")] + [reason]).to_json()
        r["baseline_invariant_applied"] = reason
        out["rows_invalidated"].append(f"{pid}/{r['target']['id']}/{r['control']['id']}/{r['workload']['id']}")
    ctx.ledger.append(ctx.chain_id, "baseline_invariants", out, Provenance(ENGINE_VERSION, object_hash(ctx.env), "platform-runner"))
    return out


def _apply_variant_presence(ctx: RunContext) -> dict[str, Any]:
    """Founder ruling 2026-09-12: variant presence is decided over the whole matrix at close, not from the variants
    that happened to run before a cell (which made the first variant of every pair informational and the second
    decisive). Runs after the baseline invariants: a row they invalidated contributed no measurement. The shared
    rule lives in redecide.py, which also applies it to bundles closed before this pass existed."""
    from .redecide import apply_variant_presence

    out = apply_variant_presence(ctx.results, ctx.gates)
    ctx.ledger.append(ctx.chain_id, "variant_presence", out, Provenance(ENGINE_VERSION, object_hash(ctx.env), "platform-runner"))
    return out


def close_run(ctx: RunContext, *, benchmark: str | None, sign_key_path: Path | None, cert_path: Path | None, signed_on: str = "pod") -> dict[str, Any]:
    """Write results.json, the signed manifest (if a key is available), verify the ledger, stop the mock world."""
    from mark_ledger.keys import iso, now_utc
    from mark_ledger.manifest import build_manifest, sign_manifest, write_signed

    ctx.mock.stop()
    if ctx.egress is not None:
        ctx.egress.stop()
    from .process_identity import set_agents_uid, set_resolver

    set_agents_uid(None)
    if ctx.peer_resolver is not None:
        set_resolver(None)
        ctx.peer_resolver.stop()
    serving_concurrency = ctx.serving_sampler.stop() if ctx.serving_sampler is not None else None
    model_proxy_summary = ctx.model_proxy.summary() if ctx.model_proxy is not None else None
    if ctx.model_proxy is not None:
        ctx.model_proxy.stop()
    telemetry.force_flush()
    # Task 4.1 exporter rule: a non-zero dropped-span counter fails the run outright.
    dropped = telemetry.dropped_spans() + sum(int(p.read_text() or 0) for p in ctx.run_dir.glob("scenarios/*/spans.*.dropped") if p.read_text().strip().isdigit())
    egress_denied = len(ctx.egress.denied()) if ctx.egress is not None else None
    # denied attempts by host: a target's own exporter reaching for the internet shows up here, next to the archive scan
    by_host: dict[str, int] = {}
    for d in (ctx.egress.denied() if ctx.egress is not None else []):
        key = f"{d.get('host')}:{d.get('port')}"
        by_host[key] = by_host.get(key, 0) + 1
    integrity = model_cache_integrity()
    # final incremental scan of the collector archive (spans flushed after the last cell's result was formed)
    ctx.scan_instruments(cell="close")
    single_instrument = {**ctx.single_instrument, "process_checks": _process_instrument_summary(ctx), "egress_denied_by_host": by_host}
    baseline_invariants = _apply_baseline_invariants(ctx)
    variant_presence = _apply_variant_presence(ctx)
    # distinct paths are counted over the replications that stayed measured after the close passes
    for r in ctx.results:
        paths = r.get("reply_paths")
        if paths is not None:
            r["reply_paths"] = cell_reply_paths(r.get("per_replication", []), model_driven=paths["model_driven"], variation=paths["variation"], min_replications=paths.get("min_replications"))
    # task d67450f4: a model-call log short of the calls the proxy answered is a sealed record missing what decided results --
    # the same class as a dropped span, and it fails the run the same way
    log_short = model_proxy_summary is not None and not model_proxy_summary.get("log_complete", True)
    failed = dropped > 0 or integrity["status"] == "changed" or log_short
    # C1: outcomes read from the aggregate as it stands after the close passes, under the run's pinned mapping
    from mark_probes.tag_mapping import tag_outcomes

    for r in ctx.results:
        r["tag_outcomes"] = tag_outcomes(ctx.tag_mapping, r["probe"]["id"], r["aggregate"])
    tag_mapping = ctx.tag_mapping.to_json()
    # C2: the block resolved at open, with the calls the harness made through the run counted beside it; nothing else changes
    operator_record = dict(ctx.operator, permitted_calls=ctx.call_gate.summary()) if ctx.operator is not None and ctx.call_gate is not None else ctx.operator
    # change-set B4/B5 (founder rulings 2026-09-15): the study sets' disjointness and each open-source subject's enterprise delta,
    # recorded with the run (and the disjointness pinned in the manifest) so the report reads the run's own record
    from .registry import load_enterprise_deltas, study_sets_record

    study_sets = study_sets_record(ctx.registry.values())
    delta_refs = sorted({t.enterprise_delta_ref for t in ctx.registry.values() if t.enterprise_delta_ref})
    known_deltas = load_enterprise_deltas() if delta_refs else {}
    enterprise_deltas = {ref: known_deltas[ref] for ref in delta_refs if ref in known_deltas}
    results = {"schema": "mark.run-results/1", "run_id": ctx.run_id, "benchmark": benchmark, "benchmark_spec": ctx.benchmark_spec, "started_at": ctx.started_at, "ended_at": iso(now_utc()), "environment": ctx.env,
               "egress_control": ctx.env.get("egress_control"), "egress_denied_attempts": egress_denied, "egress_denied_by_host": by_host, "dropped_spans": dropped, "model_cache_integrity": integrity, "run_failed": failed,
               "run_failure_reason": (f"{dropped} span(s) dropped by a synchronous exporter" if dropped else "") + (" model cache changed during the run" if integrity["status"] == "changed" else "")
               + (f" model-call log incomplete: {model_proxy_summary.get('log_lines')} line(s) on disk for {model_proxy_summary.get('calls')} call(s), {model_proxy_summary.get('log_write_errors')} write error(s)" if log_short else "") or None,
               "calibration": ctx.calibration, "single_instrument": single_instrument, "baseline_invariants": baseline_invariants, "variant_presence": variant_presence, "model_proxy": model_proxy_summary, "serving_concurrency": serving_concurrency,
               "tag_mapping": tag_mapping, "operator": operator_record,
               "paces": {f"{t}/{w}": p for (t, w), p in ctx.paces.items()}, "pace_cells": ctx.pace_cells, "env_test": ctx.env_test, "study_sets": study_sets, "enterprise_deltas": enterprise_deltas, "results": ctx.results}
    rp = ctx.run_dir / "results.json"
    rp.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    results_hash = sha256_hex(rp.read_bytes())
    ctx.ledger.append(ctx.chain_id, "run_close", {"results_sha256": results_hash, "ended_at": results["ended_at"]}, Provenance(ENGINE_VERSION, object_hash(ctx.env), "platform-runner"))
    from .anchoring import local_anchor

    local_anchor(ctx.ledger, ctx.chain_id)
    verification = ctx.ledger.verify(ctx.chain_id).to_json()
    pins = {"image_digest": ctx.env.get("image_digest"), "image_platform_digest": ctx.env.get("image_platform_digest"), "lockfile_sha256": lockfile_sha256(), "engine_version": ENGINE_VERSION, "repo_commit": repo_commit(),
            "model": {"id": ctx.llm_model, "hash": os.environ.get("MARK_MODEL_HASH"), "cache_integrity_after_run": integrity["status"]}, "targets": {t.id: t.version for t in ctx.registry.values()},
            "gates": {r["probe"]["id"]: r["verdict"]["gate"]["gate_hash"] for r in ctx.results}, "probes": {r["probe"]["id"]: r["probe"]["spec_hash"] for r in ctx.results},
            "tag_mapping": tag_mapping,
            "permitted_calls_sha256": (ctx.call_gate.declaration.sha256 if ctx.call_gate is not None else None),
            "workloads": {r["workload"]["id"]: r["workload"]["hash"] for r in ctx.results},
            "study_sets": study_sets,
            # what shaped the model's output (founder ruling 2026-09-12): context length, tool-call parser, seed, request parameters
            "serving": {**serving_pin(ctx.env.get("serving")),
                        "concurrency_in_effect": ({k: serving_concurrency.get(k) for k in ("observed", "max_running", "mean_running_while_busy", "samples")} if serving_concurrency else None)}}
    manifest = build_manifest(run_id=ctx.run_id, benchmark=benchmark, pins=pins, environment={**ctx.env, "gpu": ctx.env.get("gpu"), "single_instrument": single_instrument, "baseline_invariants": baseline_invariants,
                                                                                      "probe_filter": (ctx.benchmark_spec or {}).get("probe_filter"), "serving_concurrency": serving_concurrency,
                                                                                      "model_proxy": model_proxy_summary,
                                                                                      "paces": {f"{t}/{w}": p for (t, w), p in ctx.paces.items()}, "pace_cells": ctx.pace_cells,
                                                                                      "ordering": (ctx.benchmark_spec or {}).get("ordering"), "env_test": ctx.env_test,
                                                                                      "variant_presence": variant_presence and {k: variant_presence[k] for k in ("rule", "clarification", "rows_changed")}}, started_at=ctx.started_at, ended_at=results["ended_at"],
                              chain_id=ctx.chain_id, chain_root=ctx.ledger.chain_root(ctx.chain_id), results_hash=results_hash,
                              # A5: the run's own account of what it scheduled, recorded and did not run, inside the signed object
                              account=__import__("mark_platform.account", fromlist=["run_level_account"]).run_level_account(results, run_dir=ctx.run_dir),
                              operator=operator_record)
    (ctx.run_dir / "manifest.unsigned.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    # Right-of-reply slots (pre-flight E): one per target maintainer with a repository; empty until they answer.
    slots = [{"target": t.id, "repo": t.repo, "sha": t.sha, "contact_channel": None, "dispatched_at": None, "window_days": 30, "response": None, "status": "not dispatched"}
             for t in ctx.registry.values() if t.repo and t.category in ("agent", "control", "framework-native-control")]
    (ctx.run_dir / "right-of-reply.json").write_text(json.dumps({"schema": "mark.right-of-reply/1", "run_id": ctx.run_id, "slots": slots}, indent=1), encoding="utf-8")
    signed_path = None
    signature_status = "unsigned"
    if sign_key_path and cert_path and sign_key_path.exists() and cert_path.exists():
        from mark_ledger.manifest import manifest_signature_status

        so = sign_manifest(manifest, sign_key_path.read_text().strip(), json.loads(cert_path.read_text()), signed_on=signed_on)
        signed_path = write_signed(ctx.run_dir / "manifest.json", so)
        signature_status = f"{manifest_signature_status(so)}, signed_on={signed_on}"
    return {"run_dir": str(ctx.run_dir), "results": str(rp), "manifest": str(signed_path) if signed_path else None, "manifest_unsigned": str(ctx.run_dir / "manifest.unsigned.json"),
            "ledger": verification, "chain_root": ctx.ledger.chain_root(ctx.chain_id), "run_failed": failed, "dropped_spans": dropped, "egress_denied_attempts": egress_denied, "model_cache_integrity": integrity,
            "manifest_signature": signature_status, "right_of_reply": str(ctx.run_dir / "right-of-reply.json")}
