"""Gate: the signed, pre-registered decision rule of a probe family (Constitution rule 1).

A gate is a JSON object {schema, gate_id, version, probe_family, issued_at, thresholds, preconditions,
outcome_labels, why} signed by a certificate with purpose 'gate' under the root. `load_gate` verifies the
signature; an unsigned or unverifiable gate is still LOADED (its thresholds are applied so the report can show
what the numbers would say) but `signed=False`, and every verdict it produces is `informational`.

The draft the agent writes lives next to the signed one: gates/<gate_id>.draft.json (unsigned) and
gates/<gate_id>.signed.json (the founder's signature). The report names which one was used.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mark_ledger.canonical import object_hash
from mark_ledger.keys import KeyCertError, SignedObject, verify_signed_object

GATE_SCHEMA = "mark.probe-gate/1"
GATE_KEY_PURPOSE = "gate"
INFORMATIONAL = "informational"


@dataclass(frozen=True)
class Gate:
    gate_id: str
    version: int
    probe_family: str
    thresholds: dict[str, Any]
    preconditions: dict[str, Any]
    outcome_labels: list[str]
    why: dict[str, str]
    issued_at: str | None
    signed: bool
    signed_by: str | None
    gate_hash: str
    source: str
    unsigned_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"gate_id": self.gate_id, "version": self.version, "probe_family": self.probe_family, "thresholds": self.thresholds, "preconditions": self.preconditions,
                "outcome_labels": self.outcome_labels, "issued_at": self.issued_at, "signed": self.signed, "signed_by": self.signed_by, "gate_hash": self.gate_hash, "source": self.source,
                "unsigned_reason": self.unsigned_reason}


def gate_body(gate_id: str, version: int, probe_family: str, thresholds: dict[str, Any], preconditions: dict[str, Any], outcome_labels: list[str], why: dict[str, str], issued_at: str | None = None) -> dict[str, Any]:
    body = {"schema": GATE_SCHEMA, "gate_id": gate_id, "version": version, "probe_family": probe_family, "thresholds": thresholds, "preconditions": preconditions, "outcome_labels": outcome_labels, "why": why}
    if issued_at:
        body["issued_at"] = issued_at
    return body


def _from_body(body: dict[str, Any], *, signed: bool, signed_by: str | None, source: str, unsigned_reason: str = "") -> Gate:
    if body.get("schema") != GATE_SCHEMA:
        raise ValueError(f"not a gate: {body.get('schema')}")
    return Gate(gate_id=body["gate_id"], version=int(body["version"]), probe_family=body["probe_family"], thresholds=dict(body["thresholds"]), preconditions=dict(body.get("preconditions") or {}),
                outcome_labels=list(body["outcome_labels"]), why=dict(body.get("why") or {}), issued_at=body.get("issued_at"), signed=signed, signed_by=signed_by, gate_hash=object_hash(body), source=source, unsigned_reason=unsigned_reason)


def load_gate(gates_dir: str | Path, gate_id: str, root_public_hex: str | None, revocations: dict[str, Any] | None = None) -> Gate:
    """Prefer <gate_id>.signed.json if it verifies under the root; else the draft, unsigned."""
    d = Path(gates_dir)
    signed_p, draft_p = d / f"{gate_id}.signed.json", d / f"{gate_id}.draft.json"
    if signed_p.exists():
        raw = json.loads(signed_p.read_text(encoding="utf-8"))
        if root_public_hex is None:
            return _from_body(raw["object"], signed=False, signed_by=None, source=str(signed_p), unsigned_reason="no root public key available to verify the signature")
        try:
            so = SignedObject.from_json(raw)
            body = verify_signed_object(so, root_public_hex, GATE_KEY_PURPOSE, revocations=revocations)
            return _from_body(body, signed=True, signed_by=so.key_id, source=str(signed_p))
        except (KeyCertError, KeyError, ValueError) as e:
            return _from_body(raw.get("object", {}), signed=False, signed_by=None, source=str(signed_p), unsigned_reason=f"signature does not verify: {e}")
    if draft_p.exists():
        return _from_body(json.loads(draft_p.read_text(encoding="utf-8")), signed=False, signed_by=None, source=str(draft_p), unsigned_reason="draft; not signed by the founder")
    raise FileNotFoundError(f"no gate {gate_id} in {d}")


def absent_gate(gate_id: str, probe_family: str) -> Gate:
    """A probe with no gate file at all: nothing can be decided, every verdict is informational, and the reason
    says the gate has not even been drafted. Used so a declared-but-ungated probe still produces a result record."""
    return Gate(gate_id=gate_id, version=0, probe_family=probe_family, thresholds={}, preconditions={}, outcome_labels=[], why={}, issued_at=None, signed=False, signed_by=None,
                gate_hash=object_hash({"gate_id": gate_id, "absent": True}), source="(none)", unsigned_reason="no gate file drafted for this probe")


@dataclass
class Verdict:
    label: str
    decisive: bool
    outcome_if_decisive: str | None
    reasons: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "decisive": self.decisive, "outcome_if_decisive": self.outcome_if_decisive, "reasons": self.reasons, "gate": self.gate}


def decide(gate: Gate, outcome: str | None, precondition_failures: list[str]) -> Verdict:
    """The one place a verdict is formed. `outcome` is what the numbers say (a label from the gate, or None when
    undefined); the label is decisive only when the gate is signed and every precondition passed."""
    reasons = list(precondition_failures)
    if not gate.signed:
        reasons.append(f"gate not signed ({gate.unsigned_reason})")
    if outcome is None:
        reasons.append("outcome undefined (no denominator or no measurement)")
    elif outcome not in gate.outcome_labels:
        # the probe produced a reading the signed gate does not name (a probe version ahead of its gate):
        # informational, never a crash, and the reason says which label the next gate version must carry
        reasons.append(f"outcome {outcome!r} is not a label of gate {gate.gate_id} v{gate.version} ({gate.outcome_labels}); a signed gate must name every outcome the probe can produce")
    decisive = not reasons
    return Verdict(label=outcome if decisive else INFORMATIONAL, decisive=decisive, outcome_if_decisive=outcome, reasons=reasons, gate=gate.to_json())
