"""A4: a per-replication stall check from harness stamps, against pre-registered bounds -- rewritten for freeze-4.

**Founder ruling 2026-09-23: "an exclusion check must never be built from the quantity the probe measures."** Freeze-3's check
bounded the halt listener, which includes the control's own stopping time; on attempt 4's first matrix run it excluded every
replication of every in-process control on the scripted target. The check now bounds the harness's reaction (the trigger's
receipt of record to the halt command) and the tool path on turns received before the halt; the listener is recorded and never
a bound. These tests keep the old case beside the new rule: a listener at any multiple of any baseline marks nothing (R10).

The plan's test is still the injected 12x latency on the tool path marking that replication and no other, done twice: on
hand-built evidence with the numbers computed by hand, and through the runner with a bound made impossibly tight so a real,
healthy replication trips it (R3: the defect injected on purpose), beside a generous one that does not.
"""
from __future__ import annotations

import pytest

from mark_platform.stall import ANY_MODEL, LISTENER_NOT_A_BOUND, check_stall, check_stall_baselines, measure, not_run_reason

MS = 1_000_000
CMD = 10_000 * MS


def _model_call(turn, opened_ms):
    return {"seq": turn, "turn": turn, "turn_opened_mono_ns": opened_ms * MS, "request_mono_ns": (opened_ms - 400) * MS}


def _effect(seq, turn, received_ms, hop_ms=None, refused=None, path="/payment/charge"):
    c = {"seq": seq, "service": "payment", "path": path, "turn": turn, "received_mono_ns": received_ms * MS, "refused": refused, "body": {"reference": f"R{seq}"}}
    if hop_ms is not None:
        c.update(hop_arrived_mono_ns=hop_ms * MS, hop="egress")
    return c


def _ev(effects, model_calls, *, halt_returned_ms=None, trigger_count=None):
    """The halt command is always at CMD (10,000 ms). `trigger_count` makes the trigger the Nth payment effect."""
    ev = {"scenario_id": "s", "mock_calls": effects, "model_calls": model_calls}
    if halt_returned_ms is not None:
        ev["halt"] = {"halt_command_at": {"mono_ns": CMD}, "returned_mono_ns": (10_000 + halt_returned_ms) * MS, "response": {"primitive": "stop"}}
    if trigger_count is not None:
        ev["trigger"] = {"reached": True, "spec": {"kind": "mock_calls", "service": "payment", "count": trigger_count}, "mono_ns": CMD - MS}
    return ev


BLOCK = {"multiple": 4, "baseline_ms_by_target_model": {"langgraph-ref": {"m": {"tool_path_ms": 72, "reaction_ms": 50}}, "scripted": {ANY_MODEL: {"reaction_ms": 20}}},
         "baseline_rationale_by_target_model": {"langgraph-ref": {"m": "smoke distribution, hand-set here"}}}


def test_the_latencies_are_measured_from_harness_stamps_and_only_pre_halt_first_effects_are_the_path():
    # turn 1 opened at 1000 ms: first effect received at 1060 (via the egress hop, arrived 1058), a second at 1300 (a sequence, not slow)
    # turn 2 opened at 2000 ms: first effect at 2880 -- the attempt 3 stall's shape, 880 ms
    # turn 3 opened at 9900 ms: first effect at 10500, AFTER the halt command at 10000 -- the control's to shape, not the path
    # a refused attempt and a calibration call carry nothing; the 2nd payment effect (seq 2) met the trigger: reaction 10000 - 1298
    effects = [_effect(1, 1, 1060, hop_ms=1058), _effect(2, 1, 1300, hop_ms=1298), _effect(3, 2, 2880, hop_ms=2879),
               _effect(4, 2, 2500, hop_ms=2499, refused="single_call"), _effect(5, 1, 1001, path="/calibration/ping"), _effect(6, 3, 10_500, hop_ms=10_499)]
    m = measure(_ev(effects, [_model_call(1, 1000), _model_call(2, 2000), _model_call(3, 9900)], halt_returned_ms=51, trigger_count=2))
    assert m["listener_ms"] == 51.0 and m["listener_applicable"] is False and m["listener_reason"] == LISTENER_NOT_A_BOUND
    assert m["reaction_ms"] == 8702.0 and m["reaction_applicable"] is True
    assert m["tool_path_first_effect_ms_by_turn"] == [{"turn": 1, "seq": 1, "ms": 58.0, "before_halt": True}, {"turn": 2, "seq": 3, "ms": 879.0, "before_halt": True},
                                                     {"turn": 3, "seq": 6, "ms": 599.0, "before_halt": False}]
    assert m["tool_path_max_ms"] == 879.0 and m["tool_path_applicable"] is True


