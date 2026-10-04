"""Run manifests (Task 2.2): every run produces one, signed with the bundle-signing key under the existing
hierarchy (purpose 'cloak-bundle' is the certified purpose of that key today; a dedicated 'run-manifest'
purpose is a root-session decision recorded in docs/KEYS.md when taken).

A manifest is pins + environment + the evidence chain root. It carries NO results: results live in the ledger,
and the manifest's chain_root commits to all of them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .canonical import object_hash
from .keys import KeyCertError, SignedObject, iso, now_utc, sign_object, verify_signed_object

MANIFEST_SCHEMA = "mark.run-manifest/1"
# Founder decision (session 2 pre-flight B2/B3): run manifests are signed with a `run-manifest` key. A manifest
# signed with the cloak-bundle key still verifies (prior manifests stay valid) but is reported PROVISIONAL.
MANIFEST_KEY_PURPOSE = "run-manifest"
MANIFEST_KEY_PURPOSES = ("run-manifest", "cloak-bundle")


def build_manifest(*, run_id: str, benchmark: str | None, pins: dict[str, Any], environment: dict[str, Any], started_at: str, ended_at: str | None,
                   chain_id: str, chain_root: str | None, results_hash: str | None, notes: str = "", account: dict[str, Any] | None = None,
                   operator: dict[str, Any] | None = None) -> dict[str, Any]:
    required_pins = ("image_digest", "lockfile_sha256", "engine_version", "repo_commit")
    missing = [k for k in required_pins if not pins.get(k)]
    if missing:
        raise ValueError(f"manifest pins missing: {missing} (a run without pins is not reproducible and is not written)")
    body = {
        "schema": MANIFEST_SCHEMA,
        "run_id": run_id,
        "benchmark": benchmark,
        "issued_at": iso(now_utc()),
        "started_at": started_at,
        "ended_at": ended_at,
        "pins": pins,
        "environment": environment,
        "evidence": {"chain_id": chain_id, "chain_root": chain_root, "results_sha256": results_hash},
        "notes": notes,
    }
    # attempt 4, A5: the run-level account (scheduled, recorded, not run and why) is in the signed object, not derived
    # from results.json. The platform computes it (mark_platform.account) and recomputes it before signing; a manifest
    # built before A5 has no such field, and a reader must not invent one.
    if account is not None:
        body["account"] = account
    # attempt 4, C2: the operator of record -- where the run executed, from the provider's own identity, resolved at run open
    # and chained in the run_open record; the platform compares it literally with its own deployment record (D33). A manifest
    # built before C2 has no such field, and a reader must not invent one.
    if operator is not None:
        body["operator"] = operator
    return body


def sign_manifest(manifest: dict[str, Any], private_key_hex: str, signed_cert: dict[str, Any], *, signed_on: str = "laptop") -> SignedObject:
    """`signed_on` is part of the signed object: "laptop" (the key never left the founder's machine) or "pod"
    (the key was online on the runner; a reader must know). Founder decision, session 2 review."""
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("not a run manifest")
    return sign_object({**manifest, "signed_on": signed_on}, private_key_hex, signed_cert)


def verify_manifest(signed: SignedObject, root_public_hex: str, *, revocations: dict[str, Any] | None = None) -> dict[str, Any]:
    """Returns the manifest; `manifest_key_purpose(signed)` says which purpose signed it."""
    last: Exception | None = None
    for purpose in MANIFEST_KEY_PURPOSES:
        try:
            m = verify_signed_object(signed, root_public_hex, purpose, revocations=revocations)
            break
        except KeyCertError as e:
            last = e
            if e.reason != "schema" or "purpose" not in e.detail:
                raise
    else:
        raise last  # type: ignore[misc]
    if m.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("signed object is not a run manifest")
    return m


def manifest_key_purpose(signed: SignedObject) -> str:
    return str(((signed.key_cert or {}).get("cert") or {}).get("purpose"))


def manifest_signature_status(signed: SignedObject) -> str:
    """'run-manifest' = signed with the dedicated key; 'provisional (cloak-bundle key)' otherwise."""
    p = manifest_key_purpose(signed)
    return "run-manifest" if p == "run-manifest" else f"provisional ({p} key)"


def write_signed(path: str | Path, signed: SignedObject) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(signed.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def read_signed(path: str | Path) -> SignedObject:
    return SignedObject.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def manifest_id(manifest: dict[str, Any]) -> str:
    return object_hash(manifest)
