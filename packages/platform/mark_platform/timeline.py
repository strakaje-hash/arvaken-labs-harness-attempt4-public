"""`platform bench timeline`: the raw timeline of sampled replications, so reading the evidence is a command and
not an act of discipline.

Pre-flight item 7 (founder): *read raw timelines for a sample of PASSES, not only fails.* A verdict is a reading of
evidence, and a pass that nobody looked at is the easiest place for a defect to live: every reading defect this
platform has found so far was found by looking at a row that looked fine (the LangGraph baseline that "revoked"
with no control installed, ks.resume's ten payments simultaneously missing and extra, the halt that fired on
refused attempts). `--verdict pass` samples exactly those rows.

What it prints per replication, all relative to the halt command so the order is the point:
  the halt command stamp and the control's response (primitive, mechanism, acted)
  every mock-world call: dispatch and receipt offsets in ms, the service, the reference, the agent turn, and
    whether the world refused it (an attempt, not an effect)
  the probe's own numbers for that replication, its trigger rule, and the agent's run outcome
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from mark_ledger.store import Ledger


def _ms(a: int | None, b: int | None) -> str:
    return "        -" if a is None or b is None else f"{(a - b) / 1e6:+9.1f}"


def _rows(results: dict[str, Any], probe: str | None, control: str | None, workload: str | None, verdict: str) -> list[dict[str, Any]]:
    out = []
    for r in results.get("results", []):
        if probe and r["probe"]["id"] != probe:
            continue
        if control and r["control"]["id"] != control:
            continue
        if workload and r["workload"]["id"] != workload:
            continue
        v = r["verdict"].get("outcome_if_decisive")
        if verdict != "any" and v != verdict:
            continue
        out.append(r)
    return out


def timeline(run_dir: str | Path, *, probe: str | None = None, control: str | None = None, workload: str | None = None,
             verdict: str = "pass", sample: int = 2, seed: int = 7, calls: int = 24) -> str:
    d = Path(run_dir)
    results = json.loads((d / "results.json").read_text(encoding="utf-8"))
    rows = _rows(results, probe, control, workload, verdict)
    lines = [f"# Raw timelines from {results['run_id']}",
             f"filter: probe={probe or 'any'} control={control or 'any'} workload={workload or 'any'} verdict={verdict}; {len(rows)} matching cell(s)", ""]
    if not rows:
        return "\n".join(lines + ["no cell matches; `bench report` lists what the run holds"]) + "\n"
    led = Ledger(d / "ledger") if (d / "ledger").exists() else None
    rng = random.Random(seed)
    for r in rows[: max(1, sample)] if len(rows) <= sample else rng.sample(rows, sample):
        measured = [x for x in r["per_replication"] if x["status"] == "measured"]
        if not measured:
            lines += [f"## {r['probe']['id']} / {r['target']['id']} / {r['control']['id']} / {r['workload']['id']}: no measured replication", ""]
            continue
        rep = rng.choice(measured)
        raw = rep.get("raw") or {}
        lines += [f"## {r['probe']['id']} / {r['target']['id']} / {r['control']['id']} / {r['workload']['id']}",
                  f"verdict {r['verdict']['label']} (would be {r['verdict'].get('outcome_if_decisive')}); "
                  f"replication {rep['index']} of {r['replications']['requested']}, value {rep.get('value')}",
                  f"scenario `{rep['scenario_id']}`", ""]
        if raw.get("trigger_rule"):
            lines.append(f"trigger rule: {raw['trigger_rule']}")
        cr = raw.get("control_response") or {}
        if cr:
            lines.append(f"control response: primitive={cr.get('primitive')} mechanism={cr.get('mechanism')} acted={cr.get('acted')} reachable={cr.get('reachable')}")
        for k in ("halt_class", "post_halt_landed", "pre_halt_delayed", "post_halt_attempts", "duplicates", "missing", "extra",
                  "references_reformatted", "survivors", "children_spawned", "spurious_halts", "mechanism", "inject_state", "agent_acted", "agent_reason", "inject_error",
                  "injected_after_window", "effects_after_window", "late", "late_by_ms", "window"):
            if raw.get(k) not in (None, [], {}):
                lines.append(f"{k}: {raw[k]}")
        cmd = raw.get("halt_command_mono_ns")
        ev = None
        if led is not None and rep.get("telemetry", {}).get("evidence_object"):
            try:
                ev = json.loads(led.get_object(rep["telemetry"]["evidence_object"]))
            except Exception:  # noqa: BLE001
                ev = None
        mock = (ev or {}).get("mock_calls") or []
        lines += ["", "| stamp of record ms | world receipt ms | service | reference | turn | refused |", "| --- | --- | --- | --- | --- | --- |"]
        if not mock:
            lines.append("| (the scenario's mock-call record is not in this bundle's ledger) | | | | | |")
        for c in mock[:calls]:
            ref = str((c.get("body") or {}).get("reference") or (c.get("body") or {}).get("op") or "")[:18]
            # the stamp of record (A2): the gateway's arrival where one sat in front, else the world's receipt; an attempt 3
            # bundle carries the agent's dispatch stamp here instead, and this column shows what that bundle's verdicts read
            record = c.get("hop_arrived_mono_ns") if c.get("hop_arrived_mono_ns") is not None else (c.get("received_mono_ns") if "dispatch_mono_ns" not in c else c.get("dispatch_mono_ns"))
            lines.append(f"| {_ms(record, cmd)} | {_ms(c.get('received_mono_ns'), cmd)} | {c.get('service')} | {ref} | {c.get('turn')} | {'yes: ' + str(c.get('refused'))[:40] if c.get('refused') else ''} |")
        if len(mock) > calls:
            lines.append(f"| ... {len(mock) - calls} more calls | | | | | |")
        ro = (ev or {}).get("agent_result", {}) or {}
        lines += ["", f"agent run outcome: {json.dumps(ro.get('run_outcome'))[:300]}",
                  f"agent exit: {(ev or {}).get('agent_exit')}; halt/resume/inject recorded: "
                  f"{bool((ev or {}).get('halt'))}/{bool((ev or {}).get('resume'))}/{bool((ev or {}).get('inject'))}", ""]
    lines += ["Offsets are milliseconds relative to the halt command: negative is before it, positive after.",
              "The stamp of record is what the verdict ordered by: from attempt 4 the first harness hop's arrival (egress proxy, gateway, or the world itself) (A2); "
              "in an attempt 3 bundle it is the agent's dispatch stamp, which that attempt's audit checked after the fact.",
              "A refused call is an attempt the world did not execute, never an effect."]
    return "\n".join(lines) + "\n"
