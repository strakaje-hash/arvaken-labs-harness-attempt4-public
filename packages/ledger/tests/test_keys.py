"""Key hierarchy: the Python verifier must reject exactly what the TS verifier rejects, and must accept objects
the TS signer produced (fixtures/ts-signed.json, made by fixtures/make-signed.mjs with packages/core)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mark_ledger.keys import (KEY_CERT_SCHEMA, REVOCATIONS_SCHEMA, KeyCertError, SignedObject, generate_keypair, issue_key_cert, key_id,
                              sign_bytes, sign_object, verify_key_cert, verify_signed_object)
from mark_ledger.canonical import canonical_json

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _cert(root_priv, root_pub, purpose="cloak-bundle", nb="2026-09-01T00:00:00Z", na="2026-12-01T00:00:00Z"):
    priv, pub = generate_keypair()
    cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(pub), "public_key": pub, "purpose": purpose, "not_before": nb, "not_after": na, "root_id": key_id(root_pub)}
    return priv, issue_key_cert(cert, root_priv)


def _crl(root_priv, root_pub, revoked=()):
    lst = {"schema": REVOCATIONS_SCHEMA, "issued_at": "2026-09-01T00:00:00Z", "not_after": "2026-12-01T00:00:00Z", "root_id": key_id(root_pub), "revoked": list(revoked)}
    return {"list": lst, "root_signature": sign_bytes(canonical_json(lst).encode(), root_priv)}


def test_sign_and_verify_roundtrip():
    rpriv, rpub = generate_keypair()
    kpriv, kcert = _cert(rpriv, rpub, purpose="gate")
    so = sign_object({"issued_at": "2026-09-10T00:00:00Z", "x": 1}, kpriv, kcert)
    assert verify_signed_object(so, rpub, "gate", at=NOW) == {"issued_at": "2026-09-10T00:00:00Z", "x": 1}
    back = SignedObject.from_json(json.loads(json.dumps(so.to_json())))
    assert verify_signed_object(back, rpub, "gate", at=NOW)["x"] == 1


def test_wrong_purpose_other_root_tamper_expired_revoked():
    rpriv, rpub = generate_keypair()
    kpriv, kcert = _cert(rpriv, rpub, purpose="gate")
    so = sign_object({"issued_at": "2026-09-10T00:00:00Z", "x": 1}, kpriv, kcert)
    with pytest.raises(KeyCertError) as e:
        verify_signed_object(so, rpub, "cloak-bundle", at=NOW)
    assert e.value.reason == "schema"
    _, other_pub = generate_keypair()
    with pytest.raises(KeyCertError) as e:
        verify_signed_object(so, other_pub, "gate", at=NOW)
    assert e.value.reason == "root"
    tampered = SignedObject(object={**so.object, "x": 2}, signature=so.signature, key_id=so.key_id, key_cert=so.key_cert)
    with pytest.raises(KeyCertError) as e:
        verify_signed_object(tampered, rpub, "gate", at=NOW)
    assert e.value.reason == "signature"
    with pytest.raises(KeyCertError) as e:
        verify_signed_object(so, rpub, "gate", at=datetime(2027, 3, 1, tzinfo=timezone.utc))
    assert e.value.reason == "cert-expired"
    crl = _crl(rpriv, rpub, [{"key_id": kcert["cert"]["key_id"], "at": "2026-09-05T00:00:00Z", "reason": "test"}])
    with pytest.raises(KeyCertError) as e:
        verify_signed_object(so, rpub, "gate", at=NOW, revocations=crl)
    assert e.value.reason == "revoked"
    # a forged revocation list (signed by someone else) is ignored, so the key is still good
    fpriv, fpub = generate_keypair()
    forged = _crl(fpriv, rpub, [{"key_id": kcert["cert"]["key_id"], "at": "x", "reason": "forged"}])
    assert verify_signed_object(so, rpub, "gate", at=NOW, revocations=forged)["x"] == 1


def test_issued_at_outside_key_validity_is_refused():
    rpriv, rpub = generate_keypair()
    kpriv, kcert = _cert(rpriv, rpub, purpose="gate")
    so = sign_object({"issued_at": "2027-06-01T00:00:00Z"}, kpriv, kcert)
    with pytest.raises(KeyCertError) as e:
        verify_signed_object(so, rpub, "gate", at=NOW)
    assert e.value.reason == "key"


def test_signer_output_must_verify_under_the_certificate():
    rpriv, rpub = generate_keypair()
    _, kcert = _cert(rpriv, rpub)
    other_priv, _ = generate_keypair()
    with pytest.raises(KeyCertError) as e:
        sign_object({"x": 1}, other_priv, kcert)
    assert e.value.reason == "key"


def test_verifies_an_object_signed_by_the_typescript_implementation():
    fx = json.loads((FIX / "ts-signed.json").read_text(encoding="utf-8"))
    so = SignedObject.from_json(fx["signed"])
    at = datetime.fromisoformat(fx["at"].replace("Z", "+00:00"))
    obj = verify_signed_object(so, fx["root_public_key"], fx["purpose"], at=at, revocations=fx["revocations"])
    assert obj == fx["signed"]["object"]
    cert = verify_key_cert(so.key_cert, fx["root_public_key"], fx["purpose"], at=at)
    assert cert["key_id"] == key_id(cert["public_key"])
    # and the TS key id derivation matches ours
    assert key_id(fx["root_public_key"]) == fx["root_id"]
