"""A9: nothing gets signed that another implementation may render differently.

Two layers, and the distinction is the ruling:

  - **Rendering** (`_js_number`, A9b) prints what JavaScript prints, for every value, including ones this rule
    refuses. That is what makes this a faithful port, and `test_canonical.py`'s parity fixtures test it.
  - **Signing** refuses a document carrying a number of magnitude 2^53 or beyond, whatever its Python type.

The rule is the conservative one the record (0007), `mark_product/portable.py` and `apps/console/src/portable.ts`
already enforce, rather than the narrower one this implementation could defend for itself:

    "Consistency beats one implementation being cleverer than the other three: a document the ledger could
     render correctly but the platform would refuse is a document that shouldn't exist, because no legitimate
     field in a gate, a manifest, or an attestation needs an integer that size. If one ever does, that's a
     design decision, not a number."

`sign_object` is the single chokepoint -- `sign_manifest` and everything else reach signing through it -- so a
future caller cannot bypass the rule by forgetting it.

The vectors are `mark_product`'s and are read rather than copied, so this cannot drift from the other three
implementations with every suite green. That is a test-time dependency only: `mark-product` depends on
`mark-ledger`, never the reverse.
"""
from __future__ import annotations

import json
from datetime import timedelta
from importlib import resources

import pytest

from mark_ledger.canonical import NotPortable, canonical_json, refuse_unportable
from mark_ledger.keys import (KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, iso, key_id, now_utc,
                              public_key_of, sign_object, verify_signed_object)

VECTORS = json.loads(
    resources.files("mark_product").joinpath("portable_vectors.json").read_text(encoding="utf-8"))


@pytest.fixture()
def signer():
    """A root, a certified signing key, and the purpose it is good for."""
    root_priv, root_pub = generate_keypair()
    priv, pub = generate_keypair()
    cert = issue_key_cert({"schema": KEY_CERT_SCHEMA, "key_id": key_id(pub), "public_key": pub,
                           "purpose": "run-manifest", "root_id": key_id(root_pub),
                           "not_before": iso(now_utc() - timedelta(days=1)),
                           "not_after": iso(now_utc() + timedelta(days=30))}, root_priv)
    return {"root_public": root_pub, "private": priv, "cert": cert}


# ---- the rule, against the shared vectors ----------------------------------------------------------------------
@pytest.mark.parametrize("case", VECTORS["portable"])
def test_a_portable_document_signs(case, signer):
    """Whatever the other three accept, this signs. Refusing what they accept is the same defect pointing the
    other way: a bundle nobody can sign."""
    value = case["value"]
    obj = value if isinstance(value, dict) else {"v": value}
    signed = sign_object(obj, signer["private"], signer["cert"])
    assert verify_signed_object(signed, signer["root_public"], "run-manifest", issued_at_field=None)


@pytest.mark.parametrize("case", VECTORS["unportable"])
def test_an_unportable_document_is_never_signed(case, signer):
    """All ten, with no exemptions. This is the conservative rule: some of these the canonicaliser renders
    perfectly well, and they still do not get signed."""
    value = case["value"]
    obj = value if isinstance(value, dict) else {"v": value}
    with pytest.raises((NotPortable, ValueError, TypeError)):
        sign_object(obj, signer["private"], signer["cert"])


@pytest.mark.parametrize("case", VECTORS["unportable"])
def test_the_refusal_names_the_field(case):
    """A refusal that only says "this document cannot be signed" leaves somebody grepping a manifest by hand at
    the moment they most need not to. Checked against `refuse_unportable` directly, since the vector's `path` is
    written for a document at the root."""
    value = case["value"]
    if not isinstance(value, dict):
        pytest.skip("the vector's path is relative to a document root; this one is a bare value")
    with pytest.raises(NotPortable) as caught:
        refuse_unportable(value)
    assert caught.value.path == case["path"]


# ---- the two layers stay separate --------------------------------------------------------------------------------
def test_rendering_still_works_for_values_signing_refuses():
    """**The layering, asserted.** A9b made `_js_number` print what JavaScript prints; A9 stops those values
    reaching a signature. If a future change collapsed the two -- by refusing inside `canonical_json` -- this
    fails, and so would the parity fixtures that prove the port is faithful."""
    assert canonical_json({"n": 1e21}) == '{"n":1e+21}'
    assert canonical_json({"n": 9007199254740992}) == '{"n":9007199254740992}'
    with pytest.raises(NotPortable):
        refuse_unportable({"n": 1e21})


def test_the_boundary_is_2_to_the_53():
    """2^53 - 1 signs; 2^53 does not. Both signs of it."""
    refuse_unportable({"n": 9007199254740991})
    refuse_unportable({"n": -9007199254740991})
    for over in (9007199254740992, -9007199254740992, 9007199254740992.0):
        with pytest.raises(NotPortable) as caught:
            refuse_unportable({"n": over})
        assert caught.value.path == "$.n"


def test_a_manifest_is_refused_through_its_own_entry_point(signer):
    """`sign_manifest` delegates to `sign_object`, so the rule covers it without knowing about it. A rule
    enforced only in the one caller that exists today is a rule the second caller will not have."""
    from mark_ledger.manifest import MANIFEST_SCHEMA, sign_manifest

    good = {"schema": MANIFEST_SCHEMA, "run_id": "r1", "counts": {"scheduled": 22, "recorded": 20}}
    assert sign_manifest(good, signer["private"], signer["cert"])

    bad = {"schema": MANIFEST_SCHEMA, "run_id": "r1", "counts": {"scheduled": 22, "ns": 9007199254740993}}
    with pytest.raises(NotPortable) as caught:
        sign_manifest(bad, signer["private"], signer["cert"])
    assert caught.value.path == "$.counts.ns"


def test_a_bool_is_not_an_oversized_integer():
    """`bool` is an `int` subclass in Python. A guard that forgot it would refuse `True`, which passes review
    and fails at a signing."""
    refuse_unportable({"ok": True, "no": False})
