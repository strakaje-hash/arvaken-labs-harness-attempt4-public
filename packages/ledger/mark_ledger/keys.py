"""Ed25519 signing and the key hierarchy, ported from packages/core/src/bundle.ts so the Python runtime signs
objects the TypeScript verifier accepts and verifies objects the TypeScript signer produced.

  root key --signs--> key certificate (purpose, validity window) --signs--> object
  root key --signs--> revocation list

Fail closed: every check raises a typed reason. `purpose` is a parameter here (the TS side is bound to
'cloak-bundle' today); a certificate is only good for the purpose it names.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .canonical import canonical_json, refuse_integer_timestamps, refuse_unportable, sha256_hex

KEY_CERT_SCHEMA = "mark.signing-key/1"
REVOCATIONS_SCHEMA = "mark.revocations/1"
SIGNED_OBJECT_SCHEMA = "mark.signed-object/1"
DEFAULT_SKEW = timedelta(minutes=5)


class KeyCertError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def key_id(public_key_hex: str) -> str:
    """First 16 hex chars of SHA-256(public key bytes), as packages/core keyId."""
    return sha256_hex(bytes.fromhex(public_key_hex))[:16]


def generate_keypair() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    return priv.private_bytes_raw().hex(), priv.public_key().public_bytes_raw().hex()


def public_key_of(private_key_hex: str) -> str:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex)).public_key().public_bytes_raw().hex()


def sign_bytes(message: bytes, private_key_hex: str) -> str:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex)).sign(message).hex()


def verify_bytes(signature_hex: str, message: bytes, public_key_hex: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def parse_time(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---- certificates and revocations ----------------------------------------------------------------------
def issue_key_cert(cert: dict[str, Any], root_private_hex: str) -> dict[str, Any]:
    if cert.get("schema") != KEY_CERT_SCHEMA:
        raise KeyCertError("schema", "bad cert schema")
    if cert.get("key_id") != key_id(cert["public_key"]):
        raise KeyCertError("schema", "cert.key_id does not match public_key")
    if not parse_time(cert["not_before"]) < parse_time(cert["not_after"]):
        raise KeyCertError("schema", "cert validity window is empty")
    return {"cert": cert, "root_signature": sign_canonical_body(cert, root_private_hex)}


def verify_revocations(signed_list: dict[str, Any] | None, root_public_hex: str) -> dict[str, Any] | None:
    if not signed_list:
        return None
    lst = signed_list.get("list") or {}
    if lst.get("schema") != REVOCATIONS_SCHEMA or lst.get("root_id") != key_id(root_public_hex):
        return None
    return lst if verify_bytes(signed_list.get("root_signature", ""), canonical_json(lst).encode(), root_public_hex) else None


def verify_key_cert(signed_cert: dict[str, Any] | None, root_public_hex: str, purpose: str, *, at: datetime | None = None,
                    revocations: dict[str, Any] | None = None, sticky_revoked: set[str] | None = None,
                    skew: timedelta = DEFAULT_SKEW) -> dict[str, Any]:
    """Return the certificate body or raise KeyCertError with the TS reason vocabulary."""
    c = (signed_cert or {}).get("cert")
    if not c or c.get("schema") != KEY_CERT_SCHEMA:
        raise KeyCertError("schema", "key certificate missing or malformed")
    if c.get("purpose") != purpose:
        raise KeyCertError("schema", f"certificate purpose {c.get('purpose')!r} is not {purpose!r}")
    if c.get("root_id") != key_id(root_public_hex):
        raise KeyCertError("root", "certificate was issued under a different root")
    if c.get("key_id") != key_id(c["public_key"]):
        raise KeyCertError("schema", "certificate key_id does not match its public key")
    if not verify_bytes(signed_cert.get("root_signature", ""), canonical_json(c).encode(), root_public_hex):
        raise KeyCertError("root", "certificate signature does not verify under the trusted root")
    t = at or now_utc()
    if t + skew < parse_time(c["not_before"]):
        raise KeyCertError("cert-not-yet-valid", f"key {c['key_id']} valid from {c['not_before']}")
    if t - skew > parse_time(c["not_after"]):
        raise KeyCertError("cert-expired", f"key {c['key_id']} expired {c['not_after']}")
    rev = verify_revocations(revocations, root_public_hex)
    for r in (rev or {}).get("revoked", []):
        if r.get("key_id") == c["key_id"]:
            raise KeyCertError("revoked", f"key {c['key_id']} revoked {r.get('at')}: {r.get('reason')}")
    if sticky_revoked and c["key_id"] in sticky_revoked:
        raise KeyCertError("revoked", f"key {c['key_id']} was revoked by a list seen earlier")
    return c


# ---- signed objects --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SignedObject:
    """{schema, object, signature, key_id, key_cert}: the object's canonical JSON is what was signed."""
    object: dict[str, Any]
    signature: str
    key_id: str
    key_cert: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {"schema": SIGNED_OBJECT_SCHEMA, "object": self.object, "signature": self.signature, "key_id": self.key_id, "key_cert": self.key_cert}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "SignedObject":
        if d.get("schema") != SIGNED_OBJECT_SCHEMA:
            raise KeyCertError("schema", f"expected {SIGNED_OBJECT_SCHEMA}")
        return SignedObject(object=d["object"], signature=d["signature"], key_id=d["key_id"], key_cert=d["key_cert"])