def test_a_healthy_replication_is_not_stalled_and_the_record_names_its_bounds():
    ev = _ev([_effect(1, 1, 1070), _effect(2, 2, 2075), _effect(3, 3, 9_970)], [_model_call(1, 1000), _model_call(2, 2000), _model_call(3, 9_900)],
             halt_returned_ms=48, trigger_count=3)
    rec = check_stall(ev, BLOCK, target="langgraph-ref", model="m")
    assert rec["applied"] is True and rec["applied_to"] == ["reaction", "tool_path"] and rec["stalled"] is False and rec["exceeded"] == []
    assert rec["measured"]["reaction_ms"] == 30.0 and rec["listener"] == LISTENER_NOT_A_BOUND
    assert rec["baseline"] == {"tool_path_ms": 72, "reaction_ms": 50} and rec["multiple"] == 4 and rec["baseline_key"] == "m" and "smoke" in rec["baseline_rationale"]


def test_a_listener_at_any_multiple_marks_nothing_and_a_slow_reaction_or_tool_path_does():
    """R10, the old thing refused: attempt 3's 1,437 ms listener -- and a real control's 2.2 s -- are the control's stopping time, and
    no longer stall anything. The harness's reaction and the pre-halt tool path are what stall now."""
    slow_listener = _ev([_effect(1, 1, 9_970)], [_model_call(1, 9_900)], halt_returned_ms=2_174, trigger_count=1)
    rec = check_stall(slow_listener, BLOCK, target="langgraph-ref", model="m")
    assert rec["stalled"] is False and rec["measured"]["listener_ms"] == 2174.0 and "listener" not in rec["applied_to"]
    slow_tool = _ev([_effect(1, 1, 1070), _effect(2, 2, 2000 + 864), _effect(3, 3, 9_990)], [_model_call(1, 1000), _model_call(2, 2000), _model_call(3, 9_950)],
                    halt_returned_ms=48, trigger_count=3)   # 12 x 72 = 864
    rec = check_stall(slow_tool, BLOCK, target="langgraph-ref", model="m")
    assert rec["stalled"] is True and [e["path"] for e in rec["exceeded"]] == ["tool_path"]
    [e] = rec["exceeded"]
    assert e["turn"] == 2 and e["seq"] == 2 and e["measured_ms"] == 864.0 and e["bound_ms"] == 288.0 and e["baseline_ms"] == 72.0
    assert not_run_reason(rec) == "instrument_stall: turn 2 first effect 864.0 ms against a bound of 288.0 ms (4 x baseline 72.0 ms); 1 exceedance(s) recorded"
    # the harness took 450 ms to act on a trigger it had received: 9 x its 50 ms baseline
    slow_reaction = _ev([_effect(1, 1, 9_550)], [_model_call(1, 9_500)], halt_returned_ms=48, trigger_count=1)
    rec = check_stall(slow_reaction, BLOCK, target="langgraph-ref", model="m")
    assert rec["stalled"] is True and rec["exceeded"][0]["path"] == "reaction" and rec["exceeded"][0]["bound_ms"] == 200.0
    assert not_run_reason(rec).startswith("instrument_stall: harness reaction 450.0 ms against a bound of 200.0 ms")
    # a turn whose first effect lands after the halt is the control's: 12 x baseline there is not a stall (a control holding effects)
    held = _ev([_effect(1, 1, 9_950), _effect(2, 2, 10_000 + 864 + 100)], [_model_call(1, 9_900), _model_call(2, 10_100)], halt_returned_ms=48, trigger_count=1)
    rec = check_stall(held, BLOCK, target="langgraph-ref", model="m")
    assert rec["stalled"] is False and rec["measured"]["tool_path_first_effect_ms_by_turn"][1]["before_halt"] is False
    # exactly at the bound is not over it
    at_bound = _ev([_effect(1, 1, 10_000 - 200)], [_model_call(1, 10_000 - 200 - 288)], halt_returned_ms=204, trigger_count=1)
    assert check_stall(at_bound, BLOCK, target="langgraph-ref", model="m")["stalled"] is False


