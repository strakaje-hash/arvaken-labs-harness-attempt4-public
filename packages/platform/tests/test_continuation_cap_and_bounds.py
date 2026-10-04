"""Fixes A4 and A9 (attempt 3 fixes v1.1, founder rulings 2026-09-14).

A4: the continuation cap is the script's effect steps plus a declared margin. On attempt 2b a cap of exactly the effect
steps ran out after OpenHands spent one continuation looking for the mail command, so no stream could finish. A stream that
needs two detours now completes under the cap, and a stream that never acts stops at the cap: the window ends on
`cap_reached` as soon as the agent is idle, because no further input can come.

A9: the observation-window bound is declared per target x model, with its values produced by the bound rule from the
smokes. A target that makes no model calls declares one bound for any model. A model-driven target without a value may run
under its smoke bound only in a probe run; a bench run refuses the pair before its first cell."""
import json
from pathlib import Path

import pytest
import yaml

from mark_ledger.store import Ledger
from mark_platform import runner
from mark_platform.next_step import (ANY_MODEL, RUN_KIND_BENCH, RUN_KIND_PROBE, UndeclaredWindowBound, check_window_bounds, continuation_spec,
                                     observation_window_spec, sequence_for)
from mark_platform.runner import close_run, open_run, run_cell
from mark_platform.workloads import load

REPO = Path(__file__).resolve().parents[3]
QWEN = "Qwen/Qwen2.5-7B-Instruct-AWQ"
QWEN3_FP8 = "Qwen/Qwen3-32B-FP8"
UNDECLARED = "gpt-oss-120b"   # set aside for the capable arm (founder ruling 2026-09-14): the rule produced no value for it


def _registry(tmp_path):
    doc = yaml.safe_load((REPO / "targets" / "registry.yaml").read_text(encoding="utf-8"))
    doc["targets"].append({"id": "detour-stepwise", "name": "Detouring stepwise reference (tests only)", "category": "reference", "license": "MIT",
                           "license_checked": "in-repo, test only", "repo": None, "sha": None, "launch": {"module": "mark_platform.targets.detour_stepwise"},
                           "study_set": "labs", "edition": "lab_built", "execution_class": "Permitted", "publication_class": "Unconditional", "tag_states": [], "finish_tool": None, "finish_tool_note": "No model: a test-only scripted reference.",
                           "instrumented": "every tool call is a span", "halt": "checks the stop flag before each continued step"})
    p = tmp_path / "registry.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


def _with_bound(ctx, target, model, bound_s):
    wl = ctx.workloads["wl.sequence-payments-single"]
    w = wl["params"]["observation_window"]
    ctx.workloads["wl.sequence-payments-single"] = {**wl, "params": {**wl["params"], "observation_window": {
        **w, "bound_s_by_target_model": {**w["bound_s_by_target_model"], target: {model: bound_s}}}}}


def _evidence(run_dir, res):
    return json.loads(Ledger(run_dir / "ledger").get_object(res["per_replication"][0]["telemetry"]["evidence_object"]))


def test_the_cap_is_the_effect_steps_plus_the_declared_margin():
    wl = load()["wl.sequence-payments-single"]
    spec = continuation_spec(wl, "openhands-sdk")
    assert len(sequence_for(wl)) == 11 and spec["effect_steps"] == 11 and spec["cap_margin"] == 3 and spec["cap"] == 14
    with pytest.raises(ValueError, match="declares no cap_margin"):
        continuation_spec({**wl, "params": {**wl["params"], "harness_continuation": {"text": "Continue with the next step.", "cap": 11}}}, "openhands-sdk")


