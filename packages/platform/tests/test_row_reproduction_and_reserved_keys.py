"""Two properties every probe must have, tested over a real bundle (founder rulings 2026-09-21).

**Reproduction.** A sealed row's verdict must not depend on anything the seal did not carry: seal, re-read from the
ledger, re-decide, compare. Found by `evidence.claimed_vs_landed`, whose first draft read the scripted claim surface
from `workload` -- which the runner strips before sealing -- so the sealed row re-read as `not_run` and the bundle
could not reproduce its own number.

**Reserved keys.** The runner writes several keys onto every replication's `raw` after the probe returns it. A probe
writing one has its field silently replaced, which happened to that same probe's `self_report`. The collision is
refused at cell time now, and this file drives the refusal.
"""
import json
from pathlib import Path

import pytest

from mark_probes import PROBES
from mark_probes.base import Replication
from mark_platform.runner import RESERVED_RAW_KEYS, ReservedRawKey, calibrate, check_reserved_raw, close_run, open_run, run_cell

CELLS = [
    ("evidence.claimed_vs_landed", "none", "wl.sequence-payments-lossy"),
    ("scope.side_channel", "none", "wl.sequence-payments-sidechannel"),
    ("gate.bypass_path", "none", "wl.sequence-payments-canary"),
    ("ks.latency", "ref-stop", "wl.sequence-payments"),
    ("ks.completeness", "ref-cancel", "wl.batch-payments"),
]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("repro")
    ctx = open_run(run_dir, "repro-run", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        for probe, control, workload in CELLS:
            run_cell(ctx, probe, "scripted", control, workload, 1)
        out = close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return out


def test_every_sealed_row_re_reads_to_its_own_value(bundle):
    """Seal, re-read, re-decide, compare -- for every row of every probe in the bundle. A row that re-reads to a
    different value, or to not_run, depends on something the seal did not carry."""
    from mark_ledger.store import Ledger

    results = json.loads(Path(bundle["results"]).read_text(encoding="utf-8"))
    led = Ledger(Path(bundle["run_dir"]) / "ledger")
    rows = [r for r in results["results"] if r["probe"]["id"] in {p for p, _, _ in CELLS}]
    assert len(rows) == len(CELLS), [r["probe"]["id"] for r in rows]
    checked = 0
    for row in rows:
        probe = PROBES[row["probe"]["id"]]()
        for rep in row["per_replication"]:
            eo = (rep.get("telemetry") or {}).get("evidence_object")
            assert eo and led.has_object(eo), f"{row['probe']['id']}: the row cites no evidence object the ledger holds"
            ev = json.loads(led.get_object(eo).decode("utf-8"))
            again = probe.replication(rep["index"], ev)
            assert again.status == rep["status"], f"{row['probe']['id']} rep {rep['index']}: sealed re-read is {again.status}, the row is {rep['status']} ({again.reason})"
            assert again.value == rep["value"], f"{row['probe']['id']} rep {rep['index']}: sealed re-read gives {again.value}, the row says {rep['value']}"
            checked += 1
    assert checked >= len(CELLS)


def test_the_runner_refuses_a_probe_that_writes_a_key_it_owns():
    """R10 on the collision: the runner's own keys, written by a probe, are refused rather than silently replaced."""
    assert set(RESERVED_RAW_KEYS) >= {"stall", "stall_measured", "self_report"}
    check_reserved_raw("p", {"claimed": 1, "landed": 1})   # an ordinary probe field: fine
    check_reserved_raw("p", None)
    for key in RESERVED_RAW_KEYS:
        with pytest.raises(ReservedRawKey, match=f"writes raw key\\(s\\) the runner owns"):
            check_reserved_raw("some.probe", {key: "mine"})
    # the message names every clashing key and who owns it, so the author knows what to rename
    with pytest.raises(ReservedRawKey) as e:
        check_reserved_raw("some.probe", {"self_report": 1, "stall": 2, "fine": 3})
    assert "'self_report'" in str(e.value) and "'stall'" in str(e.value) and "fine" not in str(e.value)
    assert "A2: the self-report consistency record" in str(e.value)


def test_no_registered_probe_writes_a_reserved_key_on_a_real_bundle(bundle):
    """The rows the bundle actually produced: every probe's own fields survived, and the runner's are beside them."""
    results = json.loads(Path(bundle["results"]).read_text(encoding="utf-8"))
    measured = 0
    for row in results["results"]:
        for rep in row["per_replication"]:
            raw = rep.get("raw") or {}
            # the runner's keys are present on a measured row, and they are the runner's values, not a probe's
            if rep["status"] == "measured":
                assert isinstance(raw.get("self_report"), dict) and "consistent" in raw["self_report"], (row["probe"]["id"], raw.get("self_report"))
                assert isinstance(raw.get("stall"), dict), row["probe"]["id"]
                measured += 1
    assert measured >= len(CELLS), f"only {measured} measured row(s): the runner's keys were checked on almost nothing"


def test_a_probe_whose_raw_collides_is_refused_through_run_cell(tmp_path, monkeypatch):
    """Through the runner, not just the helper: a probe that writes `self_report` never reaches a row."""
    import mark_probes

    victim = PROBES["scope.side_channel"]

    class Colliding(victim):   # type: ignore[misc, valid-type]
        def replication(self, index, evidence):
            rep = super().replication(index, evidence)
            return Replication(rep.index, rep.scenario_id, rep.status, rep.reason, rep.value, {**(rep.raw or {}), "self_report": "mine"}, rep.telemetry)

    monkeypatch.setitem(mark_probes.PROBES, "scope.side_channel", Colliding)
    ctx = open_run(tmp_path / "run", "collide", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        with pytest.raises(ReservedRawKey, match="scope.side_channel writes raw key"):
            run_cell(ctx, "scope.side_channel", "scripted", "none", "wl.sequence-payments", 1)
    finally:
        ctx.mock.stop()


def test_the_runners_own_key_on_the_model_error_path_is_not_blamed_on_the_probe(tmp_path, monkeypatch):
    """**The case this guard was missing, and it cost a smoke.**

    `not_run_reason_before_model_error` is one of the four keys the runner reserves, and the runner writes it
    itself -- on the model-error path only. The check sat *after* that write, so on any replication with a model
    error it found the runner's own key in `raw`, raised, and named the probe. langgraph-ref never tripped it
    because nothing there errored; openhands-sdk died on it at the first model error (2026-09-22), which attempt 3
    recorded as a routine occurrence on that target, 7 replications in 20.

    The guard is placed where `rep.raw` is the probe's and nothing else's, so it can only ever accuse the probe of
    something the probe did. This test drives the model-error path and asserts the cell RECORDS rather than raises:
    the earlier tests all assert the refusal, and a guard whose refusals are tested and whose non-refusal is not
    can fire on its own author's work with every suite green."""
    import mark_platform.runner as runner_mod

    real = runner_mod.model_integrity

    def erroring(calls, max_len, settled, halt=None):
        mi = real(calls, max_len, settled, halt)
        mi["error_class"] = "http_error"
        mi["errors"] = [{"detail": "forced by the test, to reach the branch that writes the runner's reserved key"}]
        return mi

    monkeypatch.setattr(runner_mod, "model_integrity", erroring)
    ctx = open_run(tmp_path / "run", "modelerr", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        row = run_cell(ctx, "ks.latency", "scripted", "none", "wl.sequence-payments", 1)   # must not raise
    finally:
        ctx.mock.stop()
    rep = row["per_replication"][0]
    assert rep["status"] == "not_run" and rep["reason"].startswith("model_error: http_error")
    # the runner's key is present on the row -- which is exactly what the misplaced check was seeing
    assert "not_run_reason_before_model_error" in rep["raw"]
    assert "not_run_reason_before_model_error" in RESERVED_RAW_KEYS
