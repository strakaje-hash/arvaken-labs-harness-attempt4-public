"""C2 (attempt 4) through a real bundle: the operator is resolved at open before any component, chained in run_open, carried in
results and the signed manifest beside the account, and a manifest whose operator moved is refused at sign."""
import json
from pathlib import Path

import pytest

from mark_ledger.keys import KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, key_id
from mark_ledger.store import Ledger
from mark_platform.cli import main
from mark_platform.close_steps import decisive_passes, record_pass_sample_review
from mark_platform.operator import run_open_operator
from mark_platform.redecide import redecide_bundle
from mark_platform.runner import calibrate, close_run, open_run, run_cell

REPO = Path(__file__).resolve().parents[3]
KEYS = REPO / "packages" / "bundles" / "keys"
ROOT_PUB = (KEYS / "root.pub").read_text().strip()
REVOCATIONS = json.loads((KEYS / "revocations.json").read_text()) if (KEYS / "revocations.json").exists() else None
REDECIDE = dict(gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REVOCATIONS, engine_version="test", repo_commit="test")


@pytest.fixture
def run_dir(tmp_path):
    ctx = open_run(tmp_path / "run", "operator-run", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[],
                   operator_probe={"dmi_dir": tmp_path / "no-dmi", "rp_env": tmp_path / "no-rp", "hostname": "laptop-x"})
    try:
        assert ctx.operator["kind"] == "laptop" and ctx.operator["tenant"].startswith("local:")   # resolved before anything else existed
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return tmp_path / "run"


def _sign(tmp_path, run_dir, monkeypatch):
    import mark_platform.cli as cli

    rpriv, rpub = generate_keypair()
    kpriv, kpub = generate_keypair()
    cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": "run-manifest", "not_before": "2026-01-01T00:00:00Z", "not_after": "2027-12-01T00:00:00Z", "root_id": key_id(rpub)}
    (tmp_path / "rm.key").write_text(kpriv)
    (tmp_path / "rm.cert.json").write_text(json.dumps(issue_key_cert(cert, rpriv)))
    root_dir = tmp_path / "keys"
    (root_dir / "packages" / "bundles" / "keys").mkdir(parents=True, exist_ok=True)
    (root_dir / "packages" / "bundles" / "keys" / "root.pub").write_text(rpub)
    monkeypatch.setattr(cli, "REPO_ROOT", root_dir)
    return main(["run", "sign", "--run-dir", str(run_dir), "--key", str(tmp_path / "rm.key"), "--cert", str(tmp_path / "rm.cert.json"), "--anchor", "none"])


def _close_steps(run_dir):
    redecide_bundle(run_dir, **REDECIDE)
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    review = {"reviewer": "test", "method": "raw timelines against the probe's question and against none",
              "cells": {c: {"reading": "earned", "basis": "read", "replications_read": [0]} for c in decisive_passes(results)}}
    record_pass_sample_review(run_dir, review, engine_version="test", repo_commit="test")


def test_the_bundle_carries_the_operator_from_open_to_manifest_and_the_laptop_reads_local(run_dir):
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    op = manifest["operator"]
    assert op["schema"] == "mark.run-operator/1" and op["kind"] == "laptop" and op["resolved"] is True and op["before_first_cell"] is True
    from mark_ledger.canonical import sha256_hex

    assert op["tenant"] == f"local:{sha256_hex('laptop-x')[:12]}" and [f["name"] for f in op["facts"]] == ["hostname_hash"]
    assert results["operator"] == op and manifest["pins"]["permitted_calls_sha256"] == op["permitted_calls"]["declaration"]["sha256"]
    # chained first: the run_open record carries the block as resolved, before the calls made through the run were counted
    opened = run_open_operator(run_dir)
    assert opened is not None and {k: v for k, v in opened.items() if k != "permitted_calls"} == {k: v for k, v in op.items() if k != "permitted_calls"}
    assert opened["permitted_calls"]["declaration"] == op["permitted_calls"]["declaration"]
    led = Ledger(run_dir / "ledger")
    kinds = [r.kind for r in led.records("operator-run")]
    assert kinds[0] == "run_open"
    # the harness reached for the model server through the gate and nothing else (the server at port 9 answered nothing)
    made = op["permitted_calls"]["made"]
    assert set(made) <= {"vllm.models", "vllm.metrics"} and made.get("vllm.models") == 1
    assert (run_dir / "harness-calls.jsonl").exists()
    # the read surface
    from mark_platform.report import report_run

    md = report_run(run_dir)
    assert f"Operator of record: {op['tenant']} (laptop, resolved at open" in md, md


def test_sign_refuses_a_manifest_whose_operator_moved_and_names_the_field(tmp_path, run_dir, monkeypatch):
    _close_steps(run_dir)
    mp = run_dir / "manifest.unsigned.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["operator"]["tenant"] = "aws:123456789012"
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    assert _sign(tmp_path, run_dir, monkeypatch) == 1 and not (run_dir / "manifest.json").exists()
    # and the honest manifest signs
    manifest["operator"]["tenant"] = run_open_operator(run_dir)["tenant"]
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    assert _sign(tmp_path, run_dir, monkeypatch) == 0 and (run_dir / "manifest.json").exists()


def test_redecide_leaves_the_operator_byte_identical(run_dir):
    before = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))["operator"]
    redecide_bundle(run_dir, **REDECIDE)
    after = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))["operator"]
    assert after == before
