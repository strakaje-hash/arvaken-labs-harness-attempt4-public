"""Model integrity at read time (founder ruling 2026-09-12): a bundle measured before the model proxy existed has the
rule applied through re-decision.
- A replication whose agent records show a model error becomes not_run model_error, its prior status kept.
- Its row is re-aggregated and re-decided by the probe's own code.
- The ledger records every changed replication and the hash of every agent log read, and still verifies.
- A workload whose repo definition no longer matches the row's hash is refused."""
import json
from pathlib import Path

import pytest

from mark_ledger.canonical import sha256_hex
from mark_ledger.store import Ledger
from mark_platform.redecide import RedecideRefused, redecide_bundle
from mark_platform.runner import calibrate, close_run, open_run, run_cell

REPO = Path(__file__).resolve().parents[3]
KEYS = REPO / "packages" / "bundles" / "keys"
CUT_REPLY = "Message from Agent ─────\n\n<tool_call>\n{\"name\": \"terminal\"}\n</tool_call>\n<tool_call>\n{\"name\": \"\nTokens: ↑ input 8.14K (total 14.1K) • ↓ output 47 (total 528)\n"


def _pre_proxy_bundle(tmp_path, monkeypatch):
    """A laptop bundle whose results carry no model_proxy summary, as a run before the model-integrity check."""
    from mark_platform import runner

    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "read-time", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 2)
        ctx.model_proxy.stop()
        ctx.model_proxy = None   # the pre-proxy runner: no model_proxy summary in results
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return run_dir


def _kw():
    rev = KEYS / "revocations.json"
    return dict(gates_dir=REPO / "gates", root_public_hex=(KEYS / "root.pub").read_text().strip(), revocations=json.loads(rev.read_text()) if rev.exists() else None,
                engine_version="test", repo_commit="test", max_model_len=8192)


def test_a_replication_with_a_model_error_in_its_agent_log_becomes_not_run_and_its_row_is_redecided(tmp_path, monkeypatch):
    run_dir = _pre_proxy_bundle(tmp_path, monkeypatch)
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results.get("model_proxy") is None
    row = next(r for r in results["results"] if r["probe"]["id"] == "ks.completeness")
    victim = row["per_replication"][0]
    measured_before = row["replications"]["measured"]
    log = run_dir / "scenarios" / victim["scenario_id"] / "agent.stdout"
    log.write_text(CUT_REPLY, encoding="utf-8")

    out = redecide_bundle(run_dir, **_kw())

    after = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    r2 = next(r for r in after["results"] if r["probe"]["id"] == "ks.completeness")
    p = next(x for x in r2["per_replication"] if x["scenario_id"] == victim["scenario_id"])
    assert p["status"] == "not_run" and p["reason"].startswith("model_error: truncated_at_context_limit (read time")
    assert p["raw"]["status_before_model_error"] == victim["status"] and p["raw"]["model_read_time"]["error_class"] == "truncated_at_context_limit"
    assert r2["replications"]["measured"] == measured_before - (1 if victim["status"] == "measured" else 0)
    assert "verdict_before_model_integrity" in r2 and "aggregate_before_model_integrity" in r2
    mp = after["model_integrity_read_time"]
    assert mp["max_model_len"] == 8192 and mp["max_model_len_source"].startswith("supplied at read time")
    assert f"scenarios/{victim['scenario_id']}/agent.stdout" in mp["agent_logs_read_sha256"]
    assert out["model_integrity_read_time"]["by_class"] == {"truncated_at_context_limit": 1}
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(run_dir / "ledger")
    v = led.verify(chain)
    assert v.ok and v.chain_root == manifest["evidence"]["chain_root"]
    assert sha256_hex((run_dir / "results.json").read_bytes()) == manifest["evidence"]["results_sha256"]
    rec = json.loads(led.get_object([x for x in led.records(chain)][-1].content_hash))
    assert rec["model_integrity_read_time"]["replications_changed"][0]["scenario_id"] == victim["scenario_id"]


def test_a_workload_whose_definition_changed_since_the_run_is_refused(tmp_path, monkeypatch):
    from mark_platform.workloads import load

    run_dir = _pre_proxy_bundle(tmp_path, monkeypatch)
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    victim = next(r for r in results["results"] if r["probe"]["id"] == "ks.completeness")["per_replication"][0]
    (run_dir / "scenarios" / victim["scenario_id"] / "agent.stdout").write_text(CUT_REPLY, encoding="utf-8")
    changed = load()
    changed["wl.batch-payments"] = {**changed["wl.batch-payments"], "task": "a different task text"}
    with pytest.raises(RedecideRefused, match="hash mismatch"):
        redecide_bundle(run_dir, **_kw(), workloads=changed)


def test_a_run_with_the_model_proxy_is_not_classified_again_at_read_time(tmp_path):
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "with-proxy", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        res = run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    assert res["per_replication"][0]["raw"]["model"]["observed"] is True and res["per_replication"][0]["raw"]["model"]["calls"] == 0
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["model_proxy"]["calls"] == 0
    out = redecide_bundle(run_dir, **_kw())
    assert out["model_integrity_read_time"] is None
