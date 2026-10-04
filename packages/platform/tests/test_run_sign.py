"""The laptop-side signing path: the pod closes a run unsigned; `platform run sign` signs where the key lives,
refuses a tampered bundle, records signed_on, and anchors (anchor disabled here: no network in tests)."""
import json
from pathlib import Path

from mark_ledger.keys import KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, key_id
from mark_ledger.manifest import read_signed
from mark_platform.cli import main
from mark_platform.runner import calibrate, close_run, open_run, run_cell


def _run_manifest_key(tmp_path):
    rpriv, rpub = generate_keypair()
    kpriv, kpub = generate_keypair()
    cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": "run-manifest", "not_before": "2026-01-01T00:00:00Z", "not_after": "2027-12-01T00:00:00Z", "root_id": key_id(rpub)}
    signed = issue_key_cert(cert, rpriv)
    (tmp_path / "rm.key").write_text(kpriv)
    (tmp_path / "rm.cert.json").write_text(json.dumps(signed))
    return rpub


def test_unsigned_close_then_laptop_sign(tmp_path, monkeypatch):
    ctx = open_run(tmp_path / "run", "sign-run", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        out = close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    assert out["manifest"] is None and out["manifest_signature"] == "unsigned" and (tmp_path / "run" / "manifest.unsigned.json").exists()
    # the close steps sign refuses to run without (founder ruling 2026-09-16; test_close_steps_before_sign.py)
    from mark_platform.close_steps import record_pass_sample_review
    from mark_platform.redecide import redecide_bundle

    repo = Path(__file__).resolve().parents[3]
    keys = repo / "packages" / "bundles" / "keys"
    rev = keys / "revocations.json"
    redecide_bundle(tmp_path / "run", gates_dir=repo / "gates", root_public_hex=(keys / "root.pub").read_text().strip(),
                    revocations=json.loads(rev.read_text()) if rev.exists() else None, engine_version="test", repo_commit="test")
    record_pass_sample_review(tmp_path / "run", {"reviewer": "test", "method": "none needed: no decisive pass", "cells": {}}, engine_version="test", repo_commit="test")
    rpub = _run_manifest_key(tmp_path)
    # the verifier's root is the repo's; point it at the test root by monkeypatching the file read
    import mark_platform.cli as cli

    root_dir = tmp_path / "keys"
    (root_dir / "packages" / "bundles" / "keys").mkdir(parents=True)
    (root_dir / "packages" / "bundles" / "keys" / "root.pub").write_text(rpub)
    monkeypatch.setattr(cli, "REPO_ROOT", root_dir)
    rc = main(["run", "sign", "--run-dir", str(tmp_path / "run"), "--key", str(tmp_path / "rm.key"), "--cert", str(tmp_path / "rm.cert.json"), "--anchor", "none"])
    assert rc == 0
    so = read_signed(tmp_path / "run" / "manifest.json")
    assert so.object["signed_on"] == "laptop" and so.key_cert["cert"]["purpose"] == "run-manifest"
    assert (tmp_path / "run" / "report.md").read_text(encoding="utf-8").count("run-manifest key") == 1
    # a tampered results.json is refused before any signature is made
    rp = tmp_path / "run" / "results.json"
    rp.write_text(rp.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    (tmp_path / "run" / "manifest.json").unlink()
    assert main(["run", "sign", "--run-dir", str(tmp_path / "run"), "--key", str(tmp_path / "rm.key"), "--cert", str(tmp_path / "rm.cert.json"), "--anchor", "none"]) == 1
    assert not (tmp_path / "run" / "manifest.json").exists()
