"""`platform run sign` refuses a bundle that has not passed its close steps (founder ruling 2026-09-16).

Found on attempt 3: all six bundles were signed although neither the offline re-decision nor the pass-sample review had
run. Both lived only in the plan. The tool now keeps the order: no signature without a `close_redecision` record and a
`pass_sample_review` record of the results.json being signed, in that bundle's own ledger."""
import json
from pathlib import Path

import pytest

from mark_ledger.keys import KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, key_id
from mark_ledger.store import Ledger
from mark_platform.cli import main
from mark_platform.close_steps import CloseStepRefused, decisive_passes, missing_close_steps, record_pass_sample_review
from mark_platform.redecide import redecide_bundle
from mark_platform.runner import calibrate, close_run, open_run, run_cell

REPO = Path(__file__).resolve().parents[3]
KEYS = REPO / "packages" / "bundles" / "keys"
ROOT_PUB = (KEYS / "root.pub").read_text().strip()
REVOCATIONS = json.loads((KEYS / "revocations.json").read_text()) if (KEYS / "revocations.json").exists() else None
REDECIDE = dict(gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REVOCATIONS, engine_version="test", repo_commit="test")


@pytest.fixture
def run_dir(tmp_path):
    ctx = open_run(tmp_path / "run", "close-steps", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return tmp_path / "run"


def _run_manifest_key(tmp_path):
    rpriv, rpub = generate_keypair()
    kpriv, kpub = generate_keypair()
    cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": "run-manifest", "not_before": "2026-01-01T00:00:00Z", "not_after": "2027-12-01T00:00:00Z", "root_id": key_id(rpub)}
    (tmp_path / "rm.key").write_text(kpriv)
    (tmp_path / "rm.cert.json").write_text(json.dumps(issue_key_cert(cert, rpriv)))
    return rpub


def _sign(tmp_path, run_dir, monkeypatch):
    import mark_platform.cli as cli

    rpub = _run_manifest_key(tmp_path)
    root_dir = tmp_path / "keys"
    (root_dir / "packages" / "bundles" / "keys").mkdir(parents=True, exist_ok=True)
    (root_dir / "packages" / "bundles" / "keys" / "root.pub").write_text(rpub)
    monkeypatch.setattr(cli, "REPO_ROOT", root_dir)
    return main(["run", "sign", "--run-dir", str(run_dir), "--key", str(tmp_path / "rm.key"), "--cert", str(tmp_path / "rm.cert.json"), "--anchor", "none"])


def _review(run_dir, reading="earned"):
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    return {"reviewer": "test", "method": "raw timelines against the probe's question and against none",
            "cells": {c: {"reading": reading, "basis": "read", "replications_read": [0]} for c in decisive_passes(results)}}


def _with_one_pass(run_dir):
    """Make the laptop bundle's one row a decisive pass, consistently with its manifest (the fixture has none)."""
    from mark_ledger.canonical import sha256_hex

    rp, mp = run_dir / "results.json", run_dir / "manifest.unsigned.json"
    results = json.loads(rp.read_text(encoding="utf-8"))
    results["results"][0]["verdict"].update(label="pass", decisive=True)
    rp.write_text(json.dumps(results, indent=1), encoding="utf-8")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["evidence"]["results_sha256"] = sha256_hex(rp.read_bytes())
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return results


def test_sign_refuses_a_bundle_with_neither_close_step_and_makes_no_signature(tmp_path, run_dir, monkeypatch):
    assert len(missing_close_steps(run_dir)) == 2
    assert _sign(tmp_path, run_dir, monkeypatch) == 1
    assert not (run_dir / "manifest.json").exists()


def test_sign_refuses_a_bundle_re_decided_but_not_reviewed(tmp_path, run_dir, monkeypatch):
    redecide_bundle(run_dir, **REDECIDE)
    missing = missing_close_steps(run_dir)
    assert len(missing) == 1 and "pass_sample_review" in missing[0]
    assert _sign(tmp_path, run_dir, monkeypatch) == 1
    assert not (run_dir / "manifest.json").exists()


def test_the_review_runs_after_redecide_never_before(run_dir):
    with pytest.raises(CloseStepRefused, match="not been re-decided"):
        record_pass_sample_review(run_dir, _review(run_dir), engine_version="test", repo_commit="test")


def test_sign_proceeds_once_both_steps_are_in_the_ledger(tmp_path, run_dir, monkeypatch):
    redecide_bundle(run_dir, **REDECIDE)
    out = record_pass_sample_review(run_dir, _review(run_dir), engine_version="test", repo_commit="test")
    assert out["ok"] and out["decisive_passes"] == 0
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    led = Ledger(run_dir / "ledger")
    assert led.verify(manifest["evidence"]["chain_id"]).chain_root == manifest["evidence"]["chain_root"]
    assert missing_close_steps(run_dir) == []
    assert _sign(tmp_path, run_dir, monkeypatch) == 0
    assert (run_dir / "manifest.json").exists()
    # and the review refuses a signed bundle, as redecide does
    with pytest.raises(CloseStepRefused, match="already signed"):
        record_pass_sample_review(run_dir, _review(run_dir), engine_version="test", repo_commit="test")


def test_a_review_of_other_results_bytes_does_not_count(tmp_path, run_dir, monkeypatch):
    from mark_ledger.canonical import sha256_hex

    redecide_bundle(run_dir, **REDECIDE)
    record_pass_sample_review(run_dir, _review(run_dir), engine_version="test", repo_commit="test")
    # results.json changed after the review (and the manifest follows, so only the review is stale)
    rp, mp = run_dir / "results.json", run_dir / "manifest.unsigned.json"
    rp.write_text(rp.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["evidence"]["results_sha256"] = sha256_hex(rp.read_bytes())
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    missing = missing_close_steps(run_dir)
    assert len(missing) == 1 and "other results bytes" in missing[0]
    assert _sign(tmp_path, run_dir, monkeypatch) == 1


def test_every_decisive_pass_is_read_and_resolved(run_dir):
    redecide_bundle(run_dir, **REDECIDE)
    results = _with_one_pass(run_dir)
    cell = decisive_passes(results)[0]
    kw = dict(engine_version="test", repo_commit="test")
    with pytest.raises(CloseStepRefused, match="not read"):
        record_pass_sample_review(run_dir, {"reviewer": "t", "method": "m", "cells": {}}, **kw)
    with pytest.raises(CloseStepRefused, match="resolved before it is recorded"):
        record_pass_sample_review(run_dir, _review(run_dir, reading="questionable"), **kw)
    bare = {"reviewer": "t", "method": "m", "cells": {cell: {"reading": "earned", "basis": "", "replications_read": []}}}
    with pytest.raises(CloseStepRefused, match="basis"):
        record_pass_sample_review(run_dir, bare, **kw)
    with pytest.raises(CloseStepRefused, match="not decisive passes"):
        record_pass_sample_review(run_dir, {**_review(run_dir), "cells": {**_review(run_dir)["cells"], "ks.latency/x/y/z": {"reading": "earned", "basis": "b", "replications_read": [0]}}}, **kw)


def test_an_unearned_pass_is_recorded_inside_the_manifest_and_the_verdict_is_not_rewritten(run_dir):
    redecide_bundle(run_dir, **REDECIDE)
    results = _with_one_pass(run_dir)
    cell = decisive_passes(results)[0]
    out = record_pass_sample_review(run_dir, _review(run_dir, reading="unearned"), engine_version="test", repo_commit="test")
    assert out["unearned"] == [cell]
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    assert manifest["environment"]["pass_sample_review"]["unearned"] == [cell]
    after = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert after["results"][0]["verdict"]["label"] == "pass"
    assert missing_close_steps(run_dir) == []
