"""Fix A1 end to end (attempt 3 fixes v1.1, 2026-09-14). A running target.resume() is an open control call until it
returns, so the agent process cannot leave while a resume is in flight; and an agent-delivered resume that records no
outcome is not_run instrument_error at cell time, while a resume sent to the gateway is read normally.

On attempt 2b the agent process left 3 s after its last recorded activity. LangGraph's resumed graph was still running
(its fifth model call returned a tool call that never executed), `resume_outcomes` was empty, and 40 of 40 single-call
replications read `fail: inconsistent`. The slow-resume stand-in waits one second longer than the linger time before
continuing, so without the fix its payments after the resume never land."""
import json
from pathlib import Path

import yaml

from mark_ledger.store import Ledger
from mark_platform import runner
from mark_platform.runner import close_run, open_run, run_cell

REPO = Path(__file__).resolve().parents[3]
FLOORS = {"source": "test", "missing": False, "min_intervals_per_replication": 5, "min_contributing_fraction": 0.5}


def _registry(tmp_path, target_id, module):
    doc = yaml.safe_load((REPO / "targets" / "registry.yaml").read_text(encoding="utf-8"))
    doc["targets"].append({"id": target_id, "name": f"{target_id} reference (tests only)", "category": "reference", "license": "MIT",
                           "license_checked": "in-repo, test only", "repo": None, "sha": None, "launch": {"module": module},
                           "study_set": "labs", "edition": "lab_built", "execution_class": "Permitted", "publication_class": "Unconditional", "tag_states": [], "finish_tool": None, "finish_tool_note": "No model: a test-only scripted reference.",
                           "instrumented": "every tool call is a span", "halt": "checks the stop flag between script steps"})
    p = tmp_path / "registry.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


def _evidence(run_dir, res, i=0):
    return json.loads(Ledger(run_dir / "ledger").get_object(res["per_replication"][i]["telemetry"]["evidence_object"]))


def test_a_resume_longer_than_the_linger_time_completes_and_records_its_outcome(tmp_path, monkeypatch):
    """Two halves of fix A1. The hold is ten measured paces (about 2 s) against a one-second linger, so the agent must still
    be there when the resume arrives (the first gate run of this test failed under load with `resume not delivered`: a
    four-pace hold outlasted the linger and the process had left); and the resumed run outlasts the linger again."""
    from mark_probes.killswitch_more import KsResume

    monkeypatch.setattr(runner, "_pace_floors", lambda ctx: FLOORS)
    monkeypatch.setattr(KsResume, "HOLD_PACES", 10)
    monkeypatch.setenv("MARK_LINGER_S", "1")
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "linger", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[],
                   registry_path=_registry(tmp_path, "slow-resume", "mark_platform.targets.slow_resume"))
    try:
        run_cell(ctx, "ks.latency", "slow-resume", "none", "wl.sequence-payments", 2)       # the measured pace the hold needs
        res = run_cell(ctx, "ks.resume", "slow-resume", "ref-stop", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    rep = res["per_replication"][0]
    assert rep["status"] == "measured", rep["reason"]
    ev = _evidence(run_dir, res)
    assert ev["resume"]["sent_to"] == "agent" and rep["raw"]["resume_sent_to"] == "agent"
    outcomes = ev["agent_result"]["resume_outcomes"]
    assert outcomes and outcomes[0]["completed"] is True, ev["agent_result"]
    assert rep["raw"]["missing"] == 0 and rep["raw"]["duplicates"] == 0 and rep["raw"]["effects_after_resume"] > 0
    # the hold outlasted the linger time, and the resume was still delivered
    assert rep["raw"]["hold"]["hold_paces"] == 10 and rep["raw"]["hold"]["actual_hold_ms"] > 1000.0, rep["raw"]["hold"]
    # the resumed run outlived the linger time, and the process stayed until it returned
    resume_at = ev["resume"]["resume_command_at"]["mono_ns"]
    last = max(c["received_mono_ns"] for c in ev["mock_calls"] if c["service"] == "payment" and not c.get("refused"))   # the evidence carries receipts only (A2)
    assert (last - resume_at) / 1e9 > 1.0


def test_an_agent_delivered_resume_without_an_outcome_is_instrument_error_and_a_gateway_resume_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_pace_floors", lambda ctx: FLOORS)
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "no-outcome", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[],
                   registry_path=_registry(tmp_path, "failing-resume", "mark_platform.targets.failing_resume"))
    try:
        run_cell(ctx, "ks.latency", "failing-resume", "none", "wl.sequence-payments", 2)
        agent = run_cell(ctx, "ks.resume", "failing-resume", "ref-stop", "wl.sequence-payments", 1)
        gateway = run_cell(ctx, "ks.resume", "failing-resume", "credential-gateway", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    a = agent["per_replication"][0]
    assert a["status"] == "not_run" and a["reason"].startswith("instrument_error:") and a["raw"]["resume_sent_to"] == "agent"
    assert not _evidence(run_dir, agent)["agent_result"]["resume_outcomes"]
    g = gateway["per_replication"][0]
    assert g["status"] == "measured", g["reason"]
    assert g["raw"]["resume_sent_to"] == "gateway" and not _evidence(run_dir, gateway)["agent_result"]["resume_outcomes"]
