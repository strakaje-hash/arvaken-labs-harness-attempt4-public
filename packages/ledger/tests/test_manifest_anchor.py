import base64
import json
from datetime import datetime, timezone

import pytest

from mark_ledger.anchor import LocalOnlyAnchor, RekorAnchor, check_receipt
from mark_ledger.keys import KEY_CERT_SCHEMA, KeyCertError, generate_keypair, issue_key_cert, key_id
from mark_ledger.manifest import build_manifest, read_signed, sign_manifest, verify_manifest, write_signed
from mark_ledger.store import Ledger, Provenance

PINS = {"image_digest": "sha256:" + "a" * 64, "lockfile_sha256": "b" * 64, "engine_version": "0.1.0", "repo_commit": "c" * 40, "model_hashes": {"qwen": "d" * 64}}
ENV = {"gpu": "test", "driver": "0"}


def _keys():
    rpriv, rpub = generate_keypair()
    kpriv, kpub = generate_keypair()
    cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": "cloak-bundle", "not_before": "2026-01-01T00:00:00Z", "not_after": "2027-12-01T00:00:00Z", "root_id": key_id(rpub)}
    return rpriv, rpub, kpriv, issue_key_cert(cert, rpriv)


def test_manifest_round_trips_and_binds_the_chain_root(tmp_path):
    rpriv, rpub, kpriv, kcert = _keys()
    led = Ledger(tmp_path / "ledger")
    rec = led.append("run-x", "probe_result", {"v": 1}, Provenance("0.1.0", "fp", "test"))
    m = build_manifest(run_id="run-x", benchmark=None, pins=PINS, environment=ENV, started_at="2026-09-11T00:00:00Z", ended_at="2026-09-11T00:01:00Z", chain_id="run-x", chain_root=led.chain_root("run-x"), results_hash="e" * 64)
    signed = sign_manifest(m, kpriv, kcert)
    p = write_signed(tmp_path / "manifest.json", signed)
    back = read_signed(p)
    out = verify_manifest(back, rpub)
    assert out["evidence"]["chain_root"] == rec.hash
    # tamper with a pin after signing
    d = json.loads(p.read_text())
    d["object"]["pins"]["model_hashes"]["qwen"] = "f" * 64
    p.write_text(json.dumps(d))
    with pytest.raises(KeyCertError) as e:
        verify_manifest(read_signed(p), rpub)
    assert e.value.reason == "signature"


def test_manifest_without_pins_is_refused():
    with pytest.raises(ValueError):
        build_manifest(run_id="r", benchmark=None, pins={"engine_version": "1"}, environment={}, started_at="x", ended_at=None, chain_id="r", chain_root=None, results_hash=None)


def test_rekor_client_and_offline_receipt_check(tmp_path):
    import hashlib

    from mark_ledger.anchor import ed25519_public_pem

    rpriv, rpub, kpriv, kcert = _keys()
    led = Ledger(tmp_path / "ledger")
    led.append("run-y", "probe_result", {"v": 1}, Provenance("0.1.0", "fp", "test"))
    root = led.chain_root("run-y")
    calls = []

    def fake_transport(method, url, body, raw, headers):
        # Rekor canonicalises a rekord entry: inline content becomes sha256(content); signature + key are kept
        calls.append((method, url, body))
        content = base64.b64decode(body["spec"]["data"]["content"])
        canon = {"apiVersion": "0.0.1", "kind": "rekord", "spec": {"data": {"hash": {"algorithm": "sha256", "value": hashlib.sha256(content).hexdigest()}}, "signature": body["spec"]["signature"]}}
        return 201, json.dumps({"24296fb24b8ad77a" + "0" * 48: {"logIndex": 42, "integratedTime": 1757548800, "logID": "c0d2", "body": base64.b64encode(json.dumps(canon).encode()).decode()}}).encode()

    client = RekorAnchor(kpriv, ed25519_public_pem(kpriv), transport=fake_transport)
    anc = client.anchor("run-y", root)
    assert calls[0][1].endswith("/api/v1/log/entries") and calls[0][2]["kind"] == "rekord" and base64.b64decode(calls[0][2]["spec"]["data"]["content"]) == RekorAnchor.artifact(root)
    assert anc.anchored and anc.receipt["logIndex"] == 42
    led.record_anchor(anc.to_json())
    v = led.verify("run-y")
    assert v.ok and v.anchors[0]["ok"] and "log inclusion not checked offline" in v.anchors[0]["detail"]
    # a receipt for a different root is caught offline, and so is a forged signature
    ok, detail = check_receipt({**anc.to_json(), "chain_root": "0" * 64})
    assert not ok and "differs" in detail
    body = json.loads(base64.b64decode(anc.receipt["body"]))
    body["spec"]["signature"]["content"] = base64.b64encode(b"x" * 64).decode()
    ok, detail = check_receipt({**anc.to_json(), "receipt": {**anc.receipt, "body": base64.b64encode(json.dumps(body).encode()).decode()}})
    assert not ok and "signature invalid" in detail


