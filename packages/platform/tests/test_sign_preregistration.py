"""The pre-registration signer (founder ruling 2026-09-22): in the tree, reproducible, and checking rather than claiming.

Attempt 3's signed pre-registration exists and the tool that made it does not. A signed object nobody can
reproduce is the opposite of what a pre-registration signature is for -- a reader should be able to take the
record, the spec and the key's public half and confirm the signature covers exactly what it claims to.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest

from mark_ledger.canonical import NotPortable
from mark_ledger.keys import (KEY_CERT_SCHEMA, generate_keypair, issue_key_cert, iso, key_id, now_utc,
                              public_key_of, sign_object, verify_signed_object)

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "packages" / "platform" / "scripts" / "sign_preregistration.py"
RECORD = "benchmarks/attempt4-preregistration.md"
SPEC = "benchmarks/attempt4-agent-controls.yaml"
ANCHORED = "5463436405328a68b1549f6bbf4b2ee70c44c8fbef6fe858b68cccbf5e794dda"


@pytest.fixture(scope="module")
def signer():
    assert SCRIPT.exists(), "the signer must live in the tree: a signature nobody can reproduce is not evidence"
    spec = importlib.util.spec_from_file_location("sign_prereg", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gate_signer():
    root_priv, root_pub = generate_keypair()
    priv, pub = generate_keypair()
    cert = issue_key_cert({"schema": KEY_CERT_SCHEMA, "key_id": key_id(pub), "public_key": pub, "purpose": "gate",
                           "root_id": key_id(root_pub), "not_before": iso(now_utc() - timedelta(days=1)),
                           "not_after": iso(now_utc() + timedelta(days=30))}, root_priv)
    return root_pub, priv, pub, cert


def test_it_reads_the_tags_committed_bytes_and_the_anchored_hash_is_what_they_are(signer):
    """**The regression, asserted.** The first signer compared the working copy's bytes with HEAD's. On a Windows
    checkout with core.autocrlf the spec carried 93 carriage returns on disk and none in the commit, git reported
    no change, and the signer refused a correct tree. Reading the tag's committed blob makes that comparison
    unnecessary -- and it is also what makes the anchored hash reproducible on any machine."""
    commit = signer.commit_of("attempt4-freeze-3")
    record = signer.committed(commit, RECORD)
    assert hashlib.sha256(record).hexdigest() == ANCHORED, "the anchored hash is the committed blob, not one machine's disk"
    spec_text = signer.committed(commit, SPEC).decode("utf-8")
    assert "\r" not in spec_text, "the committed spec is LF: whatever the disk holds, this is what gets signed"


def test_the_commit_comes_from_the_tag_so_the_two_pins_agree(signer):
    """HEAD is wherever the shell happens to be. Resolving the commit from the tag means an object naming a tag
    can never carry a commit that is not that tag's -- which is what signing from `main` would have produced."""
    assert signer.commit_of("attempt4-freeze-3").startswith("d12fdee")
    with pytest.raises(signer.SignRefused, match="no such tag"):
        signer.commit_of("attempt4-freeze-does-not-exist")


def test_a_path_missing_at_the_tag_is_refused_by_name(signer):
    commit = signer.commit_of("attempt4-freeze-3")
    with pytest.raises(signer.SignRefused, match="is not in"):
        signer.committed(commit, "benchmarks/no-such-file.md")


def test_the_same_inputs_give_the_same_bytes(signer):
    """`signed_at` is an INPUT, not a clock reading, so the object reproduces byte for byte."""
    args = (RECORD, b"body", SPEC, "text", "t", "c")
    a = json.dumps(signer.build(*args, "2026-09-22T00:00:00.000Z"), sort_keys=True)
    assert a == json.dumps(signer.build(*args, "2026-09-22T00:00:00.000Z"), sort_keys=True)
    assert a != json.dumps(signer.build(*args, "2026-09-22T00:00:01.000Z"), sort_keys=True), "a different time is a different input"


def test_spec_cites_record_is_checked_not_claimed(signer):
    """A document cannot contain its own hash, so the spec holds it. The flag is true only when the spec carries
    the path AND the digest -- a spec naming the file without its hash pins nothing, and a digest with no path is
    unattributed."""
    digest = hashlib.sha256(b"body").hexdigest()
    b = lambda spec_text: signer.build("r.md", b"body", "s.yaml", spec_text, "t", "c", "z")["spec_cites_record"]  # noqa: E731
    assert b("pre_registration: nothing here") is False
    assert b("pre_registration: r.md") is False, "the path alone pins nothing"
    assert b(f"pre_registration: {digest}") is False, "a digest with no path is unattributed"
    assert b(f"pre_registration: r.md (sha256 {digest})") is True


def test_the_tagged_spec_carries_the_tagged_record(signer):
    """The check the signer runs, run here against the real tag, so the suite catches a spec that stops citing the
    record before the founder meets the refusal."""
    commit = signer.commit_of("attempt4-freeze-3")
    body = signer.build(RECORD, signer.committed(commit, RECORD), SPEC, signer.committed(commit, SPEC).decode("utf-8"),
                        "attempt4-freeze-3", commit, "z")
    assert body["spec_cites_record"] is True and body["record_sha256"] == ANCHORED


def test_the_signed_object_verifies_under_the_root_for_the_gate_purpose(signer):
    root_pub, priv, pub, cert = _gate_signer()
    body = signer.build(RECORD, b"body", SPEC, "text", "attempt4-freeze-3", "deadbeef", "2026-09-22T00:00:00.000Z")
    so = sign_object(body, priv, cert)
    assert verify_signed_object(so, root_pub, "gate", issued_at_field=None)
    assert public_key_of(priv) == pub


def test_the_signing_chokepoint_applies_to_this_object_too(signer):
    """A9/A9c reach this signature like every other: an integer nanosecond field is refused, not published."""
    _root_pub, priv, _pub, cert = _gate_signer()
    body = signer.build(RECORD, b"body", SPEC, "text", "t", "c", "z")
    with pytest.raises(NotPortable):
        sign_object({**body, "signed_mono_ns": 331_105_879_541_700}, priv, cert)


def test_it_writes_lf_so_the_anchor_scripts_byte_check_passes(signer):
    """On Windows the default text mode writes CRLF, so the file on disk would differ from the blob git stores,
    and anchor_file.py -- which compares the two -- would refuse the very file this script just produced."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'newline="\\n"' in source, "the signed object must be written with LF line endings"
