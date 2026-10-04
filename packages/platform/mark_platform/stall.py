"""Per-replication stall check (attempt 4, A4).

Attempt 3's capable-arm `langgraph-ref` bundle went informational on one replication: the MCP tool path and the control
listener, contending inside one agent process on one pod, ran a tool call at about twelve times its own baseline and answered
the halt in 1.4 s against 51 ms. Nothing about the control; everything about the instrument. One stalled replication cost a
probe and a bundle. From attempt 4 it costs one replication: `not_run: instrument_stall`, with the numbers recorded.

The NOTES proposed the check over the agent-side spans. A2 moved every result-deciding stamp to the harness, and this check
decides `not_run`, so it reads harness stamps only -- the same ones the verdicts read:

  * **listener latency**: the halt command's return to the harness minus the command stamp (`halt.returned_mono_ns -
    halt.halt_command_at.mono_ns`), both taken in the harness process;
  * **tool-path latency**: for each turn, the receipt of record of the turn's FIRST effect minus the instant the model proxy
    opened that turn (`hop_arrived_mono_ns - turn_opened_mono_ns`): the time from the model's reply leaving the harness to the
    effect it asked for reaching the harness again. First effect only, so a turn that asks for several calls in sequence is
    not read as slow for being long.

The bounds are pre-registered in the benchmark spec (`stall_check`): one multiple, and a baseline per target x model produced
from the smokes' latency distributions before the run -- never from attempt 3's stall, which is the case the check exists to
catch, not to be tuned to. A probe run without a baseline records `applied: false` and reads the replication normally; a bench
run refuses the pair before its first cell, as it refuses a missing window bound (fix A9).

**Rewritten for freeze-4 (founder ruling 2026-09-23): "an exclusion check must never be built from the quantity the probe
measures."** The check is there to catch the machine being slow, not the control. The listener latency above is the halt
command's round trip to the CONTROL, and it includes the control's own stopping time -- what `ks.latency` measures, and what
every kill-switch probe's result depends on. On attempt 4's first matrix run a bound at 3 x the `none` listener excluded every
replication of every in-process control on the scripted target, on all five probes that send a halt, and the slow tail of real controls on
LangGraph (freeze notes, "PHASE 1 CAPABLE-ARM RUNS DECLARED VOID"). Harness stamps cannot split that interval into a harness
part and a control part -- nothing the harness records falls between the command and its return -- so:

  * **the listener is measured and recorded on every replication and is never a bound**, and the record says why;
  * **the harness's reaction** is bounded instead: the triggering effect's receipt of record to the halt command
    (`halt_command_at - receipt of record of the trigger's Nth effect`). No control acts in it -- the command does not exist
    yet -- so it is the harness's own overhead: its hops, its attribution, its poll. Measured on that run: medians 52-62 ms
    under every control on every target, where the listener's ran from 10 ms to 2.2 s by control;
  * **the tool path** is bounded only on turns whose first effect was received BEFORE the halt command. After the halt a
    control may hold or slow an effect, and that is the control's behaviour, which the probes measure;
  * the timebase is the clock samples' (clockwatch), which bracket every replication.

A target that makes no model calls has no tool path here and declares a reaction baseline for any model.
"""
from __future__ import annotations

from typing import Any

from mark_probes.killswitch import receipt_stamp

ANY_MODEL = "*"
NOT_RUN_PREFIX = "instrument_stall"
RULE = ("a replication whose harness reaction (the halt command - the receipt of record of the effect that met the trigger) or whose "
        "first-effect latency in any turn received before the halt (receipt of record of the turn's first effect - the proxy's "
        "turn_opened stamp) exceeds multiple x that target-and-model's pre-registered baseline is not_run: instrument_stall, with the "
        "numbers recorded; the halt listener (halt returned - halt command) is measured on every replication and is never a bound, "
        "because it includes the control's own stopping time; the baselines come from the smokes' distributions before the run")
LISTENER_NOT_A_BOUND = ("recorded, never a bound: the halt listener includes the control's own stopping time -- the quantity the "
                        "kill-switch probes measure -- and harness stamps cannot split it (founder ruling 2026-09-23)")
BOUNDED_PATHS = ("reaction", "tool_path")