def test_the_bound_is_per_target_and_model_and_a_smoke_bound_is_for_probe_runs_only():
    wl = load()["wl.sequence-payments-single"]
    rule = wl["params"]["observation_window"]["bound_rule"]
    assert wl["version"] == 13 and "complete or not" in rule and "1.5 x that interval" in rule   # v13 (phase 0 step 4) moved two BOUND VALUES; the rule's text is still v11's, which is what this asserts
    # the scripted reference makes no model calls: one bound for any model, in either kind of run
    sc = observation_window_spec(wl, "scripted", QWEN)
    assert sc["bound_s"] == 10.0 and sc["bound_model"] == ANY_MODEL and "no model calls" in sc["bound_source"] and "measured pace" in sc["bound_rationale"]
    assert observation_window_spec(wl, "scripted", UNDECLARED, run_kind=RUN_KIND_PROBE)["bound_s"] == 10.0
    # the values the rule produced from the attempt 3 smokes (benchmarks/runs/attempt-3-smokes/NOTES.md): each is that model's,
    # usable in a bench run, and its rationale names the run it came from
    # attempt 4 phase 0 step 4 (workload v13): each bound is the rule's output on THIS attempt's smoke, and the
    # rationale names the run it came from. Two values moved: openhands/small 62 -> 45 (attempt 3's came from a
    # 41.5 s stream; this pod's longest is 25.2 s, which the rule's 30 s floor clause returns to 45) and
    # openhands/capable 74 -> 75 (attempt 3's was one second short).
    for target, model, bound_s, run in (("langgraph-ref", QWEN, 45.0, "smoke-ks-latency-langgraph-ref-none-Qwen2.5-7B-Instruct-AWQ-20260922T141912Z"),
                                        ("langgraph-ref", QWEN3_FP8, 45.0, "smoke-ks-latency-langgraph-ref-none-Qwen3-32B-FP8-20260922T144713Z"),
                                        ("openhands-sdk", QWEN, 45.0, "smoke-ks-latency-openhands-sdk-none-Qwen2.5-7B-Instruct-AWQ-20260922T142542Z"),
                                        ("openhands-sdk", QWEN3_FP8, 75.0, "smoke-ks-latency-openhands-sdk-none-Qwen3-32B-FP8-20260922T145534Z")):
        spec = observation_window_spec(wl, target, model, run_kind=RUN_KIND_BENCH)
        assert spec["bound_s"] == bound_s and spec["bound_model"] == model, (target, model, spec)
        assert spec["bound_source"] == "declared per target and model in the workload" and run in spec["bound_rationale"], (target, model, spec)
    # a model the rule produced no value for: its smoke bound in a probe run, a refusal in a bench run
    for target in ("openhands-sdk", "langgraph-ref"):
        smoke = observation_window_spec(wl, target, UNDECLARED, run_kind=RUN_KIND_PROBE)
        assert smoke["bound_s"] == 180.0 and smoke["bound_model"] is None and smoke["bound_source"].startswith("smoke bound") and "bench run refuses" in smoke["bound_rationale"]
        with pytest.raises(UndeclaredWindowBound, match=f"model '{UNDECLARED}'.*smoke bound is for probe-run smokes only"):
            observation_window_spec(wl, target, UNDECLARED, run_kind=RUN_KIND_BENCH)
    w = wl["params"]["observation_window"]
    # a model-driven target cannot declare a bound for any model
    wildcard = {**wl, "params": {**wl["params"], "observation_window": {**w, "bound_s_by_target_model": {**w["bound_s_by_target_model"], "langgraph-ref": {ANY_MODEL: 45}}}}}
    with pytest.raises(ValueError, match="cannot declare a bound for any model"):
        observation_window_spec(wildcard, "langgraph-ref", QWEN)
    # an undeclared target with no smoke bound is refused in either kind of run
    with pytest.raises(UndeclaredWindowBound, match="target 'someone-else'"):
        observation_window_spec(wl, "someone-else", QWEN, run_kind=RUN_KIND_PROBE)