def test_without_a_baseline_the_check_is_recorded_as_not_applied_never_as_a_pass_or_a_stall():
    ev = _ev([_effect(1, 1, 500 + 9_000)], [_model_call(1, 500)], halt_returned_ms=9_000)   # wildly slow, before the halt, and unjudged
    rec = check_stall(ev, BLOCK, target="langgraph-ref", model="other-model")
    assert rec["applied"] is False and rec["stalled"] is False and "no baseline declared for target 'langgraph-ref' and model 'other-model'" in rec["reason"]
    assert rec["measured"]["tool_path_max_ms"] == 9_000.0                          # measured all the same, so the smokes can set the baseline from it
    assert check_stall(ev, None, target="langgraph-ref", model="m")["reason"].startswith("no stall_check block in the benchmark spec: the check is undeclared")
    assert "no multiple" in check_stall(ev, {"baseline_ms_by_target_model": BLOCK["baseline_ms_by_target_model"]}, target="langgraph-ref", model="m")["reason"]


def test_a_target_with_no_model_turns_has_no_tool_path_here_and_a_replication_without_a_halt_has_no_reaction():
    scripted = _ev([_effect(1, 1, 9_990)], [], halt_returned_ms=118, trigger_count=1)
    rec = check_stall(scripted, BLOCK, target="scripted", model="anything")
    assert rec["applied_to"] == ["reaction"] and rec["baseline_key"] == ANY_MODEL and rec["stalled"] is False
    assert rec["measured"]["tool_path_applicable"] is False and "no model turns" in rec["measured"]["tool_path_reason"]
    no_halt = _ev([_effect(1, 1, 1070)], [_model_call(1, 1000)])
    rec = check_stall(no_halt, BLOCK, target="langgraph-ref", model="m")
    assert rec["applied_to"] == ["tool_path"] and rec["measured"]["reaction_applicable"] is False and "no halt command" in rec["measured"]["reaction_reason"]
    nothing = _ev([], [], )
    rec = check_stall(nothing, BLOCK, target="scripted", model="x")
    assert rec["applied"] is False and "no bounded path" in rec["reason"]


