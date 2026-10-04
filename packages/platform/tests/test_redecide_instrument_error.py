"""Fix A10 (ruling 3, 2026-09-14): redecide applies fix A1 at read time, with the probe's own rule, to a bundle measured
before the probe applied it. Attempt 2b's LangGraph single-call ks.resume rows read `fail: inconsistent` because the agent
process killed the resumed graph; they become not_run instrument_error. It changes exactly the agent-delivered resumes
with no recorded outcome, leaves a gateway resume (empty by design) untouched, re-decides the rows it changes, keeps the
bundle's integrity checks true, and refuses a second pass and a signed bundle."""
import json
from pathlib import Path

import pytest
import yaml

from mark_ledger.canonical import sha256_hex
from mark_ledger.store import Ledger
from mark_platform import runner
from mark_platform.redecide import RedecideRefused, redecide_bundle
from mark_platform.runner import close_run, open_run, run_cell

REPO = Path(__file__).resolve().parents[3]
KEYS = REPO / "packages" / "bundles" / "keys"
ROOT_PUB = (KEYS / "root.pub").read_text().strip()
REVOCATIONS = json.loads((KEYS / "revocations.json").read_text()) if (KEYS / "revocations.json").exists() else None
FLOORS = {"source": "test", "missing": False, "min_intervals_per_replication": 5, "min_contributing_fraction": 0.5}


def _registry(tmp_path):
    doc = yaml.safe_load((REPO / "targets" / "registry.yaml").read_text(encoding="utf-8"))
    doc["targets"].append({"id": "failing-resume", "name": "failing-resume reference (tests only)", "category": "reference", "license": "MIT",
                           "license_checked": "in-repo, test only", "repo": None, "sha": None, "launch": {"module": "mark_platform.targets.failing_resume"},
                           "study_set": "labs", "edition": "lab_built", "execution_class": "Permitted", "publication_class": "Unconditional", "tag_states": [], "finish_tool": None, "finish_tool_note": "No model: a test-only scripted reference.",
                           "instrumented": "every tool call is a span", "halt": "checks the stop flag between script steps"})
    p = tmp_path / "registry.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


@pytest.fixture
def pre_a1_bundle(tmp_path, monkeypatch):
    """A laptop bundle closed as a runner before fix A1 would have closed it: the probe's rule is switched off while it runs."""
    import mark_probes.killswitch_more as ksm

    monkeypatch.setattr(runner, "_pace_floors", lambda ctx: FLOORS)
    monkeypatch.setattr(ksm, "resume_outcome_missing", lambda evidence: None)
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "pre-a1", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[], registry_path=_registry(tmp_path))
    try:
        run_cell(ctx, "ks.latency", "failing-resume", "none", "wl.sequence-payments", 2)
        run_cell(ctx, "ks.resume", "failing-resume", "ref-stop", "wl.sequence-payments", 1)
        run_cell(ctx, "ks.resume", "failing-resume", "credential-gateway", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    monkeypatch.undo()
    return run_dir


def _resume_rows(run_dir):
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    return results, {r["control"]["id"]: r for r in results["results"] if r["probe"]["id"] == "ks.resume"}


def _redecide(run_dir):
    return redecide_bundle(run_dir, gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REVOCATIONS, engine_version="test", repo_commit="test")


def test_redecide_marks_exactly_the_agent_delivered_resumes_without_an_outcome(pre_a1_bundle):
    _, rows = _resume_rows(pre_a1_bundle)
    assert rows["ref-stop"]["per_replication"][0]["status"] == "measured", "the pre-A1 runner read the killed resume"
    gateway_before = rows["credential-gateway"]["per_replication"][0]["status"]

    out = _redecide(pre_a1_bundle)
    assert out["ok"] and out["resume_instrument_error"]["replications_changed"] == 1

    results, rows = _resume_rows(pre_a1_bundle)
    stop = rows["ref-stop"]
    p = stop["per_replication"][0]
    assert p["status"] == "not_run" and p["reason"].startswith("instrument_error:") and "read time" in p["reason"]
    assert p["raw"]["status_before_instrument_error"] == "measured" and p["value"] is None
    assert stop["replications"]["measured"] == 0 and not stop["verdict"]["decisive"]
    assert "verdict_before_instrument_error" in stop and "aggregate_before_instrument_error" in stop
    # the gateway resume never reached the agent: untouched
    gw = rows["credential-gateway"]
    assert gw["per_replication"][0]["status"] == gateway_before and "verdict_before_instrument_error" not in gw
    assert results["resume_instrument_error"]["defect"] == "A1"

    # the checks `platform run sign` makes before it signs still hold, and the ledger names the pass
    manifest = json.loads((pre_a1_bundle / "manifest.unsigned.json").read_text(encoding="utf-8"))
    led = Ledger(pre_a1_bundle / "ledger")
    chain = manifest["evidence"]["chain_id"]
    v = led.verify(chain)
    assert v.ok and v.chain_root == manifest["evidence"]["chain_root"]
    assert sha256_hex((pre_a1_bundle / "results.json").read_bytes()) == manifest["evidence"]["results_sha256"]
    # the pass is in the re-decided results (above); the ledger's last record is the re-decision that carries it
    assert [r.kind for r in led.records(chain)][-1] == "close_redecision"

    with pytest.raises(RedecideRefused, match="already re-decided"):
        _redecide(pre_a1_bundle)


def test_a_bundle_measured_with_fix_a1_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_pace_floors", lambda ctx: FLOORS)
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "post-a1", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[], registry_path=_registry(tmp_path))
    try:
        run_cell(ctx, "ks.latency", "failing-resume", "none", "wl.sequence-payments", 2)
        run_cell(ctx, "ks.resume", "failing-resume", "ref-stop", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    _, rows = _resume_rows(run_dir)
    assert rows["ref-stop"]["per_replication"][0]["reason"].startswith("instrument_error:"), "fix A1 applied at cell time"
    out = _redecide(run_dir)
    assert out["resume_instrument_error"]["replications_changed"] == 0 and out["resume_instrument_error"]["rows_changed"] == []
