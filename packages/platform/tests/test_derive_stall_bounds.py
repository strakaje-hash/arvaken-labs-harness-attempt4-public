"""The stall multiple and baselines are produced by a rule written before the practice runs (founder ruling 2026-09-23).

Every expected value here is computed by hand from the numbers in the fixture, never by the function under test.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("derive_stall_bounds", Path(__file__).resolve().parents[1] / "scripts" / "derive_stall_bounds.py")
dsb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dsb)


def _row(target, control, reps):
    return {"target": {"id": target}, "control": {"id": control}, "per_replication": reps}


def _rep(reaction=None, turns=()):
    return {"scenario_id": "s", "status": "measured", "raw": {"stall_measured": {"reaction_ms": reaction, "tool_path_first_effect_ms_by_turn": list(turns)}}}


def _turn(ms, before=True, turn=1):
    return {"turn": turn, "seq": turn, "ms": ms, "before_halt": before}


def test_the_rule_on_numbers_worked_by_hand():
    # langgraph-ref on model m, two controls pooled: reactions 50, 60, 70, 80 -> median 65; worst 80/65 = 1.23
    # tool path, pre-halt: 600, 700, 1300 -> median 700; worst 1300/700 = 1.857; the post-halt 9000 is the control's and is not an observation
    results = {"results": [_row("langgraph-ref", "none", [_rep(50, [_turn(600)]), _rep(60, [_turn(700), _turn(9000, before=False, turn=2)])]),
                           _row("langgraph-ref", "agt-kill-switch", [_rep(70, [_turn(1300)]), _rep(80)])]}
    out = dsb.derive(dsb.observations(results, "m"))
    assert out["baseline_ms_by_target_model"] == {"langgraph-ref": {"m": {"reaction_ms": 65.0, "tool_path_ms": 700.0}}}
    assert out["observations_by_target_model"] == {"langgraph-ref": {"m": {"reaction": 4, "tool_path": 3}}}
    assert out["worst"]["path"] == "tool_path" and out["worst"]["control"] == "agt-kill-switch" and out["worst"]["ms"] == 1300.0
    assert round(out["worst"]["ratio"], 3) == 1.857
    assert out["multiple"] == 2          # smallest whole number strictly above 1.857 is 2, and the floor is 2


@pytest.mark.parametrize("worst_ms, expect", [(100.0, 2), (150.0, 2), (200.0, 3), (202.0, 3), (399.0, 4)])
def test_strictly_above_the_worst_ratio_and_never_below_two(worst_ms, expect):
    # median of (100, 100, worst) is 100, so the worst ratio is worst_ms / 100: 1.0 -> 2 (floor), 1.5 -> 2, 2.0 -> 3 (strictly
    # above), 2.02 -> 3 (freeze-3's case, now produced rather than chosen), 3.99 -> 4
    results = {"results": [_row("scripted", "none", [_rep(100.0), _rep(100.0), _rep(worst_ms)])]}
    assert dsb.derive(dsb.observations(results, "any-model-served"))["multiple"] == expect


def test_a_target_that_makes_no_model_calls_gets_one_baseline_for_any_model():
    a = {"results": [_row("scripted", "none", [_rep(10.0)])]}
    b = {"results": [_row("scripted", "ref-stop", [_rep(30.0)])]}
    obs = dsb.observations(a, "Qwen/Qwen3-32B-FP8") + dsb.observations(b, "Qwen/Qwen2.5-7B-Instruct-AWQ")
    out = dsb.derive(obs)
    assert out["baseline_ms_by_target_model"] == {"scripted": {"*": {"reaction_ms": 20.0}}}


def test_the_listener_is_never_an_observation_and_nothing_to_derive_is_refused():
    """R10: freeze-3's quantity, however large, does not reach the rule. And no observations is an error, not a multiple of 2."""
    results = {"results": [_row("scripted", "ref-stop", [{"scenario_id": "s", "raw": {"stall_measured": {"listener_ms": 2174.0, "reaction_ms": None}}}])]}
    assert dsb.observations(results, "m") == []
    with pytest.raises(ValueError, match="no observations"):
        dsb.derive([])