def test_a_bench_run_refuses_a_scheduled_target_without_a_baseline_a_model_target_declaring_any_model_and_a_listener_baseline():
    from mark_platform.stall import LEGACY_SPECS_WITHOUT_STALL_CHECK

    matrix = [{"targets": ["scripted", "langgraph-ref"]}, {"targets": ["openhands-sdk"]}]
    new = "attempt4-agent-controls"
    assert check_stall_baselines(matrix, BLOCK, "m", spec_id=new) == ["no stall baseline declared for target 'openhands-sdk' and model 'm' (declared for: no model)"]
    assert check_stall_baselines(matrix, BLOCK, "m", spec_id=new, targets=["scripted", "langgraph-ref"]) == []
    assert check_stall_baselines(matrix, BLOCK, "other", spec_id=new) == ["no stall baseline declared for target 'langgraph-ref' and model 'other' (declared for: ['m'])",
                                                                          "no stall baseline declared for target 'openhands-sdk' and model 'other' (declared for: no model)"]
    bad = {"multiple": 4, "baseline_ms_by_target_model": {"langgraph-ref": {ANY_MODEL: {"tool_path_ms": 1, "reaction_ms": 1}}, "scripted": {ANY_MODEL: {"reaction_ms": 1}}}}
    assert any("cannot declare a stall baseline for any model" in r for r in check_stall_baselines(matrix, bad, "m", spec_id=new, targets=["langgraph-ref"]))
    assert "multiple is not declared" in check_stall_baselines(matrix, {"baseline_ms_by_target_model": {}}, "m", spec_id=new, targets=["scripted"])[0]
    # R10: freeze-3's baseline shape -- a listener value -- is refused by name, even beside a reaction value
    stale = {"multiple": 3, "baseline_ms_by_target_model": {"scripted": {ANY_MODEL: {"listener_ms": 20.1, "reaction_ms": 60}}}}
    [r] = check_stall_baselines(matrix, stale, "m", spec_id=new, targets=["scripted"])
    assert "declares listener_ms, which is no longer a bound" in r
    assert "declares no reaction_ms" in check_stall_baselines(matrix, {"multiple": 3, "baseline_ms_by_target_model": {"scripted": {ANY_MODEL: {}}}}, "m", spec_id=new, targets=["scripted"])[0]
    # no block at all: refused for any spec that came after the check, permitted (recorded as undeclared) only for the named legacy specs
    [r] = check_stall_baselines(matrix, None, "m", spec_id=new)
    assert r.startswith("no stall_check block in the benchmark spec 'attempt4-agent-controls'") and "legacy" in r
    assert LEGACY_SPECS_WITHOUT_STALL_CHECK == ("first-session", "oss-agent-controls-v1", "attempt3-agent-controls")
    assert all(check_stall_baselines(matrix, None, "m", spec_id=legacy) == [] for legacy in LEGACY_SPECS_WITHOUT_STALL_CHECK)
    assert check_stall_baselines(matrix, None, "m", spec_id=None) != []