def sign_canonical_body(body: dict[str, Any], private_key_hex: str) -> str:
    """Sign a document that is canonicalised directly rather than wrapped in a `SignedObject` -- certificates,
    today, here and in `mark_product.trust`.

    **The refusals live here, not at the call sites.** A signed document that does not pass the signer's own
    refusals is a signed document with none of them, and which package the issuer lives in cannot change that
    (founder ruling 2026-09-21; D15 already has the product reusing these modules rather than copying them).
    `sign_object` is the other shape, and enforces the same two rules.
    """
    refuse_unportable(body)
    refuse_integer_timestamps(body)
    return sign_bytes(canonical_json(body).encode(), private_key_hex)


def sign_object(obj: dict[str, Any], private_key_hex: str, signed_cert: dict[str, Any]) -> SignedObject:
    # **A9: nothing is signed that another implementation may render differently.** The single chokepoint --
    # `sign_manifest` and everything else reaches signing through here -- so the rule cannot be bypassed by a
    # new caller that forgot it. A signature over a document two verifiers hash differently is worse than no
    # signature: it is a good signature that looks invalid, which discredits real evidence.
    refuse_unportable(obj)
    # A9c: and the shape rule beside it, because the magnitude rule above only fires once a monotonic clock is
    # large enough -- which depends on the signing host's uptime, not on the document.
    refuse_integer_timestamps(obj)
    msg = canonical_json(obj).encode()
    sig = sign_bytes(msg, private_key_hex)
    pub = signed_cert["cert"]["public_key"]
    if not verify_bytes(sig, msg, pub):
        raise KeyCertError("key", "signer output does not verify under the certified public key (wrong key)")
    return SignedObject(object=obj, signature=sig, key_id=signed_cert["cert"]["key_id"], key_cert=signed_cert)


def verify_signed_object(so: SignedObject, root_public_hex: str, purpose: str, *, at: datetime | None = None,
                         revocations: dict[str, Any] | None = None, issued_at_field: str | None = "issued_at") -> dict[str, Any]:
    """Verify the certificate under the root for `purpose`, then the signature. If the object carries an
    `issued_at` it must lie inside the key's validity (the bundle-manifest rule). Returns the object."""
    cert = verify_key_cert(so.key_cert, root_public_hex, purpose, at=at, revocations=revocations)
    if so.key_id != cert["key_id"]:
        raise KeyCertError("key", "signed object key_id does not match its certificate")
    if issued_at_field and issued_at_field in so.object:
        issued = parse_time(so.object[issued_at_field])
        if issued < parse_time(cert["not_before"]) - DEFAULT_SKEW or issued > parse_time(cert["not_after"]) + DEFAULT_SKEW:
            raise KeyCertError("key", f"{issued_at_field} {so.object[issued_at_field]} lies outside the signing key validity window")
    if not verify_bytes(so.signature, canonical_json(so.object).encode(), cert["public_key"]):
        raise KeyCertError("signature", "object signature does not verify under the certified key")
    return so.object