def test_local_only_anchor_is_not_anchored():
    a = LocalOnlyAnchor().anchor("c", "0" * 64)
    assert a.anchored is False and a.authority == "local-only"
    ok, detail = check_receipt(a.to_json())
    assert ok and "not an external anchor" in detail


def test_manifest_purposes_run_manifest_preferred_bundle_key_provisional(tmp_path):
    from mark_ledger.manifest import manifest_signature_status

    rpriv, rpub = generate_keypair()
    kpriv, kpub = generate_keypair()
    m = build_manifest(run_id="r", benchmark=None, pins=PINS, environment=ENV, started_at="x", ended_at=None, chain_id="r", chain_root=None, results_hash=None)
    for purpose, expect in (("run-manifest", "run-manifest"), ("cloak-bundle", "provisional (cloak-bundle key)")):
        cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": purpose, "not_before": "2026-01-01T00:00:00Z", "not_after": "2027-12-01T00:00:00Z", "root_id": key_id(rpub)}
        so = sign_manifest(m, kpriv, issue_key_cert(cert, rpriv))
        assert verify_manifest(so, rpub)["run_id"] == "r"
        assert manifest_signature_status(so) == expect
    gate_cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": "gate", "not_before": "2026-01-01T00:00:00Z", "not_after": "2027-12-01T00:00:00Z", "root_id": key_id(rpub)}
    with pytest.raises(KeyCertError):
        verify_manifest(sign_manifest(m, kpriv, issue_key_cert(gate_cert, rpriv)), rpub)



def test_rekor_409_is_the_same_proof_not_a_failure():
    """Rekor answers 409 when the log already holds an identical entry, which is the proof anchoring wanted. A
    retry of `run sign --anchor rekor` must therefore succeed and record the existing entry, flagged as
    pre-existing; it raised instead, so any retry failed outright (dress rehearsal, 2026-09-12)."""
    import json as _json

    from mark_ledger.anchor import RekorAnchor
    from mark_ledger.anchor import ed25519_public_pem
    from mark_ledger.keys import generate_keypair

    priv, _ = generate_keypair()
    root = "c" * 64
    uuid = "108e9186" + "a" * 72
    entry = {uuid: {"logIndex": 999, "integratedTime": 1789200000, "logID": "deadbeef", "body": "e30="}}
    calls = []

    def transport(method, url, json_body, raw, headers):
        calls.append((method, url))
        if method == "POST":
            return 409, _json.dumps({"code": 409, "message": f"an equivalent entry already exists in the transparency log with UUID {uuid}"}).encode()
        return 200, _json.dumps(entry).encode()

    a = RekorAnchor(priv, ed25519_public_pem(priv), transport=transport).anchor("chain", root)
    assert a.anchored and a.receipt["uuid"] == uuid and a.receipt["logIndex"] == 999
    assert a.receipt["pre_existing_entry"] is True
    assert [m for m, _ in calls] == ["POST", "GET"] and uuid in calls[1][1]

    def no_uuid(method, url, json_body, raw, headers):
        return 409, b'{"code":409,"message":"an equivalent entry already exists"}'

    with pytest.raises(RuntimeError, match="without a UUID"):
        RekorAnchor(priv, ed25519_public_pem(priv), transport=no_uuid).anchor("chain", root)

    def fresh(method, url, json_body, raw, headers):
        return 201, _json.dumps(entry).encode()

    b = RekorAnchor(priv, ed25519_public_pem(priv), transport=fresh).anchor("chain", root)
    assert b.receipt["pre_existing_entry"] is False
