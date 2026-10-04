"""Invariants the next passes cannot violate (Task 7): read-only discovery manifests; valid=false without a
certified assessor signature (two for third-party-facing), when revoked, or when stale; the status endpoint
shares that code path and a static export points at it; the assessor cap bites at signature time; no rating
field on a synthesized finding; only confirmed state changes revoke."""
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from mark_ledger.keys import KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, key_id
from mark_platform.interfaces import (Assessor, AssessorCapExceeded, Attestation, AwsConnectorStub, Finding, PermissionsManifest, Tripwire, attestation_status, classify_drift,
                                      static_export)

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def test_aws_connector_manifest_is_read_only_and_discover_is_not_built():
    c = AwsConnectorStub()
    assert c.permissions_manifest().is_read_only()
    with pytest.raises(NotImplementedError):
        c.discover()


def test_a_write_action_makes_the_manifest_not_read_only():
    assert not PermissionsManifest("x", ("bedrock:ListAgents", "bedrock:CreateAgent")).is_read_only()
    assert not PermissionsManifest("x", ("s3:PutObject",)).is_read_only()
    assert not PermissionsManifest("x", ("iam:DeleteRole",)).is_read_only()


def _assessor(rpriv, rpub, purpose="attestation", cap=5):
    kpriv, kpub = generate_keypair()
    cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(kpub), "public_key": kpub, "purpose": purpose, "not_before": "2026-01-01T00:00:00Z", "not_after": "2030-01-01T00:00:00Z", "root_id": key_id(rpub)}
    return kpriv, issue_key_cert(cert, rpriv), Assessor(key_id(kpub), cap)


def _att(a1, a2, audience="third-party"):
    return Attestation(id="att-1", scope="acme:agent-fleet:2026-09", framework_versions={"eu-ai-act": "2024/1689"}, findings=[], assessor_ids=[a1, a2] if a2 else [a1], signatures=[],
                       baseline_at="2026-09-01T00:00:00Z", last_confirmed_run="2026-09-10T00:00:00Z", audience=audience)


def test_valid_needs_two_certified_assessors_for_third_parties_and_the_right_purpose():
    rpriv, rpub = generate_keypair()
    k1, c1, as1 = _assessor(rpriv, rpub)
    k2, c2, as2 = _assessor(rpriv, rpub)
    att = _att(as1.key_id, as2.key_id)
    assert att.valid(rpub, now=NOW) is False
    att.sign(as1, k1, c1, "2026-09-11T00:00:00Z")
    assert att.valid(rpub, now=NOW) is False, "one assessor is not enough for a third-party-facing attestation"
    att.sign(as2, k2, c2, "2026-09-11T00:00:00Z")
    assert att.valid(rpub, now=NOW) is True
    # a signature bound to another attestation id or scope does not count
    other = Attestation(id="att-2", scope=att.scope, framework_versions={}, findings=[], assessor_ids=att.assessor_ids, signatures=list(att.signatures), baseline_at="x", last_confirmed_run=att.last_confirmed_run)
    assert other.valid(rpub, now=NOW) is False
    # a key certified for another purpose does not count even though the signature verifies
    kb, cb, asb = _assessor(rpriv, rpub, purpose="cloak-bundle")
    bad = _att(asb.key_id, as2.key_id)
    bad.sign(asb, kb, cb, "2026-09-11T00:00:00Z")
    bad.signatures.append(att.signatures[1])
    assert bad.valid(rpub, now=NOW) is False
    internal = _att(as1.key_id, None, audience="internal")
    internal.sign(as1, k1, c1, "2026-09-11T00:00:00Z")
    assert internal.valid(rpub, now=NOW) is True


def test_status_endpoint_is_never_true_for_unsigned_revoked_or_stale():
    rpriv, rpub = generate_keypair()
    k1, c1, as1 = _assessor(rpriv, rpub)
    k2, c2, as2 = _assessor(rpriv, rpub)
    att = _att(as1.key_id, as2.key_id)
    s = attestation_status(att, rpub, chain_root="c" * 64, now=NOW)
    assert s["valid"] is False and any("signatures" in r for r in s["reasons"])
    att.sign(as1, k1, c1, "2026-09-11T00:00:00Z")
    att.sign(as2, k2, c2, "2026-09-11T00:00:00Z")
    assert attestation_status(att, rpub, chain_root="c" * 64, now=NOW)["valid"] is True
    # stale
    assert attestation_status(att, rpub, chain_root="c" * 64, now=NOW + timedelta(days=31))["valid"] is False
    # revoked
    att.revoke("confirmed state change", "e" * 64, "2026-09-12T00:00:00Z")
    s = attestation_status(att, rpub, chain_root="c" * 64, now=NOW)
    assert s["valid"] is False and s["revoked_at"] and any("revoked" in r for r in s["reasons"])
    exp = static_export(att, s)
    assert exp["live_endpoint"] == "/attestations/att-1/status" and exp["snapshot"]["valid"] is False


def test_assessor_cap_bites_at_signature_time():
    rpriv, rpub = generate_keypair()
    k1, c1, as1 = _assessor(rpriv, rpub, cap=1)
    att = _att(as1.key_id, None, audience="internal")
    att.sign(as1, k1, c1, "2026-09-11T00:00:00Z")
    att2 = Attestation(id="att-9", scope="s", framework_versions={}, findings=[], assessor_ids=[as1.key_id], signatures=[], baseline_at="x", audience="internal")
    with pytest.raises(AssessorCapExceeded):
        att2.sign(as1, k1, c1, "2026-09-11T00:00:00Z")


def test_valid_is_not_a_settable_field():
    assert "valid" not in {f.name for f in dataclasses.fields(Attestation)}


def test_finding_has_no_rating_field():
    assert not any("rating" in f.name or "score" in f.name for f in dataclasses.fields(Finding))


def test_tripwire_revokes_only_on_confirmed_state_change():
    rpriv, rpub = generate_keypair()
    k1, c1, as1 = _assessor(rpriv, rpub)
    att = _att(as1.key_id, None, audience="internal")
    att.sign(as1, k1, c1, "2026-09-11T00:00:00Z")
    assert att.valid(rpub, now=NOW)
    prev = {"status": "measured", "label": "pass", "probe_id": "ks.latency", "target_id": "x"}
    tw = Tripwire()
    # a probe failure: ticket, validity unchanged
    assert tw.handle(classify_drift(prev, {"status": "not_run", "probe_id": "ks.latency", "target_id": "x"}), att, "2026-09-12T00:00:00Z") == "ticket"
    assert att.valid(rpub, now=NOW) and tw.tickets and not att.revoked_at
    # a connector error is a probe failure too
    assert tw.handle(classify_drift(prev, {"status": "measured", "label": "fail", "connector_error": True, "probe_id": "ks.latency", "target_id": "x"}), att, "t") == "ticket"
    assert att.valid(rpub, now=NOW)
    # an unconfirmed state change queues confirmation, does not revoke
    ev = classify_drift(prev, {"status": "measured", "label": "fail", "probe_id": "ks.latency", "target_id": "x"})
    assert ev.alerts and tw.handle(ev, att, "t") == "pending_confirmation" and att.valid(rpub, now=NOW)
    # a confirmed state change revokes and records the evidence reference
    ev2 = classify_drift(prev, {"status": "measured", "label": "fail", "probe_id": "ks.latency", "target_id": "x"}, confirmed=True)
    assert tw.handle(ev2, att, "2026-09-12T00:00:00Z") == "revoked"
    assert not att.valid(rpub, now=NOW) and att.revocation_evidence_ref == ev2.evidence_ref and len(att.revocation_evidence_ref) == 64
