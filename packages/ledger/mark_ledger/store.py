"""The evidence ledger: append-only, hash-chained records with full provenance, over content-addressed payloads.

Layout under a ledger directory (local disk in this pass; the same interface fronts S3 later):
  objects/<sha256[:2]>/<sha256>        payload bytes, content-addressed
  chains/<chain_id>.jsonl              one EvidenceRecord per line, in order; each carries prev_hash and hash
  anchors/<chain_id>.jsonl             Anchor receipts for chain roots (see anchor.py)

hash = sha256(canonical_json(record without `hash`)); prev_hash of the first record is 64 zeros. The chain root
is the hash of the last record. `verify` re-derives every hash and link, checks every payload is present and
matches its content_hash, and checks every anchor names a root that is (or was) the head of this chain.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .canonical import canonical_json, sha256_hex
from .keys import iso, now_utc

GENESIS = "0" * 64
RECORD_SCHEMA = "mark.evidence-record/1"


@dataclass(frozen=True)
class Provenance:
    """Who/what produced the evidence. Every field is a pin or an actor, never free text about the result."""
    engine_version: str
    environment_fingerprint: str
    actor: str
    probe_id: str | None = None
    probe_version: str | None = None
    workload_id: str | None = None
    workload_version: str | None = None
    model_hash: str | None = None
    control_id: str | None = None
    control_version: str | None = None
    target_id: str | None = None
    target_version: str | None = None

    def to_json(self) -> dict[str, Any]:
        return dict(asdict(self))


@dataclass(frozen=True)
class EvidenceRecord:
    schema: str
    id: str
    chain_id: str
    seq: int
    kind: str
    content_hash: str
    provenance: dict[str, Any]
    prev_hash: str
    created_at: str
    hash: str = ""

    def body(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("hash")
        return d

    def compute_hash(self) -> str:
        return sha256_hex(canonical_json(self.body()))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "EvidenceRecord":
        return EvidenceRecord(**d)


@dataclass
class LedgerVerification:
    chain_id: str
    ok: bool
    records: int
    chain_root: str | None
    problems: list[str] = field(default_factory=list)
    anchors: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class Ledger:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        for d in ("objects", "chains", "anchors"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    # ---- objects ----
    def put_object(self, data: bytes | str | dict | list) -> str:
        """Store a payload; dict/list payloads are stored as their canonical JSON so the hash is structural."""
        if isinstance(data, (dict, list)):
            data = canonical_json(data).encode()
        elif isinstance(data, str):
            data = data.encode()
        h = sha256_hex(data)
        p = self._object_path(h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return h

    def get_object(self, content_hash: str) -> bytes:
        return self._object_path(content_hash).read_bytes()

    def has_object(self, content_hash: str) -> bool:
        return self._object_path(content_hash).exists()

    def _object_path(self, h: str) -> Path:
        if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
            raise ValueError(f"not a sha256 hex: {h!r}")
        return self.root / "objects" / h[:2] / h

    # ---- chains ----
    def _chain_path(self, chain_id: str) -> Path:
        if not chain_id or "/" in chain_id or "\\" in chain_id or chain_id.startswith("."):
            raise ValueError(f"bad chain id {chain_id!r}")
        return self.root / "chains" / f"{chain_id}.jsonl"

    def records(self, chain_id: str) -> Iterator[EvidenceRecord]:
        p = self._chain_path(chain_id)
        if not p.exists():
            return
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield EvidenceRecord.from_json(json.loads(line))

    def head(self, chain_id: str) -> EvidenceRecord | None:
        last = None
        for r in self.records(chain_id):
            last = r
        return last

    def chain_root(self, chain_id: str) -> str | None:
        h = self.head(chain_id)
        return h.hash if h else None

    def list_chains(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "chains").glob("*.jsonl"))

    def append(self, chain_id: str, kind: str, payload: bytes | str | dict | list, provenance: Provenance, *, created_at: str | None = None) -> EvidenceRecord:
        """Append one record. The payload is stored first (content-addressed), then the record is chained to the
        current head. Append-only: nothing here can rewrite a line; a rewrite shows in `verify`."""
        content_hash = self.put_object(payload)
        head = self.head(chain_id)
        rec = EvidenceRecord(
            schema=RECORD_SCHEMA,
            id=uuid.uuid4().hex,
            chain_id=chain_id,
            seq=(head.seq + 1) if head else 0,
            kind=kind,
            content_hash=content_hash,
            provenance=provenance.to_json(),
            prev_hash=head.hash if head else GENESIS,
            created_at=created_at or iso(now_utc()),
        )
        rec = EvidenceRecord(**{**asdict(rec), "hash": rec.compute_hash()})
        with self._chain_path(chain_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_json(), separators=(",", ":"), sort_keys=True) + "\n")
        return rec

    # ---- anchors ----
    def _anchor_path(self, chain_id: str) -> Path:
        self._chain_path(chain_id)  # validates the id
        return self.root / "anchors" / f"{chain_id}.jsonl"

    def record_anchor(self, anchor: dict[str, Any]) -> None:
        with self._anchor_path(anchor["chain_id"]).open("a", encoding="utf-8") as f:
            f.write(json.dumps(anchor, separators=(",", ":"), sort_keys=True) + "\n")

    def anchors(self, chain_id: str) -> list[dict[str, Any]]:
        p = self._anchor_path(chain_id)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ---- verification: the third-party path ----
    def verify(self, chain_id: str, *, check_receipts: bool = True) -> LedgerVerification:
        from .anchor import check_receipt  # local import: anchor depends on store types

        problems: list[str] = []
        seen_hashes: list[str] = []
        prev = GENESIS
        n = 0
        for r in self.records(chain_id):
            if r.schema != RECORD_SCHEMA:
                problems.append(f"seq {r.seq}: schema {r.schema}")
            if r.seq != n:
                problems.append(f"seq {r.seq}: expected seq {n}")
            if r.prev_hash != prev:
                problems.append(f"seq {r.seq}: prev_hash {r.prev_hash[:12]} != head {prev[:12]}")
            if r.compute_hash() != r.hash:
                problems.append(f"seq {r.seq}: hash does not match its content")
            if not self.has_object(r.content_hash):
                problems.append(f"seq {r.seq}: payload {r.content_hash[:12]} missing")
            elif sha256_hex(self.get_object(r.content_hash)) != r.content_hash:
                problems.append(f"seq {r.seq}: payload bytes do not match content_hash")
            prev = r.hash
            seen_hashes.append(r.hash)
            n += 1
        anchor_reports = []
        for a in self.anchors(chain_id):
            rep = {"authority": a.get("authority"), "chain_root": a.get("chain_root"), "anchored": a.get("anchored", False), "ok": True, "detail": ""}
            if a.get("chain_root") not in seen_hashes:
                rep["ok"] = False
                rep["detail"] = "anchor names a root that was never a head of this chain"
            elif check_receipts:
                ok, detail = check_receipt(a)
                rep["ok"], rep["detail"] = ok, detail
            if not rep["ok"]:
                problems.append(f"anchor {a.get('authority')}: {rep['detail']}")
            anchor_reports.append(rep)
        return LedgerVerification(chain_id=chain_id, ok=not problems and n > 0, records=n, chain_root=prev if n else None, problems=problems, anchors=anchor_reports)
