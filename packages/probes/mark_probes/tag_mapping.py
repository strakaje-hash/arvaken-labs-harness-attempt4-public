"""Tag mapping: the signed artifact that says which probe outcomes demonstrate which control-family tag state (attempt 4, C1).

The registry records what a project CLAIMS for each family tag; a run demonstrates a tag, and until attempt 4 the report
derived that from a table in code. The mapping is now an artifact with a version and a hash, signed by the founder like a
gate: mappings/<mapping_id>.draft.json (unsigned) beside mappings/<mapping_id>.signed.json. The runtime prefers the signed
file and falls back to the draft as unsigned; the bundle pins which one was used and every row carries its outcomes
beside the mapping's hash. The product's `demonstrated` column reads those outcomes and never derives one (D32).

One reading is supported in v1: `by_halt_class` -- the per-class counts a probe aggregate carries -- with a set of classes
that demonstrate the tag and a set that demonstrate its absence in the tested configuration. A tag with no rule, a row on
a probe the rule does not read, or a row with no measured replication yields no outcome: unverified, never absent."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mark_ledger.canonical import object_hash
from mark_ledger.keys import KeyCertError, SignedObject, verify_signed_object

MAPPING_SCHEMA = "mark.tag-mapping/1"
MAPPING_KEY_PURPOSE = "gate"   # the mapping is a decision rule pre-registered like a gate; the gate key signs it
READINGS = ("by_halt_class",)


class TagMappingError(ValueError):
    pass


@dataclass(frozen=True)
class TagMapping:
    mapping_id: str
    version: int
    family_tags: tuple[str, ...]
    rules: tuple[dict[str, Any], ...]
    why: dict[str, str]
    issued_at: str | None
    signed: bool
    signed_by: str | None
    mapping_hash: str
    source: str
    unsigned_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"id": self.mapping_id, "version": self.version, "hash": self.mapping_hash, "signed": self.signed, "signed_by": self.signed_by, "issued_at": self.issued_at,
                "source": self.source, "unsigned_reason": self.unsigned_reason, "tags_with_a_rule": sorted({r["tag"] for r in self.rules})}

    def ref(self) -> dict[str, Any]:
        """What a row carries beside its outcomes: enough to find the mapping that produced them."""
        return {"id": self.mapping_id, "version": self.version, "hash": self.mapping_hash, "signed": self.signed}


def _validate(body: dict[str, Any]) -> None:
    if body.get("schema") != MAPPING_SCHEMA:
        raise TagMappingError(f"not a tag mapping: {body.get('schema')!r}")
    if not body.get("mapping_id") or not isinstance(body.get("version"), int) or body["version"] < 1:
        raise TagMappingError("a tag mapping has a mapping_id and an integer version >= 1")
    tags = body.get("family_tags")
    if not isinstance(tags, list) or not tags or any(not isinstance(t, str) for t in tags):
        raise TagMappingError("family_tags is the list of control-family tags the mapping may name")
    rules = body.get("rules")
    if not isinstance(rules, list):
        raise TagMappingError("rules is a list")
    for r in rules:
        if not isinstance(r, dict) or r.get("tag") not in tags:
            raise TagMappingError(f"a rule names a tag in family_tags, got {r!r}")
        if not isinstance(r.get("probe"), str) or not r["probe"]:
            raise TagMappingError(f"rule for {r['tag']}: names the probe it reads")
        if r.get("reads") not in READINGS:
            raise TagMappingError(f"rule for {r['tag']}: reads must be one of {READINGS}, got {r.get('reads')!r}")
        sets = {}
        for side in ("demonstrated", "absent"):
            spec = r.get(side)
            if not isinstance(spec, dict) or not isinstance(spec.get("classes"), list) or not spec["classes"] or any(not isinstance(c, str) for c in spec["classes"]):
                raise TagMappingError(f"rule for {r['tag']}: {side}.classes is a non-empty list of class names")
            if not isinstance(spec.get("wording"), str) or not spec["wording"].strip():
                raise TagMappingError(f"rule for {r['tag']}: {side}.wording is the phrase the reading prints")
            sets[side] = set(spec["classes"])
        if sets["demonstrated"] & sets["absent"]:
            raise TagMappingError(f"rule for {r['tag']}: a class cannot both demonstrate the tag and its absence: {sorted(sets['demonstrated'] & sets['absent'])}")
    if not isinstance(body.get("why"), dict) or not body["why"]:
        raise TagMappingError("why records the decision in the founder's words")


def _from_body(body: dict[str, Any], *, signed: bool, signed_by: str | None, source: str, unsigned_reason: str = "") -> TagMapping:
    _validate(body)
    return TagMapping(mapping_id=body["mapping_id"], version=int(body["version"]), family_tags=tuple(body["family_tags"]), rules=tuple(dict(r) for r in body["rules"]),
                      why=dict(body["why"]), issued_at=body.get("issued_at"), signed=signed, signed_by=signed_by, mapping_hash=object_hash(body), source=source, unsigned_reason=unsigned_reason)


def load_tag_mapping(mappings_dir: str | Path, mapping_id: str, root_public_hex: str | None, revocations: dict[str, Any] | None = None) -> TagMapping:
    """Prefer <mapping_id>.signed.json if it verifies under the root; else the draft, unsigned. No file at all is a broken
    instrument, not a missing feature: the caller refuses to run."""
    d = Path(mappings_dir)
    signed_p, draft_p = d / f"{mapping_id}.signed.json", d / f"{mapping_id}.draft.json"
    if signed_p.exists():
        raw = json.loads(signed_p.read_text(encoding="utf-8"))
        if root_public_hex is None:
            return _from_body(raw["object"], signed=False, signed_by=None, source=str(signed_p), unsigned_reason="no root public key available to verify the signature")
        try:
            so = SignedObject.from_json(raw)
            body = verify_signed_object(so, root_public_hex, MAPPING_KEY_PURPOSE, revocations=revocations)
            return _from_body(body, signed=True, signed_by=so.key_id, source=str(signed_p))
        except (KeyCertError, KeyError, ValueError) as e:
            return _from_body(raw.get("object", {}), signed=False, signed_by=None, source=str(signed_p), unsigned_reason=f"signature does not verify: {e}")
    if draft_p.exists():
        return _from_body(json.loads(draft_p.read_text(encoding="utf-8")), signed=False, signed_by=None, source=str(draft_p), unsigned_reason="draft; not signed by the founder")
    raise FileNotFoundError(f"no tag mapping {mapping_id} in {d}")


def tag_outcomes(mapping: TagMapping, probe_id: str, agg: dict[str, Any]) -> dict[str, Any]:
    """The row's per-tag outcomes under the mapping: {"mapping": ref, "by_tag": {tag: {held, absent, n, reading, rule}}}.
    A tag appears only when the mapping has a rule reading this probe and at least one measured replication fell in a
    named class; everything else is unverified and is not written, so an absent key never reads as absent."""
    by_tag: dict[str, Any] = {}
    n = int(agg.get("n") or 0)
    for r in mapping.rules:
        if r["probe"] != probe_id or n <= 0:
            continue
        classes = agg.get(r["reads"]) or {}
        held = sum(int((classes.get(c) or {}).get("n") or 0) for c in r["demonstrated"]["classes"])
        absent = sum(int((classes.get(c) or {}).get("n") or 0) for c in r["absent"]["classes"])
        if not held and not absent:
            continue
        parts = [f"{r['demonstrated']['wording']} in {held} of {n}" if held else "", f"{r['absent']['wording']} in {absent} of {n}" if absent else ""]
        by_tag[r["tag"]] = {"held": held, "absent": absent, "n": n, "reading": ", ".join(p for p in parts if p),
                            "rule": {"probe": r["probe"], "reads": r["reads"], "demonstrated": list(r["demonstrated"]["classes"]), "absent": list(r["absent"]["classes"])}}
    return {"mapping": mapping.ref(), "by_tag": by_tag}
