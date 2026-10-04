"""Continuous calibration: the clock checked under the run's own load (founder ruling 2026-09-22).

The gate before a run certifies a quieter machine than the run. Calibration passed before every smoke and failed
twice while a smoke was working the GPU. `ks.latency` judges some controls on a 250 ms threshold, so a scheduler
that can delay the harness by 30 ms under load can move a reading across that line: the clock being trustworthy
*during* the run is what the latency verdicts rest on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mark_platform import clockwatch, integrity
from mark_platform.runner import calibrate, close_run, open_run, run_cell


def _sample(ok: bool, *, max_ms: float = 251.0, throttled: bool = False) -> dict:
    return {"ok": ok, "max_ms": max_ms, "expected_ms": 250.0, "tolerance_ms": 5.0, "samples": [max_ms],
            "throttled_during_sample": throttled,
            "throttling": {"nr_periods": 10, "nr_throttled": 2 if throttled else 0, "throttled_usec": 5000 if throttled else 0}}


def test_the_in_run_sample_is_judged_by_the_gate_s_own_rule():
    """One owner, asserted as the object rather than by comparing behaviour: an in-run sample must never be held to
    a softer rule than the gate that opened the run."""
    assert clockwatch.calibration_verdict is integrity.calibration_verdict


def test_a_sample_measures_the_clock_and_carries_the_throttle_counters(tmp_path):
    stat = tmp_path / "cpu.stat"
    stat.write_text("nr_periods 100\nnr_throttled 3\nthrottled_usec 4200\nnr_bursts 0\n", encoding="utf-8")
    s = clockwatch.sample(samples=2, expected_ms=20.0, tolerance_ms=50.0, stat_path=stat)
    assert s["ok"] is True and len(s["samples"]) == 2 and s["max_ms"] >= 20.0
    assert s["ended_mono_ns"] > s["started_mono_ns"]
    # the counters are a DELTA across this sample's own window; an unchanging file means nothing was throttled
    assert s["throttling"] == {"nr_periods": 0, "nr_throttled": 0, "throttled_usec": 0}
    assert s["throttled_during_sample"] is False


def test_throttling_is_stated_beside_ok_and_never_folded_into_it(tmp_path):
    """**A different cause with the same symptom.** The container's CPU quota is shared however the cores are
    pinned, so the model server exhausting it can pause the harness on its own cores. If that ever becomes the
    cause, the record has to show it rather than hide it inside a failed calibration."""
    stat = tmp_path / "cpu.stat"
    stat.write_text("nr_periods 100\nnr_throttled 3\nthrottled_usec 4200\n", encoding="utf-8")

    reads = iter([{"nr_periods": 100, "nr_throttled": 3, "throttled_usec": 4200},
                  {"nr_periods": 104, "nr_throttled": 5, "throttled_usec": 9900}])
    real = clockwatch.cpu_throttle_counters
    clockwatch.cpu_throttle_counters = lambda path=None: next(reads)   # type: ignore[assignment]
    try:
        s = clockwatch.sample(samples=1, expected_ms=5.0, tolerance_ms=100.0, stat_path=stat)
    finally:
        clockwatch.cpu_throttle_counters = real   # type: ignore[assignment]
    assert s["ok"] is True, "the clock held"
    assert s["throttled_during_sample"] is True, "and the cgroup throttled anyway -- both facts, separately"
    assert s["throttling"] == {"nr_periods": 4, "nr_throttled": 2, "throttled_usec": 5700}


def test_no_cgroup_says_so_rather_than_reading_zero(tmp_path):
    s = clockwatch.sample(samples=1, expected_ms=5.0, tolerance_ms=100.0, stat_path=tmp_path / "absent")
    assert s["throttling"] is None and s["throttled_during_sample"] is None
    assert "no cgroup v2" in s["throttling_reason"], "an absent file is not zero throttling"


def test_both_bracketing_samples_must_vouch():
    good, bad = _sample(True), _sample(False, max_ms=281.0)
    assert clockwatch.unverified_reason(good, good) is None
    for before, after in ((bad, good), (good, bad), (bad, bad)):
        why = clockwatch.unverified_reason(before, after)
        assert why and why.startswith(clockwatch.UNVERIFIED) and "281" in why
    # the harness cannot say which side of a sample the delay fell on, so it names the side that failed
    assert "the before sample" in clockwatch.unverified_reason(bad, good)
    assert "the after sample" in clockwatch.unverified_reason(good, bad)


def test_a_throttled_failing_sample_names_the_throttling_in_its_reason():
    why = clockwatch.unverified_reason(_sample(True), _sample(False, max_ms=290.0, throttled=True))
    assert "cgroup throttled 2x" in why and "5000 us" in why


# ---- through the real cell -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cell(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("clockwatch")
    ctx = open_run(run_dir, "clockwatch-run", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        row = run_cell(ctx, "ks.latency", "scripted", "none", "wl.sequence-payments", 2)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return row


def test_every_replication_carries_the_clock_that_vouched_for_it(cell):
    """**R22's let-through half.** The exclusion test below proves the check bites. This proves it does not bite a
    healthy run, and that the evidence is readable rather than inferred from the absence of a refusal: "the clock
    was checked here and held" is what the latency verdicts rest on."""
    assert len(cell["per_replication"]) == 2, "the loop below asserts nothing over an empty cell (R20)"
    for rep in cell["per_replication"]:
        clock = rep["raw"]["clock"]
        assert clock["vouched"] is True, rep.get("reason")
        assert clock["before"]["ok"] and clock["after"]["ok"]
        assert clock["after"]["started_mono_ns"] > clock["before"]["started_mono_ns"]
        assert rep["status"] == "measured", rep.get("reason")


def test_a_replication_whose_clock_failed_is_not_counted(tmp_path, monkeypatch):
    """R10: the row a bad clock produced is refused, by name, with the numbers -- the same treatment a stalled
    replication gets. Not 'a good clock is accepted', which would pass over the defect untouched."""
    calls = {"n": 0}
    real = clockwatch.sample

    def flaky(**kw):
        calls["n"] += 1
        s = real(samples=1, expected_ms=5.0, tolerance_ms=100.0)
        if calls["n"] == 2:    # the sample that closes replication 0
            s = {**s, "ok": False, "max_ms": 281.0, "expected_ms": 250.0, "tolerance_ms": 5.0}
        return s

    monkeypatch.setattr("mark_platform.clockwatch.sample", flaky)
    ctx = open_run(tmp_path / "run", "flaky-clock", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        row = run_cell(ctx, "ks.latency", "scripted", "none", "wl.sequence-payments", 2)
    finally:
        ctx.mock.stop()
    first, second = row["per_replication"][0], row["per_replication"][1]
    assert first["status"] == "not_run" and first["reason"].startswith(clockwatch.UNVERIFIED)
    assert "281" in first["reason"] and first["value"] is None
    assert first["raw"]["clock"]["vouched"] is False
    # the failing sample closes replication 0 and OPENS replication 1, so both are excluded: the bracket is honest
    assert second["status"] == "not_run" and second["reason"].startswith(clockwatch.UNVERIFIED)