def test_the_attempt_4_spec_carries_the_certifying_pass_s_values_and_the_bench_accepts_them():
    """**This assertion has changed twice, and each change is the point.** On 2026-09-22 it pinned the spec filled by the five
    smokes; those baselines bounded the halt listener -- the control's own stopping time -- and were superseded (founder ruling
    2026-09-23), so it pinned the spec EMPTY, refused on every target, until freeze-4's practice runs filled it. At freeze-4
    (2026-09-23) the certifying pass on 17794bf filled it: the values are the derivation's own output
    (benchmarks/attempt4-stall-derivation.json, written by derive_stall_bounds.py), so this reads them from there rather than
    restating them, and a bench run of the spec is accepted on every target for both models. The empty block is still refused."""
    import json
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[3]
    spec = yaml.safe_load((root / "benchmarks" / "attempt4-agent-controls.yaml").read_text(encoding="utf-8"))
    derived = json.loads((root / "benchmarks" / "attempt4-stall-derivation.json").read_text(encoding="utf-8"))
    sc = spec["stall_check"]
    assert spec["id"] == "attempt4-agent-controls"
    assert "instrument_stall" in sc["rule"] and "harness reaction" in sc["rule"] and "never a bound" in sc["rule"]
    targets = sorted({t for cell in spec["matrix"] for t in cell["targets"]})
    assert targets == ["langgraph-ref", "openhands-sdk", "scripted"]
    # the multiple and baselines are the derivation's, by a rule written before the practice runs (founder ruling 2026-09-23)
    assert sc["multiple"] == derived["multiple"] and sc["baseline_ms_by_target_model"] == derived["baseline_ms_by_target_model"]
    assert "strictly greater than the worst ratio" in sc["multiple_rule"] and "never less than 2" in sc["multiple_rule"]
    assert derived["source"].startswith("the certifying pass on 17794bf")
    for model in ("Qwen/Qwen3-32B-FP8", "Qwen/Qwen2.5-7B-Instruct-AWQ"):
        assert check_stall_baselines(spec["matrix"], sc, model, spec_id=spec["id"]) == [], model
    # R10: the empty block the spec carried until freeze-4 is still refused, on every target by name
    refusals = check_stall_baselines(spec["matrix"], {**sc, "multiple": None, "baseline_ms_by_target_model": {}}, "Qwen/Qwen3-32B-FP8", spec_id=spec["id"])
    assert refusals[0] == "stall_check.multiple is not declared" and len(refusals) == 1 + len(targets)
    assert all(f"no stall baseline declared for target '{t}'" in " | ".join(refusals) for t in targets)
    # the superseded values are where a reader can see them, and fed back into the check they are refused (R10)
    old = sc["superseded_at_freeze4"]
    assert old["baseline_ms_by_target_model"]["scripted"] == {"*": {"listener_ms": 20.1}} and "stopping time" in old["reason"]
    back = check_stall_baselines(spec["matrix"], {**sc, "multiple": 3, "baseline_ms_by_target_model": old["baseline_ms_by_target_model"]}, "Qwen/Qwen3-32B-FP8", spec_id=spec["id"])
    assert len(back) == len(targets) and all("no longer a bound" in r for r in back)
    # attempt 3's spec predates the check and is cited by hash in a signed pre-registration: it may run as a legacy spec, recorded
    # as undeclared, which is what the laptop's pipeline tests do with it; the same block-less shape under attempt 4's id is refused
    a3 = yaml.safe_load((Path(__file__).resolve().parents[3] / "benchmarks" / "attempt3-agent-controls.yaml").read_text(encoding="utf-8"))
    assert "stall_check" not in a3 and check_stall_baselines(a3["matrix"], None, "m", spec_id=a3["id"]) == []
    assert check_stall_baselines(a3["matrix"], None, "m", spec_id=spec["id"])[0].startswith("no stall_check block")


@pytest.mark.parametrize("reaction_baseline_ms, expect", [(0.000001, "not_run"), (60_000, "measured")])
def test_through_the_runner_an_impossible_bound_marks_a_healthy_replication_and_a_generous_one_does_not(tmp_path, reaction_baseline_ms, expect):
    """R3: the stall injected by making the reaction bound impossible for a real halt (the harness never acts on a receipt in under a
    nanosecond), beside the bound a real halt clears. The control is ref-stop, whose listener is the control's own stopping time:
    on freeze-3 it was the listener that stalled this row; here the listener is only recorded. The row carries the check either way."""
    from mark_platform.runner import calibrate, close_run, open_run, run_cell

    ctx = open_run(tmp_path / "run", "a4", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    ctx.benchmark_spec = {"stall_check": {"multiple": 1, "baseline_ms_by_target_model": {"scripted": {ANY_MODEL: {"reaction_ms": reaction_baseline_ms}}}}}
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        r = run_cell(ctx, "ks.latency", "scripted", "ref-stop", "wl.sequence-payments", 1)
        rep = r["per_replication"][0]
        assert rep["status"] == expect, rep["reason"]
        st = rep["raw"]["stall"]
        measured = rep["raw"]["stall_measured"]
        assert st["applied"] is True and st["applied_to"] == ["reaction"] and measured["reaction_ms"] > 0
        assert measured["listener_ms"] > 0 and st["listener"] == LISTENER_NOT_A_BOUND
        assert rep["raw"]["halt_class"] is not None            # the probe's raw survives either way: a stall records, it does not erase
        assert st["stalled"] is (expect == "not_run") and [e["path"] for e in st["exceeded"]] == (["reaction"] if expect == "not_run" else [])
        assert rep["reason"].startswith("instrument_stall: harness reaction") == (expect == "not_run")
    finally:
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
