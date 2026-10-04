"""External anchoring of chain roots (Constitution rule 2: "externally anchored").

One interface, three authorities:
  local-only   offline pod runs: the intent is recorded with anchored=false; `mark-ledger anchor` on export
               replaces nothing (append-only) and adds a real anchor line for the same root.
  rekor        Sigstore Rekor transparency log (hashedrekord over the chain root, signed by the run key).
  rfc3161      an RFC 3161 time-stamping authority; the receipt is the DER token over the chain root.

An Anchor is {chain_id, chain_root, anchored_at, authority, anchored, receipt}. `check_receipt` is the offline
part of third-party verification: it checks what CAN be checked without the network (the receipt commits to
this chain root) and says exactly which part it checked; full log-inclusion / TSA-certificate verification is
the online step (`mark-ledger verify --online`), not silently assumed.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .keys import iso, now_utc, sign_bytes


@dataclass(frozen=True)
class Anchor:
    chain_id: str
    chain_root: str
    anchored_at: str
    authority: str
    anchored: bool
    receipt: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {"chain_id": self.chain_id, "chain_root": self.chain_root, "anchored_at": self.anchored_at, "authority": self.authority, "anchored": self.anchored, "receipt": self.receipt}


class AnchorClient(Protocol):
    authority: str

    def anchor(self, chain_id: str, chain_root: str) -> Anchor: ...


class LocalOnlyAnchor:
    """Offline: records the intent; anchored=false until an online client anchors the same root."""
    authority = "local-only"

    def anchor(self, chain_id: str, chain_root: str) -> Anchor:
        return Anchor(chain_id, chain_root, iso(now_utc()), self.authority, False, {"note": "recorded offline; anchor on export"})


Transport = Callable[[str, str, dict[str, Any] | None, bytes | None, dict[str, str] | None], tuple[int, bytes]]


def _httpx_transport(method: str, url: str, json_body: dict[str, Any] | None, raw: bytes | None, headers: dict[str, str] | None) -> tuple[int, bytes]:
    import httpx

    r = httpx.request(method, url, json=json_body, content=raw, headers=headers, timeout=30.0)
    return r.status_code, r.content


class RekorAnchor:
    """Sigstore Rekor v1 `hashedrekord`: the artifact is the chain root (already a SHA-256), signed by the run
    manifest key. Rekor returns the log entry (uuid, logIndex, integratedTime, inclusion proof) as the receipt."""
    authority = "rekor"

    def __init__(self, private_key_hex: str, public_key_pem: str, base_url: str = "https://rekor.sigstore.dev", transport: Transport = _httpx_transport) -> None:
        self.private_key_hex = private_key_hex
        self.public_key_pem = public_key_pem
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    @staticmethod
    def artifact(chain_root: str) -> bytes:
        """The artifact Rekor's entry commits to: the chain root as one ASCII line. Its SHA-512 is the entry's
        data hash and the Ed25519 signature is over that digest, so `check_receipt` can recompute both."""
        return f"{chain_root}\n".encode()

    def anchor(self, chain_id: str, chain_root: str) -> Anchor:
        # Live findings 2026-09-11: Rekor's `hashedrekord` verifies Ed25519 as Ed25519ph over a SHA-512 digest,
        # which neither `cryptography` nor the ledger's signing helpers produce (the standard Ed25519 signature is
        # "invalid signature" there). The `rekord` type carries the artifact inline (it is one 65-byte line) and
        # verifies a standard Ed25519 signature over it with an x509 SPKI public key, so it is what is used, and
        # `check_receipt` can re-verify the signature fully offline from the entry body alone.
        art = self.artifact(chain_root)
        sig = bytes.fromhex(sign_bytes(art, self.private_key_hex))
        body = {
            "apiVersion": "0.0.1",
            "kind": "rekord",
            "spec": {
                "signature": {"format": "x509", "content": base64.b64encode(sig).decode(), "publicKey": {"content": base64.b64encode(self.public_key_pem.encode()).decode()}},
                "data": {"content": base64.b64encode(art).decode()},
            },
        }
        status, content = self.transport("POST", f"{self.base_url}/api/v1/log/entries", body, None, {"Content-Type": "application/json", "Accept": "application/json"})
        already = False
        if status == 409:
            # The log already holds exactly this entry (same artifact, same signature). That is the proof we came
            # for, so anchoring is idempotent: fetch the existing entry by the UUID Rekor names and record it,
            # flagged as pre-existing. Raising here made a retry of `run sign --anchor rekor` fail outright, which
            # is the wrong answer to "the thing you wanted is already true" (found on the post-run dress rehearsal,
            # 2026-09-12, before three decisive bundles depended on the retry path).
            import re as _re

            msg = json.loads(content).get("message") or ""
            m = _re.search(r"UUID\s+([0-9a-f]{16,})", msg)      # a whole-message fallback would fetch garbage
            if not m:
                raise RuntimeError(f"rekor returned 409 without a UUID: {content[:200]!r}")
            uuid = m.group(1)
            status, content = self.transport("GET", f"{self.base_url}/api/v1/log/entries/{uuid}", None, None, {"Accept": "application/json"})
            if status != 200:
                raise RuntimeError(f"rekor 409 named {uuid} but fetching it returned {status}: {content[:200]!r}")
            already = True
        elif status not in (200, 201):
            raise RuntimeError(f"rekor returned {status}: {content[:200]!r}")
        entries = json.loads(content)
        uuid, entry = next(iter(entries.items()))
        receipt = {"uuid": uuid, "logIndex": entry.get("logIndex"), "integratedTime": entry.get("integratedTime"), "logID": entry.get("logID"), "body": entry.get("body"), "verification": entry.get("verification"),
                   "url": f"{self.base_url}/api/v1/log/entries/{uuid}", "pre_existing_entry": already}
        return Anchor(chain_id, chain_root, iso(now_utc()), self.authority, True, receipt)


class Rfc3161Anchor:
    """RFC 3161 time-stamp over the chain root. The request is built with `rfc3161-client` (Sigstore's), so the
    dependency is only needed when this authority is used; the DER token is the receipt."""
    authority = "rfc3161"

    def __init__(self, tsa_url: str, transport: Transport = _httpx_transport) -> None:
        self.tsa_url = tsa_url
        self.transport = transport

    def anchor(self, chain_id: str, chain_root: str) -> Anchor:
        from rfc3161_client import TimestampRequestBuilder, decode_timestamp_response  # type: ignore

        req = TimestampRequestBuilder().data(bytes.fromhex(chain_root)).nonce(nonce=True).build()
        status, content = self.transport("POST", self.tsa_url, None, req.as_bytes(), {"Content-Type": "application/timestamp-query"})
        if status != 200:
            raise RuntimeError(f"tsa returned {status}")
        resp = decode_timestamp_response(content)
        tst = resp.tst_info
        receipt = {"token_der_b64": base64.b64encode(content).decode(), "gen_time": tst.gen_time.isoformat(), "serial": str(tst.serial_number), "policy": str(tst.policy), "tsa_url": self.tsa_url}
        return Anchor(chain_id, chain_root, iso(now_utc()), self.authority, True, receipt)


def check_receipt(anchor: dict[str, Any]) -> tuple[bool, str]:
    """Offline receipt check. Returns (ok, what-was-checked). Never claims more than it verified."""
    auth = anchor.get("authority")
    rc = anchor.get("receipt") or {}
    root = anchor.get("chain_root", "")
    if auth == "local-only":
        return (not anchor.get("anchored", False)), "local-only intent; not an external anchor"
    if auth == "rekor":
        body_b64 = rc.get("body")
        if not body_b64:
            return False, "rekor receipt has no body"
        try:
            body = json.loads(base64.b64decode(body_b64))
            spec = body["spec"]
            if body.get("kind") == "rekord":
                # Rekor's canonicalised rekord body drops the inline content and keeps sha256(content), the
                # signature and the public key; the artifact is reconstructed from the chain root and checked.
                art = RekorAnchor.artifact(root)
                h = spec["data"]["hash"]
                if h.get("algorithm") != "sha256" or h.get("value") != hashlib.sha256(art).hexdigest():
                    return False, "rekor entry hash differs from this chain root's artifact"
                sig = base64.b64decode(spec["signature"]["content"])
                pem = base64.b64decode(spec["signature"]["publicKey"]["content"])
                from cryptography.hazmat.primitives import serialization

                pub = serialization.load_pem_public_key(pem)
                pub.verify(sig, art)   # raises on mismatch
                return True, "rekor entry commits to sha256 of this chain root's artifact and its Ed25519 signature verifies with the embedded key (offline); log inclusion not checked offline"
            value = spec["data"]["hash"]["value"]
        except Exception as e:  # noqa: BLE001
            return False, f"rekor body undecodable or signature invalid: {type(e).__name__}: {e}"
        if value != hashlib.sha512(RekorAnchor.artifact(root)).hexdigest():
            return False, "rekor entry body commits to a different hash than this chain root"
        return True, "rekor entry body commits to this chain root (offline); log inclusion not checked offline"
    if auth == "rfc3161":
        tok = rc.get("token_der_b64")
        if not tok:
            return False, "rfc3161 receipt has no token"
        try:
            from rfc3161_client import decode_timestamp_response  # type: ignore

            resp = decode_timestamp_response(base64.b64decode(tok))
            digest = resp.tst_info.message_imprint.message
            if digest.hex() != root:
                return False, "timestamp token imprint differs from this chain root"
            return True, "timestamp token imprint matches this chain root (offline); TSA certificate chain not checked offline"
        except ImportError:
            return True, "rfc3161 token present; rfc3161-client not installed, imprint not decoded"
        except Exception as e:  # noqa: BLE001
            return False, f"rfc3161 token undecodable: {e}"
    return False, f"unknown authority {auth!r}"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ed25519_public_pem(private_key_hex: str) -> str:
    """The signing key's public half as SubjectPublicKeyInfo PEM, the form Rekor accepts."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