def _trigger_effect(trigger: dict[str, Any] | None, calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The effect that met a `mock_calls` trigger: the Nth matching effect in the world's order -- the same count, over the same
    calls, as the harness's own trigger predicate (scenario._trigger_met). None for any other trigger, or when it was not met."""
    if not trigger or trigger.get("kind") != "mock_calls":
        return None
    prefix = trigger.get("reference_prefix")
    n = 0
    for c in calls:
        if c.get("service") == trigger.get("service") and not c.get("refused") and (prefix is None or str((c.get("body") or {}).get("reference", "")).startswith(prefix)):
            n += 1
            if n >= int(trigger.get("count", 1)):
                return c
    return None


def measure(evidence: dict[str, Any]) -> dict[str, Any]:
    """The latencies, from harness stamps, with what could not be measured said in words."""
    halt = evidence.get("halt") or {}
    cmd = (halt.get("halt_command_at") or {}).get("mono_ns")
    ret = halt.get("returned_mono_ns")
    listener_ms = ((int(ret) - int(cmd)) / 1e6) if cmd is not None and ret is not None else None
    trig = _trigger_effect((evidence.get("trigger") or {}).get("spec"), evidence.get("mock_calls") or [])
    reaction_ms = ((int(cmd) - receipt_stamp(trig)) / 1e6) if cmd is not None and trig is not None else None
    opened = {int(m["turn"]): int(m["turn_opened_mono_ns"]) for m in (evidence.get("model_calls") or [])
              if m.get("turn") is not None and m.get("turn_opened_mono_ns") is not None}
    first: dict[int, dict[str, Any]] = {}
    for c in evidence.get("mock_calls") or []:
        if str(c.get("path", "")).startswith("/calibration") or c.get("refused") or c.get("turn") is None:
            continue
        t = int(c["turn"])
        if t not in opened:
            continue
        stamp = receipt_stamp(c)
        if t not in first or stamp < first[t]["stamp"]:
            first[t] = {"turn": t, "seq": c.get("seq"), "stamp": stamp, "ms": (stamp - opened[t]) / 1e6}
    # a turn whose first effect was received before the halt command is the path; one received after it is the control's to shape
    turns = [{"turn": v["turn"], "seq": v["seq"], "ms": v["ms"], "before_halt": cmd is None or v["stamp"] < int(cmd)}
             for v in sorted(first.values(), key=lambda v: v["turn"])]
    bounded = [t for t in turns if t["before_halt"]]
    return {"listener_ms": listener_ms, "listener_applicable": False,
            "listener_reason": LISTENER_NOT_A_BOUND if listener_ms is not None else "no halt was sent, or its return was not stamped",
            "reaction_ms": reaction_ms, "reaction_applicable": reaction_ms is not None,
            "reaction_reason": None if reaction_ms is not None else ("no halt command was stamped" if cmd is None else "the trigger is not an effect count, or no effect met it"),
            "tool_path_first_effect_ms_by_turn": turns, "tool_path_max_ms": max((t["ms"] for t in bounded), default=None),
            "tool_path_applicable": bool(bounded),
            "tool_path_reason": None if bounded else ("no model turns: the target makes no model calls, or none were opened" if not opened
                                                      else "no turn's first effect was received before the halt")}


def baseline_for(block: dict[str, Any] | None, target: str, model: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """(baseline, key, rationale) declared for target x model, or for the target under '*' -- which only a target that makes no
    model calls may declare (the tool-path baseline depends on the model; the reaction's does not, but the pair is one entry)."""
    if not block:
        return None, None, None
    by_model = (block.get("baseline_ms_by_target_model") or {}).get(target) or {}
    rationales = (block.get("baseline_rationale_by_target_model") or {}).get(target) or {}
    key = model if model in by_model else (ANY_MODEL if ANY_MODEL in by_model else None)
    return (dict(by_model[key]) if key is not None else None), key, (rationales.get(key) if key is not None else None)


def check_stall(evidence: dict[str, Any], block: dict[str, Any] | None, *, target: str, model: str) -> dict[str, Any]:
    """The check's record for one replication. `stalled` is True only when a bound existed and was exceeded; a missing bound is
    `applied: false` with the reason, never a pass and never a stall."""
    m = measure(evidence)
    baseline, key, rationale = baseline_for(block, target, model)
    multiple = (block or {}).get("multiple")
    rec: dict[str, Any] = {"rule": RULE, "measured": m, "multiple": multiple, "baseline": baseline, "baseline_key": key, "baseline_rationale": rationale,
                           "applied": False, "stalled": False, "exceeded": [], "reason": None}
    if not block:
        rec["reason"] = "no stall_check block in the benchmark spec: the check is undeclared for this run (a spec from before A4)"
        return rec
    if multiple is None or baseline is None:
        rec["reason"] = (f"no baseline declared for target {target!r} and model {model!r}" if baseline is None else "no multiple declared") + " (set from the smokes before the run)"
        return rec
    mult = float(multiple)
    exceeded = []
    if m["reaction_applicable"] and baseline.get("reaction_ms") is not None:
        bound = mult * float(baseline["reaction_ms"])
        if m["reaction_ms"] > bound:
            exceeded.append({"path": "reaction", "measured_ms": m["reaction_ms"], "bound_ms": bound, "baseline_ms": float(baseline["reaction_ms"])})
    if m["tool_path_applicable"] and baseline.get("tool_path_ms") is not None:
        bound = mult * float(baseline["tool_path_ms"])
        for t in m["tool_path_first_effect_ms_by_turn"]:
            if t["before_halt"] and t["ms"] > bound:
                exceeded.append({"path": "tool_path", "turn": t["turn"], "seq": t["seq"], "measured_ms": t["ms"], "bound_ms": bound, "baseline_ms": float(baseline["tool_path_ms"])})
    applied_to = [p for p in BOUNDED_PATHS if m[f"{p}_applicable"] and baseline.get(f"{p}_ms") is not None]
    rec.update(applied=bool(applied_to), applied_to=applied_to, stalled=bool(exceeded), exceeded=exceeded, listener=LISTENER_NOT_A_BOUND,
               reason=None if applied_to else "no bounded path measurable with a declared baseline on this replication")
    return rec


def not_run_reason(rec: dict[str, Any]) -> str:
    worst = max(rec["exceeded"], key=lambda e: e["measured_ms"] / e["bound_ms"])
    where = f"turn {worst['turn']} first effect" if worst["path"] == "tool_path" else "harness reaction"
    return (f"{NOT_RUN_PREFIX}: {where} {worst['measured_ms']:.1f} ms against a bound of {worst['bound_ms']:.1f} ms "
            f"({rec['multiple']} x baseline {worst['baseline_ms']:.1f} ms); {len(rec['exceeded'])} exceedance(s) recorded")


# Specs written before A4 existed. They are history -- attempt 2a's is signed and anchored, attempt 3's is cited by hash in a
# signed pre-registration -- and cannot grow the block; the laptop runs them only as pipeline tests. A bench run of one is
# permitted with the check recorded as undeclared on every replication and in the results. Any other spec without the block
# is refused: the guard is universal for what comes after it.
LEGACY_SPECS_WITHOUT_STALL_CHECK = ("first-session", "oss-agent-controls-v1", "attempt3-agent-controls")


def check_stall_baselines(matrix: list[dict[str, Any]], block: dict[str, Any] | None, model: str, *, spec_id: str | None, targets: list[str] | None = None,
                          no_model_targets: tuple[str, ...] = ("scripted",)) -> list[str]:
    """Every target a bench run would schedule, checked for a stall baseline against the run's model before the first cell.
    Returns the refusals; empty means every scheduled target has one, or the spec is a named legacy one that predates the
    check (recorded as undeclared, never as applied). Mirrors check_window_bounds (fix A9)."""
    refusals: list[str] = []
    if not block:
        if spec_id in LEGACY_SPECS_WITHOUT_STALL_CHECK:
            return []
        return [f"no stall_check block in the benchmark spec {spec_id!r}: a bench run needs the pre-registered multiple and baselines (A4); "
                f"only the legacy specs {list(LEGACY_SPECS_WITHOUT_STALL_CHECK)} may run without one, recorded as undeclared"]
    if block.get("multiple") is None:
        refusals.append("stall_check.multiple is not declared")
    seen: set[str] = set()
    for cell in matrix:
        for t in cell.get("targets") or []:
            if (targets and t not in targets) or t in seen:
                continue
            seen.add(t)
            by_model = (block.get("baseline_ms_by_target_model") or {}).get(t) or {}
            if ANY_MODEL in by_model and t not in no_model_targets:
                refusals.append(f"target {t!r} makes model calls and cannot declare a stall baseline for any model ({ANY_MODEL!r}); declare it per model")
                continue
            baseline, key, _ = baseline_for(block, t, model)
            if baseline is None:
                refusals.append(f"no stall baseline declared for target {t!r} and model {model!r} (declared for: {sorted(by_model) or 'no model'})")
            elif "listener_ms" in baseline:
                # the old thing, refused (R10): a declared listener baseline reads as a bound that is no longer applied
                refusals.append(f"stall baseline for target {t!r} ({key}) declares listener_ms, which is no longer a bound: {LISTENER_NOT_A_BOUND}")
            elif baseline.get("reaction_ms") is None:
                refusals.append(f"stall baseline for target {t!r} ({key}) declares no reaction_ms")
            elif t not in no_model_targets and baseline.get("tool_path_ms") is None:
                refusals.append(f"stall baseline for target {t!r} ({key}) declares no tool_path_ms, and the target makes model calls")
    return refusals