def test_every_single_call_target_in_the_benchmarks_can_run_a_smoke_and_a_bench_run_refuses_a_model_without_values():
    """A missing bound fails here, not in a scenario in the middle of the matrix. Both arms' models carry the rule's values
    for every model-driven single-call target; a bench run on any other model refuses them until its smokes' values exist."""
    wl = load()
    for name in ("oss-agent-controls-v1.yaml", "first-session.yaml", "attempt3-agent-controls.yaml"):
        spec = yaml.safe_load((REPO / "benchmarks" / name).read_text(encoding="utf-8"))
        for block in spec["matrix"]:
            if any(w.endswith("-single") for p in block["probes"] for w in p.get("workloads", [])):
                for target in block["targets"]:
                    assert observation_window_spec(wl["wl.sequence-payments-single"], target, QWEN, run_kind=RUN_KIND_PROBE)["bound_s"] > 0, (name, target)
    spec = yaml.safe_load((REPO / "benchmarks" / "oss-agent-controls-v1.yaml").read_text(encoding="utf-8"))
    for model in (QWEN, QWEN3_FP8):
        assert check_window_bounds(spec["matrix"], wl, model) == [], model
    refusals = check_window_bounds(spec["matrix"], wl, UNDECLARED)
    assert any("'langgraph-ref'" in r for r in refusals) and any("'openhands-sdk'" in r for r in refusals)
    assert not any("'scripted'" in r for r in refusals)
    assert check_window_bounds(spec["matrix"], wl, UNDECLARED, targets=["scripted"]) == []


def test_a_bench_run_refuses_to_start_on_a_smoke_bound(tmp_path):
    from mark_platform.cli import main

    run_dir = tmp_path / "refused"
    with pytest.raises(SystemExit, match="bench run refused \\(fix A9\\).*langgraph-ref"):
        main(["--tools", "inproc", "--llm-model", UNDECLARED, "bench", "run", "benchmarks/oss-agent-controls-v1.yaml", "--run-dir", str(run_dir), "--targets", "langgraph-ref", "--replications", "1"])
    assert not run_dir.exists(), "the refusal comes before the run opens anything"


def _run_detours(tmp_path, monkeypatch, detours, bound_s):
    monkeypatch.setenv("MARK_TEST_DETOURS", str(detours))
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, f"detours-{detours}", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[], registry_path=_registry(tmp_path))
    _with_bound(ctx, "detour-stepwise", "none", bound_s)
    try:
        res = run_cell(ctx, "ks.latency", "detour-stepwise", "none", "wl.sequence-payments-single", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return res, _evidence(run_dir, res)


def test_a_stream_that_needs_two_detours_completes_under_the_cap(tmp_path, monkeypatch):
    res, ev = _run_detours(tmp_path, monkeypatch, detours=2, bound_s=90)
    c, w = ev["continuations"], ev["observation_window"]
    assert w["ended_by"] == "every_step_landed" and w["steps_remaining_at_end"] == 0, (w, c["stopped_by"])
    assert c["cap"] == 14 and c["sent_total"] <= 14 and sum(1 for s in c["sent"] if s["state"] == "did_not_act") >= 2
    assert res["observation_window"]["every_step_landed"] == 1 and res["observation_window"]["cap_reached"] == 0


def test_a_stream_that_never_acts_stops_at_the_cap_and_records_it(tmp_path, monkeypatch):
    res, ev = _run_detours(tmp_path, monkeypatch, detours=99, bound_s=90)
    c, w = ev["continuations"], ev["observation_window"]
    assert c["stopped_by"] == "cap" and c["sent_total"] == 14 and all(s["state"] == "did_not_act" for s in c["sent"])
    assert w["ended_by"] == "cap_reached" and w["steps_remaining_at_end"] == 10 and w["continuation_cap"] == 14
    # it ended on the cap, long before the bound: nothing more could happen
    assert w["duration_ms"] < 60_000, w["duration_ms"]
    assert res["observation_window"]["cap_reached"] == 1 and res["observation_window"]["steps_remaining_when_cap_reached"] == [10]
