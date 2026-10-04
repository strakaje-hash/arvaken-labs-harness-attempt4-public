"""A9c: a timestamp in a signed document is a decimal string (founder ruling 2026-09-20).

A9 already refuses any number at or beyond 2^53, and a nanosecond count is about 1.8e18 -- roughly 200x past it.
So why a second rule? Because **a monotonic clock's magnitude is the signing host's uptime, not the epoch.**

    a laptop up three days  ->  3.3e14   under 2^53   signs
    a Linux pod up 4 months ->  1.4e16   over  2^53   refused

A9's magnitude rule therefore cannot be exercised on the machine the instrument is written on. That is not a
hypothetical: C2's operator record carried an integer `mono_ns` into the signed run manifest on 2026-09-21, and
every test on the laptop passed, because the laptop's monotonic clock was two orders of magnitude too small to
trip the rule. It would have fired for the first time at Phase 0 signing, on the pod, which is the moment A9c
exists to protect.

This rule is about **shape, not size**: an integer under a key ending `_ns` is refused on every host whatever its
uptime, and the decimal string the ruling requires is accepted. A9's magnitude rule stays as the backstop.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from mark_ledger.canonical import NotPortable, integer_timestamp, refuse_integer_timestamps
from mark_ledger.keys import (KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, iso, key_id, now_utc,
                              sign_object, verify_signed_object)

LAPTOP_NS = 331_105_879_541_700          # this machine, up three days: A9 accepts it
POD_NS = 20_923_021_892_223_845          # a pod that actually ran attempt 3: A9 refuses it


@pytest.fixture()
def signer():
    root_priv, root_pub = generate_keypair()
    priv, pub = generate_keypair()
    cert = issue_key_cert({"schema": KEY_CERT_SCHEMA, "key_id": key_id(pub), "public_key": pub,
                           "purpose": "run-manifest", "root_id": key_id(root_pub),
                           "not_before": iso(now_utc() - timedelta(days=1)),
                           "not_after": iso(now_utc() + timedelta(days=30))}, root_priv)
    return {"root_public": root_pub, "private": priv, "cert": cert}


def _operator(stamp):
    """The shape C2 writes into every signed manifest."""
    return {"operator": {"schema": "mark.operator/1", "tenant": "lab:arvaken", "asked_at": {"iso": "2026-09-21T00:00:00Z", "mono_ns": stamp},
                         "facts": [{"name": "pod_id", "value": "abc", "source": {"call": "file", "endpoint": "/etc/rp_environment",
                                                                                 "read_at": {"iso": "2026-09-21T00:00:00Z", "mono_ns": stamp}}}]}}


def test_the_integer_is_refused_at_a_magnitude_A9_accepts():
    """**The rule is shape, not size.** This is the whole point: at the laptop's own magnitude A9 says nothing, so
    only this rule can fail here -- and a rule that can only fail on the pod is a rule the laptop cannot test."""
    from mark_ledger.canonical import unportable

    assert unportable(_operator(LAPTOP_NS)) is None            # A9 is silent at this size
    found = integer_timestamp(_operator(LAPTOP_NS))
    assert found and found[0] == "$.operator.asked_at.mono_ns"
    assert "decimal string" in found[1] and "str(" in found[1]   # the refusal says what to do about it


def test_the_decimal_string_signs_at_the_magnitude_that_would_have_failed(signer):
    """The same reading that refuses as an integer signs as a string, and verifies."""
    so = sign_object({"schema": "x", **_operator(str(POD_NS))}, signer["private"], signer["cert"])
    assert verify_signed_object(so, signer["root_public"], "run-manifest", issued_at_field=None)
    assert so.object["operator"]["asked_at"]["mono_ns"] == str(POD_NS)


def test_signing_refuses_the_integer_form_through_the_chokepoint(signer):
    """R10: the OLD thing is refused. Not "a string is accepted" -- that would pass over the defect untouched."""
    with pytest.raises(NotPortable) as caught:
        sign_object({"schema": "x", **_operator(LAPTOP_NS)}, signer["private"], signer["cert"])
    assert caught.value.path == "$.operator.asked_at.mono_ns"
    with pytest.raises(NotPortable) as pod:
        sign_object({"schema": "x", **_operator(POD_NS)}, signer["private"], signer["cert"])
    assert pod.value.path == "$.operator.asked_at.mono_ns"


def test_a_document_with_no_ns_field_still_signs(signer):
    """R3 positive control: the guard refuses a field, not every document -- otherwise the tests above prove
    only that signing is broken."""
    so = sign_object({"schema": "x", "run_id": "r", "count": 12, "when": "2026-09-21T00:00:00Z"},
                     signer["private"], signer["cert"])
    assert verify_signed_object(so, signer["root_public"], "run-manifest", issued_at_field=None)


def test_a_certificate_is_not_the_one_signed_document_the_rule_misses():
    """`issue_key_cert` canonicalises and signs directly rather than wrapping a `SignedObject`, so before this
    it met neither refusal -- and so did `mark_product.trust.issue_assessor_cert`, the same shape in another
    package. Both call `sign_canonical_body` now: a second path to signing is a second set of rules, whatever the
    comment above it says, and which package the issuer lives in does not change that."""
    root_priv, root_pub = generate_keypair()
    _priv, pub = generate_keypair()
    body = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(pub), "public_key": pub, "purpose": "run-manifest",
            "root_id": key_id(root_pub), "not_before": iso(now_utc() - timedelta(days=1)),
            "not_after": iso(now_utc() + timedelta(days=30))}
    assert issue_key_cert(dict(body), root_priv)["cert"]["key_id"] == key_id(pub)    # the ordinary cert still issues
    with pytest.raises(NotPortable) as caught:
        issue_key_cert({**body, "issued_mono_ns": LAPTOP_NS}, root_priv)
    assert caught.value.path == "$.issued_mono_ns"


def test_the_walk_reaches_lists_and_leaves_other_types_alone():
    assert integer_timestamp({"a": [{"b": {"t_ns": 5}}]})[0] == "$.a[0].b.t_ns"
    assert integer_timestamp({"flag_ns": True}) is None          # a bool is not a timestamp
    assert integer_timestamp({"gap_ns": 1.5}) is None            # a float is not the integer this rule is about
    assert integer_timestamp({"ns": 20_923_021_892_223_845}) is None   # the suffix is `_ns`; A9's size rule has this one
    refuse_integer_timestamps({"ok": "2026-09-21T00:00:00Z"})     # does not raise
