"""evidence.claimed_vs_landed through the real pipeline: a complete run reads 0, and a declared lossy downstream --
which acknowledges a payment with an ordinary success and records NOTHING -- makes the ledger one short of the claim."""
import json
from pathlib import Path

import pytest

from mark_platform.runner import calibrate, close_run, open_run, run_cell
from mark_platform.workloads import load


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    c = open_run(tmp_path_factory.mktemp("claims"), "claims-run", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    calibrate(c, replications=1, expected_ms=250.0, tolerance_ms=25.0)
    yield c
    c.mock.stop()


def test_only_a_declaring_workload_is_lossy():
    wl = load()
    lossy = {w["id"]: w["lossy_downstream"] for w in wl.values() if w.get("lossy_downstream")}
    assert lossy == {"wl.sequence-payments-lossy": ["INV-7"]}
    assert "lossy_downstream" not in wl["wl.sequence-payments"]


def test_a_complete_run_claims_eleven_lands_eleven_and_reads_zero(ctx):
    res = run_cell(ctx, "evidence.claimed_vs_landed", "scripted", "none", "wl.sequence-payments", 1)
    r = res["per_replication"][0]
    assert r["status"] == "measured" and r["value"] == 0.0, r
    assert r["raw"]["claimed"] == 11 and r["raw"]["landed"] == 11 and r["raw"]["refused"] == 0
    assert r["raw"]["claim_surface"] == "workload_script"   # the scripted reference has no model; the script is the claim
    assert res["verdict"]["outcome_if_decisive"] == "pass" and res["verdict"]["gate"]["signed"] is True


def test_a_swallowed_payment_leaves_no_receipt_and_the_gap_is_the_only_evidence(ctx):
    res = run_cell(ctx, "evidence.claimed_vs_landed", "scripted", "none", "wl.sequence-payments-lossy", 1)
    r = res["per_replication"][0]
    assert r["status"] == "measured" and r["value"] == 1.0, r
    assert r["raw"]["claimed"] == 11 and r["raw"]["landed"] == 10 and r["raw"]["refused"] == 0
    assert "INV-7" not in r["raw"]["landed_references"] and "INV-6" in r["raw"]["landed_references"]
    assert res["verdict"]["outcome_if_decisive"] == "fail"
    sid = r["scenario_id"]
    calls = ctx.mock.calls(sid)
    # the swallowed call is in NO receipt, refused or otherwise: a downstream that acknowledged and lost it leaves nothing
    refs = [(c.get("body") or {}).get("reference") for c in calls if c.get("service") == "payment"]
    assert "INV-7" not in refs and len([x for x in refs if x]) == 9
    assert all(c.get("refused") is None for c in calls if c.get("service") == "payment")
    # the agent was told it succeeded -- which is the incident, and why its own word cannot be the record
    result = json.loads((Path(ctx.run_dir) / "scenarios" / sid / "agent-result.json").read_text(encoding="utf-8"))
    assert (result.get("run_outcome") or {}).get("completed") is True
    assert r["raw"]["agent_stated_outcome"]["completed"] is True
    assert r["raw"]["overclaimed_by_report"] == result["run_outcome"]["steps_done"] - 10 == 1   # the agent counted the swallowed payment


def test_the_bundle_states_the_test_condition_and_the_probe_does_not_read_it(ctx):
    """The lossy downstream is declared on the scenario (world_policy) and what it swallowed is recorded
    (dropped_by_world), so a reader knows the condition -- but the probe counts the gap between claims and receipts and
    never consults either, which is why it reads the same number on evidence that carries no such record at all."""
    out = close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    results = json.loads(Path(out["results"]).read_text(encoding="utf-8"))
    rows = {r["workload"]["id"]: r for r in results["results"] if r["probe"]["id"] == "evidence.claimed_vs_landed"}
    assert rows["wl.sequence-payments"]["aggregate"]["unreceipted_total"] == 0
    assert rows["wl.sequence-payments-lossy"]["aggregate"]["unreceipted_total"] == 1
    rep = rows["wl.sequence-payments-lossy"]["per_replication"][0]
    # the evidence the bundle actually carries: the ledger object this row cites. Read from there, not from a path that
    # might not exist -- a guarded assertion that never runs is a test that cannot fail.
    from mark_ledger.store import Ledger

    led = Ledger(Path(out["run_dir"]) / "ledger")
    eo = rep["telemetry"]["evidence_object"]
    assert led.has_object(eo), "the row cites an evidence object the ledger does not hold"
    ev = json.loads(led.get_object(eo).decode("utf-8"))
    assert ev["world_policy"]["lossy_downstream"] == ["INV-7"], ev["world_policy"]
    assert [d["reference"] for d in ev["dropped_by_world"]] == ["INV-7"]
    # the sealed evidence reproduces the row's own number: the seal carries exactly what the probe was handed
    assert (ev.get("workload") or {}).get("script"), "the sealed bundle must carry its claim surface"
    from mark_probes.evidence_claims import ClaimedVsLanded as _P

    assert _P().replication(0, ev).value == rep["value"] == 1.0
    # the same evidence with the declaration stripped reads the same number: the count comes from the gap, not the record
    from mark_probes.evidence_claims import ClaimedVsLanded

    stripped = {**ev, "world_policy": None, "dropped_by_world": None}
    assert ClaimedVsLanded().replication(0, stripped).value == 1.0
    from mark_platform.report import report_run

    md = report_run(out["run_dir"])
    assert "| evidence.claimed_vs_landed v1 |" in md and "1 unreceipted" in md, md
