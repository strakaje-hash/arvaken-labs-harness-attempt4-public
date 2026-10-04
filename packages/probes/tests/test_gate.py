"""Gate enforcement (Constitution rule 1): unsigned -> informational; signed + preconditions -> decisive."""
import json
from pathlib import Path

import pytest

from mark_ledger.keys import KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, key_id, sign_object
from mark_probes.gate import INFORMATIONAL, decide, gate_body, load_gate

GATES = Path(__file__).resolve().parents[3] / "gates"


def _root_and_gate_key(purpose="gate"):
    rpriv, rpub = generate_keypair()
    kpriv, kpub = generate_keypair()
    cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": purpose, "not_before": "2026-01-01T00:00:00Z", "not_after": "2030-01-01T00:00:00Z", "root_id": key_id(rpub)}
    return rpriv, rpub, kpriv, issue_key_cert(cert, rpriv)


def test_repo_gates_are_signed_under_the_root_and_unsigned_without_it():
    root = (GATES.parent / "packages" / "bundles" / "keys" / "root.pub").read_text().strip()
    g = load_gate(GATES, "ks.latency", root_public_hex=root)
    assert g.signed and g.version == 2 and g.signed_by
    # without the root public key a signed file cannot be trusted: it loads, unsigned, and says why
    u = load_gate(GATES, "ks.latency", root_public_hex=None)
    assert not u.signed and "no root public key" in u.unsigned_reason
    v = decide(u, "pass", [])
    assert v.label == INFORMATIONAL and not v.decisive and v.outcome_if_decisive == "pass"
    assert any("not signed" in r for r in v.reasons)


def test_signed_gate_is_decisive_only_when_preconditions_pass(tmp_path):
    rpriv, rpub, kpriv, kcert = _root_and_gate_key()
    body = gate_body("ks.test", 1, "kill-switch", {"max": 1}, {"min_replications": 3}, ["pass", "fail"], {"max": "test"}, issued_at="2026-09-11T00:00:00Z")
    so = sign_object(body, kpriv, kcert)
    (tmp_path / "ks.test.signed.json").write_text(json.dumps(so.to_json()))
    g = load_gate(tmp_path, "ks.test", rpub)
    assert g.signed and g.signed_by == kcert["cert"]["key_id"]
    assert decide(g, "fail", []).label == "fail"
    assert decide(g, "pass", []).decisive
    v = decide(g, "pass", ["measured replications 2 < min_replications 3"])
    assert v.label == INFORMATIONAL and v.outcome_if_decisive == "pass"
    assert decide(g, None, []).label == INFORMATIONAL


def test_signed_by_wrong_purpose_or_wrong_root_is_informational(tmp_path):
    rpriv, rpub, kpriv, kcert = _root_and_gate_key(purpose="cloak-bundle")
    body = gate_body("ks.test", 1, "kill-switch", {"max": 1}, {}, ["pass", "fail"], {}, issued_at="2026-09-11T00:00:00Z")
    (tmp_path / "ks.test.signed.json").write_text(json.dumps(sign_object(body, kpriv, kcert).to_json()))
    g = load_gate(tmp_path, "ks.test", rpub)
    assert not g.signed and "purpose" in g.unsigned_reason
    rpriv2, rpub2, kpriv2, kcert2 = _root_and_gate_key(purpose="gate")
    (tmp_path / "ks.test.signed.json").write_text(json.dumps(sign_object(body, kpriv2, kcert2).to_json()))
    _, other_root, _, _ = _root_and_gate_key()
    g2 = load_gate(tmp_path, "ks.test", other_root)
    assert not g2.signed and "different root" in g2.unsigned_reason
    assert load_gate(tmp_path, "ks.test", rpub2).signed


def test_tampered_signed_gate_is_informational(tmp_path):
    rpriv, rpub, kpriv, kcert = _root_and_gate_key()
    body = gate_body("ks.test", 1, "kill-switch", {"max": 1}, {}, ["pass", "fail"], {}, issued_at="2026-09-11T00:00:00Z")
    d = sign_object(body, kpriv, kcert).to_json()
    d["object"]["thresholds"]["max"] = 999
    (tmp_path / "ks.test.signed.json").write_text(json.dumps(d))
    g = load_gate(tmp_path, "ks.test", rpub)
    assert not g.signed and g.thresholds["max"] == 999 and "signature" in g.unsigned_reason


def test_unknown_outcome_label_is_never_decisive_and_names_the_missing_label():
    """Founder rule 2026-09-12: a signed gate must name every outcome the probe can produce. A probe version ahead
    of its gate (ks.mechanism v2 read not_attempted before gate v3 named it) must not abort a pod run; the row is
    informational and the reason says which label the next gate version must carry."""
    g = load_gate(GATES, "ks.completeness", None)
    v = decide(g, "maybe", [])
    assert v.label == "informational" and not v.decisive and v.outcome_if_decisive == "maybe"
    assert any("'maybe' is not a label of gate ks.completeness" in r and "must name every outcome" in r for r in v.reasons), v.reasons
