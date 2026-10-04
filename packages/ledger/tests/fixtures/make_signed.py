"""Produces py-signed.json: a throwaway root, certificate and signed object made by the Python ledger, which
packages/core/test/canonical-conformance.test.ts must verify. Deterministic keys (fixed seeds).
Run: uv run python packages/ledger/tests/fixtures/make_signed.py

**Every value here must be signable under A9.** This generator signs, so the signing-time portability rule
applies to it like any other caller. It used to carry `"big": 1e21`, and A9 refuses that -- which left the
committed fixture verifying fine while the code that reproduces it raised. A generator that cannot regenerate
its own fixture is broken whether or not any suite is red.

Losing 1e21 from here loses no coverage, and that is worth stating rather than assuming:

  - **Rendering parity** for 1e21 -- the thing a cross-implementation fixture exists to prove -- is already
    pinned by canonical-input.json/canonical-golden.txt, which carry it and which both suites compare.
  - **Signature parity** over a value that size is coverage of something A9 has made impossible. A test that
    exercises a path no caller can reach is not protection.

What replaces it is stronger: 2^53 - 1, the largest integer the rule allows. That is the boundary value a
sloppy implementation is most likely to render differently, and it travels the whole signing path."""
import json
from pathlib import Path

from mark_ledger.canonical import SAFE_INTEGER, canonical_json
from mark_ledger.keys import KEY_CERT_SCHEMA, issue_key_cert, key_id, public_key_of, sign_object

here = Path(__file__).parent
root_priv, key_priv = "33" * 32, "44" * 32
root_pub, key_pub = public_key_of(root_priv), public_key_of(key_priv)
cert = {"schema": KEY_CERT_SCHEMA, "key_id": key_id(key_pub), "public_key": key_pub, "purpose": "gate", "not_before": "2026-09-01T00:00:00Z", "not_after": "2026-12-01T00:00:00Z", "root_id": key_id(root_pub), "notes": "test fixture, not a real key"}
signed_cert = issue_key_cert(cert, root_priv)
obj = {"issued_at": "2026-09-10T00:00:00Z", "gate": "ks.completeness", "thresholds": {"max_landed_after_halt": 0, "min_replications": 20, "tolerance_ms": 5.5}, "labels": ["pass", "fail", "informational"], "note": "unicode é中 😀 \"quoted\" 1e-7", "ratio": 1e-7, "big": SAFE_INTEGER}
so = sign_object(obj, key_priv, signed_cert)
out = {"root_public_key": root_pub, "root_id": key_id(root_pub), "purpose": "gate", "at": "2026-09-10T00:00:00Z", "signed": so.to_json(), "canonical": canonical_json(obj)}
(here / "py-signed.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote py-signed.json", key_id(root_pub))
