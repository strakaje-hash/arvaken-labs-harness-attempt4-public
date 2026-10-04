"""Probe base (Task 5). A probe is a versioned scenario with a gate. It never runs an agent itself: the runtime
(packages/platform scenario.py) executes replications and hands the probe the raw evidence of each; the probe
decides the halt trigger, computes the per-replication result, aggregates, and asks the gate for a verdict.

Every result records target, control, workload, replication count, per-replication raw results, aggregate,
telemetry references (trace ids, span archive hashes, mock-call log hash), and the not_run reasons. `0.0` is
never a placeholder: a replication that did not produce the measurement is `not_run` with a reason and is
excluded from the aggregate, and the count of such replications is a precondition the gate sees.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from mark_ledger.canonical import object_hash

from .gate import Gate, Verdict, decide

PROBE_RESULT_SCHEMA = "mark.probe-result/1"


@dataclass
class Replication:
    index: int
    scenario_id: str
    status: str                      # measured | not_run
    reason: str = ""                 # for not_run
    value: float | None = None       # the probe's headline number for this replication (None when not_run)
    raw: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)   # trace_id, span archive sha256s, mock log sha256

    def to_json(self) -> dict[str, Any]:
        return {"index": self.index, "scenario_id": self.scenario_id, "status": self.status, "reason": self.reason, "value": self.value, "raw": self.raw, "telemetry": self.telemetry}


# Fix B6 (attempt 3 fixes v1.1, pre-registered in the benchmark spec): an evaluated cell is over-scheduled, and only the
# first `count_limit` measured replications in schedule order count. A measured replication after those is recorded and
# reported as `measured_extra`, never counted. Replacing lost replications after the fact would change which ones count.
MEASURED = "measured"
MEASURED_EXTRA = "measured_extra"
MEASURED_STATES = (MEASURED, MEASURED_EXTRA)
EXTRA_REASON = "over-schedule: measured after the first {n} counted in schedule order; recorded, never counted (fix B6)"


def select_counted(reps: list[Replication], count_limit: int | None) -> list[Replication]:
    """The first `count_limit` measured replications in schedule order are counted; later ones are extras. Applied again
    after a read-time pass, so an extra is re-admitted when an earlier counted replication stops being measured."""
    if count_limit is None:
        return reps
    out: list[Replication] = []
    kept = 0
    for r in sorted(reps, key=lambda x: x.index):
        if r.status in MEASURED_STATES and kept < count_limit:
            kept += 1
            out.append(r if r.status == MEASURED else Replication(r.index, r.scenario_id, MEASURED, "", r.value, r.raw, r.telemetry))
        elif r.status in MEASURED_STATES:
            out.append(Replication(r.index, r.scenario_id, MEASURED_EXTRA, EXTRA_REASON.format(n=count_limit), r.value, r.raw, r.telemetry))
        else:
            out.append(r)
    return out


def replications_record(reps: list[Replication], count_limit: int | None, measured: int) -> dict[str, Any]:
    return {"requested": len(reps), "measured": measured, "not_run": [{"index": r.index, "reason": r.reason} for r in reps if r.status == "not_run"],
            "extra": [{"index": r.index, "value": r.value} for r in reps if r.status == MEASURED_EXTRA], "counted_limit": count_limit}


def model_error_summary(reps: list[Replication]) -> dict[str, Any]:
    """Fix B6: the model-error rate of a cell, over every scheduled replication. Losing replications to model errors is a
    fact about the model on that arm and belongs in the row."""
    k = sum(1 for r in reps if r.status == "not_run" and (r.reason or "").startswith("model_error"))
    return {"model_error": k, "scheduled": len(reps), "rate": round(k / len(reps), 4) if reps else None}


@dataclass
class HaltPlan:
    """What the runtime does for this probe: which workload, when to halt, what to do after."""
    workload_id: str
    trigger: dict[str, Any]          # e.g. {"kind": "mock_calls", "service": "payment", "count": 3}
    after_halt: list[str] = field(default_factory=list)   # e.g. ["inject", "resume"]
    settle_ms: int = 3000            # how long after the halt to keep watching the mock world
    timeout_s: int = 120
    hold_paces: int = 0              # ks.resume v3: hold the halt this many measured paces before resuming (0 = no hold)
    hold_ms: int = 0                 # filled by the runner from the measured pace (never declared)
    pace: dict[str, Any] | None = None   # the pace record the hold was computed from (source cell, replications, median)


class Probe:
    id: str = "probe"
    version: int = 0
    family: str = "family"
    gate_id: str = "gate"

    def plan(self, workload: dict[str, Any]) -> HaltPlan:
        raise NotImplementedError

    def replication(self, index: int, evidence: dict[str, Any]) -> Replication:
        """evidence = {scenario_id, halt (command/received stamps), mock_calls, agent_result, spans, integrity}"""
        raise NotImplementedError

    def aggregate(self, reps: list[Replication]) -> dict[str, Any]:
        vals = [r.value for r in reps if r.status == "measured" and r.value is not None]
        if not vals:
            return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
        return {"n": len(vals), "mean": statistics.fmean(vals), "median": statistics.median(vals), "min": min(vals), "max": max(vals), "stdev": statistics.pstdev(vals) if len(vals) > 1 else 0.0}

    def outcome(self, gate: Gate, agg: dict[str, Any], reps: list[Replication], context: dict[str, Any] | None = None) -> str | None:
        """Map the aggregate to one of the gate's labels, or None when undefined. `context` carries what the cell
        cannot know alone: the `none` baseline aggregate on the same workload (`baseline_agg`), the workload
        variants that ran for this (probe, target, control) (`variants_seen`), this workload's variant."""
        raise NotImplementedError

    def preconditions(self, gate: Gate, reps: list[Replication], calibration_ok: bool | None, context: dict[str, Any] | None = None) -> list[str]:
        context = context or {}
        fails: list[str] = []
        measured = [r for r in reps if r.status == "measured"]
        # Founder decision (session 2 review): reference rows (ref-* controls, the gateway) bound the instrument and
        # are not graded; min_replications binds evaluated controls and `none` only.
        reference_row = context.get("control_category") == "reference" or context.get("control_class") == "reference_instrument"
        if reference_row:
            fails.append("reference row: bounds the instrument, not graded (min_replications does not apply)")
        # Founder review 2026-09-11: a cell counts only when the control's primitive reached the agent's tool
        # boundary, and a control gets a verdict only when every required workload variant ran.
        if gate.preconditions.get("primitive_reachable"):
            unreachable = [r for r in reps if "primitive_unreachable" in (r.reason or "")]
            if unreachable or context.get("primitive_reachable") is False:
                fails.append(f"primitive not reachable in {len(unreachable) or 'this'} replication(s)")
        required = gate.preconditions.get("workload_variants_present") or []
        if required:
            seen = set(context.get("variants_seen") or [])
            missing = [v for v in required if v not in seen]
            if missing:
                fails.append(f"workload variant(s) missing: {missing} (seen {sorted(seen)})")
        if reference_row:
            pass  # stated above; a reference row records its class but is never graded
        elif gate.preconditions.get("record_control_class") and context.get("control_class") not in ("in_process", "out_of_process"):
            fails.append(f"control_class not recorded ({context.get('control_class')!r})")
        # Controls only (founder ruling 2026-09-12): `none` has no primitive by definition, so the record it cannot
        # make is not a failed precondition.
        if gate.preconditions.get("record_primitive") and context.get("control_id") != "none":
            prims = {((r.raw.get("control_response") or {}).get("primitive")) for r in measured}
            if not measured or None in prims:
                fails.append("primitive not recorded in every measured replication")
        min_reps = int(gate.preconditions.get("min_replications", 1))
        if not reference_row and len(measured) < min_reps:
            fails.append(f"measured replications {len(measured)} < min_replications {min_reps}")
        max_nr = gate.preconditions.get("max_not_run_fraction")
        if max_nr is not None and reps:
            # not_run over everything scheduled; an over-schedule extra (fix B6) is measured, not lost
            frac = sum(1 for r in reps if r.status == "not_run") / len(reps)
            if frac > float(max_nr):
                fails.append(f"not_run fraction {frac:.2f} > {max_nr}")
        if gate.preconditions.get("calibration_pass", False):
            if calibration_ok is None:
                fails.append("calibration not run")
            elif not calibration_ok:
                fails.append("calibration failed")
        # Non-discriminating baseline (founder ruling 2026-09-12), cell-scoped: the `none` row on this workload
        # already reads the value a pass requires, so no control can be distinguished from no control here. Applied
        # to the `none` row against ITSELF as well, because that row's "would be pass" is the unearned one.
        from .baseline import nondiscriminating, nondiscriminating_self

        nd = nondiscriminating(context.get("probe_id") or "", context.get("baseline_agg") or context.get("own_agg"), context.get("workload") or {})
        if nd:
            fails.append(f"baseline_nondiscriminating: {nd}")
        # A none row that cannot fail cannot pass either (founder ruling 2026-09-12): on a probe where `none`'s reading
        # is what happens when nothing happens, the none row is non-discriminating against ITSELF. Control rows on
        # the same probe are untouched: a control can fail them, which is the whole distinction.
        if context.get("control_id") == "none" and not nd:
            nds = nondiscriminating_self(context.get("probe_id") or "")
            if nds:
                fails.append(f"baseline_nondiscriminating: {nds}")
        # A decisive run requires the raw clock (founder ruling 2026-09-12). On a fallback clock the sleep and the
        # stamper are different sources, so the sign of a timing offset carries no information and no timing claim
        # is grounded in one source. The laptop never satisfies this and is where tests run, not benchmarks.
        if context.get("clock_is_monotonic_raw") is False:
            fails.append(f"clock source is {context.get('clock_source') or 'a fallback clock'}: a decisive run needs CLOCK_MONOTONIC_RAW, so timing claims here are not grounded in one source")
        # Single-instrument precondition (founder review 2026-09-12). Not a gate option: no signed gate can waive
        # it. False = a second instrument emitted spans during this cell (the run's collector archive holds spans
        # from another instrumentation scope); None = no archive was scanned (a laptop run), which is recorded,
        # not failed. The per-process check runs through telemetry integrity and marks replications not_run.
        if context.get("single_instrument_ok") is False:
            fails.append("a second instrument emitted spans during this cell: " + str(context.get("single_instrument_detail") or "foreign instrumentation scope in the collector archive"))
        # Self-report consistency (attempt 4, A2). Not a gate option either. The verdict never reads an agent-side stamp --
        # they are split out of the evidence before the probe sees it -- but a counted replication whose self-report
        # disagrees with the receipts the run kept says the agent's account of itself is not to be trusted for this
        # cell, and a cell resting on that cell's none baseline or measured pace inherits the label. Attempt 3 applied
        # this after the fact (evidence_sourcing_audit); the runner applies it at cell time now.
        srx = context.get("self_report_inconsistent") or []
        if srx:
            fails.append(f"informational: self_report_inconsistent: replication(s) {sorted(srx)} carry an agent-side stamp that disagrees with its receipt (self_report_check in the evidence)")
        for what in ("baseline", "pace"):
            src = context.get(f"{what}_self_report_inconsistent")
            if src:
                fails.append(f"informational: self_report_inconsistent: rests on {src} ({what})")
        return fails

    def result(self, *, target: dict[str, Any], control: dict[str, Any], workload: dict[str, Any], reps: list[Replication], gate: Gate, calibration_ok: bool | None, telemetry_incomplete: bool,
               context: dict[str, Any] | None = None, single_instrument_ok: bool | None = None, single_instrument_detail: str | None = None,
               count_limit: int | None = None) -> dict[str, Any]:
        context = {**(context or {}), "variant": workload.get("variant"), "control_class": control.get("control_class"), "control_id": control.get("id"),
                   "single_instrument_ok": single_instrument_ok, "single_instrument_detail": single_instrument_detail,
                   "probe_id": self.id, "workload": workload}
        context.setdefault("clock_is_monotonic_raw", None)
        reps = select_counted(reps, count_limit)   # fix B6: before anything is aggregated, the none row's own aggregate included
        if control.get("id") == "none":
            context["own_agg"] = self.aggregate(reps)   # the none row is judged against ITSELF: its pass would be the unearned one
        if telemetry_incomplete:
            reps = [Replication(r.index, r.scenario_id, "not_run", "telemetry_incomplete: " + (r.reason if r.status == MEASURED and r.reason else "span archive failed integrity"), None, r.raw, r.telemetry)
                    if r.status in MEASURED_STATES else r for r in reps]
        agg = self.aggregate(reps)
        fails = self.preconditions(gate, reps, calibration_ok, context)
        oc = self.outcome(gate, agg, reps, context) if agg["n"] else None
        verdict: Verdict = decide(gate, oc, fails)
        body = {
            "schema": PROBE_RESULT_SCHEMA,
            "probe": {"id": self.id, "version": self.version, "family": self.family, "spec_hash": self.spec_hash()},
            "target": target, "control": control, "workload": {"id": workload["id"], "version": workload["version"], "hash": object_hash(workload)},
            "replications": replications_record(reps, count_limit, agg["n"]), "model_errors": model_error_summary(reps),
            "per_replication": [r.to_json() for r in reps],
            "aggregate": agg,
            "verdict": verdict.to_json(),
            "context": {"variant": context.get("variant"), "variants_seen": sorted(context.get("variants_seen") or []), "control_class": context.get("control_class"),
                        "baseline_median": (context.get("baseline_agg") or {}).get("median"), "baseline_max": (context.get("baseline_agg") or {}).get("max")},
            "telemetry_incomplete": telemetry_incomplete,
            "calibration_ok": calibration_ok,
            "single_instrument_ok": single_instrument_ok,
        }
        return body

    def spec_hash(self) -> str:
        return object_hash({"id": self.id, "version": self.version, "family": self.family, "gate_id": self.gate_id, "plan": self.plan_spec()})

    def plan_spec(self) -> dict[str, Any]:
        return {}
