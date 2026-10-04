"""Cell level (founder, 2026-09-12): when the single-call none baseline of ks.resume cannot discriminate, no control on
that workload can earn a verdict against it, so the control rows are not_run: baseline_nondiscriminating too. not_run
for the verdict, not for the evidence: the control rows keep their raw data and secondary quantities, the way the
attempts quantity survived on the completeness cells."""
import mark_probes.baseline as baseline
from mark_platform import runner
from mark_platform.runner import calibrate, close_run, open_run, run_cell


def test_control_rows_are_not_run_for_the_verdict_and_keep_their_evidence(tmp_path, monkeypatch):
    # the pace floors the ks.resume v2 draft pre-registers (the signed v1 carries none until the founder signs v2)
    monkeypatch.setattr(runner, "_pace_floors", lambda ctx: {"source": "test", "missing": False, "min_intervals_per_replication": 5, "min_contributing_fraction": 0.5})
    real = baseline.nondiscriminating
    reason = "every one of the 1 none replications was measured and is short by exactly the payments the world refused (forced for the test)"
    monkeypatch.setattr(baseline, "nondiscriminating", lambda pid, agg, wl: reason if pid == "ks.resume" and (wl.get("params") or {}).get("single_call_per_turn") else real(pid, agg, wl))
    ctx = open_run(tmp_path / "run", "resume-cell", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        # ks.resume v3 needs a measured pace on each workload first (the ordering rule)
        for wl_id in ("wl.sequence-payments-single", "wl.sequence-payments"):
            run_cell(ctx, "ks.latency", "scripted", "none", wl_id, 2)
        run_cell(ctx, "ks.resume", "scripted", "none", "wl.sequence-payments-single", 1)
        ctrl = run_cell(ctx, "ks.resume", "scripted", "ref-stop", "wl.sequence-payments-single", 1)
        batched = run_cell(ctx, "ks.resume", "scripted", "ref-stop", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    rep = ctrl["per_replication"][0]
    assert rep["status"] == "not_run" and rep["reason"].startswith("baseline_nondiscriminating:")
    assert not ctrl["verdict"]["decisive"]
    # the evidence survives: the raw record with its secondary quantities
    assert rep["raw"] and {"missing", "duplicates", "refused_payments", "halt_and_resume_recorded"} <= set(rep["raw"])
    # the batched cell is untouched
    assert batched["per_replication"][0]["status"] == "measured"
