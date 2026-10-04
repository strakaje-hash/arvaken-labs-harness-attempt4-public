"""Interfaces for the next passes (Task 7): specified with type definitions and invariant tests, not built.

  Connector            discover() -> [AISystem]; permissions_manifest() is READ-ONLY (test-asserted)
  Crosswalk            crosswalks/<framework>/<version>.yaml: requirement -> evidence types -> probe families; signed
  Attestation          valid is DERIVED: certified assessor signatures under the hierarchy (two for third-party
                       scope), not revoked, last_confirmed_run within the gate's allowed staleness
  attestation_status   the verification endpoint's payload (GET /attestations/{id}/status): never valid=true
                       for an unsigned, revoked or stale object; every static export points at the live endpoint
  Assessor             max_live_attestations cap enforced at signature time (value set in Layer 2)
  FindingsSynthesizer  proposes findings with evidence links; never writes a rating
  DriftMonitor         state_change vs probe_failure; only a confirmed state_change can revoke (tripwire)
  Portal/API           verifiable bundle download; REST + MCP endpoints (shape only)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from mark_ledger.canonical import object_hash
from mark_ledger.keys import KeyCertError, SignedObject, parse_time, sign_object, verify_signed_object

ATTESTATION_KEY_PURPOSE = "attestation"
READ_ONLY_ACTIONS_PREFIXES = ("Describe", "Get", "List", "Read", "View", "Lookup", "Search", "Query", "Head", "Select")


# ---- discovery -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AISystem:
    id: str
    provider: str
    kind: str                       # model-endpoint | agent-runtime | vector-store | pipeline | unknown
    region: str | None
    attributes: dict[str, Any] = field(default_factory=dict)
    discovered_via: str = ""        # connector id + api call


@dataclass(frozen=True)
class PermissionsManifest:
    connector: str
    actions: tuple[str, ...]        # cloud API actions the connector needs

    def is_read_only(self) -> bool:
        return all(_read_only(a) for a in self.actions)


def _read_only(action: str) -> bool:
    verb = action.split(":", 1)[-1]
    return verb.startswith(READ_ONLY_ACTIONS_PREFIXES)


class Connector(Protocol):
    id: str

    def permissions_manifest(self) -> PermissionsManifest: ...

    def discover(self) -> list[AISystem]: ...


class AwsConnectorStub:
    """First implementation target. The manifest is the contract; discover() is not built in this pass."""
    id = "aws"

    def permissions_manifest(self) -> PermissionsManifest:
        return PermissionsManifest(self.id, ("bedrock:ListFoundationModels", "bedrock:ListAgents", "bedrock:GetAgent", "sagemaker:ListEndpoints", "sagemaker:DescribeEndpoint",
                                             "bedrock:ListKnowledgeBases", "lambda:ListFunctions", "lambda:GetFunctionConfiguration", "ec2:DescribeInstances"))

    def discover(self) -> list[AISystem]:
        raise NotImplementedError("AWS discovery is specified, not built, in this pass")


# ---- crosswalks ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CrosswalkEntry:
    requirement_id: str             # e.g. "EU-AI-Act:Art.14(4)(e)"
    evidence_types: tuple[str, ...] # e.g. ("kill-switch:latency", "kill-switch:completeness")
    probe_families: tuple[str, ...]
    logic: str                      # how the evidence satisfies the requirement, in words the assessor signs


@dataclass(frozen=True)
class Crosswalk:
    framework: str
    version: str
    entries: tuple[CrosswalkEntry, ...]
    signed: bool = False


# ---- assessors -------------------------------------------------------------------------------------------
@dataclass
class Assessor:
    """Assessor-load cap: set from policy (E&O terms + the two-assessor rule) in Layer 2; enforced at signature time."""
    key_id: str
    max_live_attestations: int
    live_attestations: int = 0

    def can_sign(self) -> bool:
        return self.live_attestations < self.max_live_attestations


class AssessorCapExceeded(Exception):
    pass


# ---- attestation -----------------------------------------------------------------------------------------
@dataclass
class Attestation:
    """valid is DERIVED, never set. It is True only when every required signature verifies under the root for
    purpose 'attestation', the two-assessor rule holds for third-party-facing scope, the object is not revoked,
    and last_confirmed_run is within max_staleness of now."""
    id: str
    scope: str
    framework_versions: dict[str, str]
    findings: list[dict[str, Any]]
    assessor_ids: list[str]
    signatures: list[SignedObject]
    baseline_at: str
    last_confirmed_run: str | None = None
    revoked_at: str | None = None
    revocation_reason: str | None = None
    revocation_evidence_ref: str | None = None
    audience: Literal["internal", "third-party"] = "third-party"
    max_staleness: timedelta = timedelta(days=30)   # the gate's allowed staleness (Layer 2 sets it from the crosswalk)

    def _verified_signers(self, root_public_hex: str, revocations: dict[str, Any] | None) -> set[str]:
        verified: set[str] = set()
        for so in self.signatures:
            try:
                obj = verify_signed_object(so, root_public_hex, ATTESTATION_KEY_PURPOSE, revocations=revocations)
            except (KeyCertError, ValueError, KeyError):
                continue
            if obj.get("scope") == self.scope and obj.get("attestation_id") == self.id and so.key_id in self.assessor_ids:
                verified.add(so.key_id)
        return verified

    def invalid_reasons(self, root_public_hex: str, *, now: datetime | None = None, revocations: dict[str, Any] | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        reasons: list[str] = []
        verified = self._verified_signers(root_public_hex, revocations)
        needed = 2 if self.audience == "third-party" else 1
        if len(verified) < needed or not all(a in verified for a in self.assessor_ids[:needed]):
            reasons.append(f"certified assessor signatures {len(verified)} < {needed}")
        if self.revoked_at:
            reasons.append(f"revoked {self.revoked_at}: {self.revocation_reason}")
        if not self.last_confirmed_run:
            reasons.append("no confirmed run")
        elif now - parse_time(self.last_confirmed_run) > self.max_staleness:
            reasons.append(f"last confirmed run {self.last_confirmed_run} older than allowed staleness {self.max_staleness}")
        return reasons

    def valid(self, root_public_hex: str, *, now: datetime | None = None, revocations: dict[str, Any] | None = None) -> bool:
        return not self.invalid_reasons(root_public_hex, now=now, revocations=revocations)

    def sign(self, assessor: Assessor, private_key_hex: str, signed_cert: dict[str, Any], issued_at: str) -> SignedObject:
        """Signature time is where the assessor-load cap bites."""
        if not assessor.can_sign():
            raise AssessorCapExceeded(f"assessor {assessor.key_id} holds {assessor.live_attestations} live attestations (cap {assessor.max_live_attestations})")
        so = sign_object({"attestation_id": self.id, "scope": self.scope, "issued_at": issued_at}, private_key_hex, signed_cert)
        self.signatures.append(so)
        assessor.live_attestations += 1
        return so

    def revoke(self, reason: str, evidence_ref: str, at: str) -> None:
        self.revoked_at, self.revocation_reason, self.revocation_evidence_ref = at, reason, evidence_ref


def attestation_status(att: Attestation, root_public_hex: str, *, chain_root: str | None, now: datetime | None = None, revocations: dict[str, Any] | None = None) -> dict[str, Any]:
    """Payload of GET /attestations/{id}/status (read-only, tokened for the relying party, signed by the service
    in Layer 2). `valid` here is the derived value and nothing else: the never-true rules are the same code path."""
    reasons = att.invalid_reasons(root_public_hex, now=now, revocations=revocations)
    return {"id": att.id, "valid": not reasons, "reasons": reasons, "baseline_at": att.baseline_at, "last_confirmed_run": att.last_confirmed_run,
            "revoked_at": att.revoked_at, "revocation_reason": att.revocation_reason, "evidence_chain_root": chain_root,
            "live_endpoint": f"/attestations/{att.id}/status"}


def static_export(att: Attestation, status: dict[str, Any]) -> dict[str, Any]:
    """A static copy always carries the pointer to the live endpoint: validity is conditional, never frozen."""
    return {"attestation_id": att.id, "snapshot": status, "live_endpoint": f"/attestations/{att.id}/status", "note": "This export is a snapshot; validity is conditional and is answered by the live endpoint."}


# ---- findings synthesizer --------------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    id: str
    statement: str
    evidence_links: tuple[str, ...]     # ledger record hashes
    proposed_by: str                    # model id (self-hosted) or human id
    # NO rating field: a rating is a human act (assessor), never synthesized.


class FindingsSynthesizer(Protocol):
    def propose(self, ledger_records: list[dict[str, Any]]) -> list[Finding]: ...


# ---- drift monitor + tripwire ----------------------------------------------------------------------------
@dataclass(frozen=True)
class DriftEvent:
    kind: Literal["state_change", "probe_failure"]
    probe_id: str
    target_id: str
    previous: dict[str, Any]
    current: dict[str, Any]
    confirmed: bool = False     # a state_change must be CONFIRMED (a second run) before it may revoke

    @property
    def alerts(self) -> bool:
        return self.kind == "state_change"

    @property
    def evidence_ref(self) -> str:
        return object_hash({"previous": self.previous, "current": self.current})


def classify_drift(previous: dict[str, Any], current: dict[str, Any], *, confirmed: bool = False) -> DriftEvent:
    """A replication that could not be measured now (not_run / telemetry_incomplete / connector error) is a
    probe_failure; a measured value whose gate label changed is a state_change."""
    if current.get("status") != "measured" or current.get("telemetry_incomplete") or current.get("connector_error"):
        kind: Literal["state_change", "probe_failure"] = "probe_failure"
    elif previous.get("label") != current.get("label"):
        kind = "state_change"
    else:
        kind = "probe_failure"  # nothing changed: no alert, no revocation
    return DriftEvent(kind=kind, probe_id=str(current.get("probe_id")), target_id=str(current.get("target_id")), previous=previous, current=current, confirmed=confirmed)


@dataclass
class Tripwire:
    """The only path from a drift event to an attestation's validity. Tested: a probe_failure never revokes; a
    confirmed state_change revokes and records the evidence reference; an unconfirmed one queues review."""
    tickets: list[dict[str, Any]] = field(default_factory=list)
    review_queue: list[dict[str, Any]] = field(default_factory=list)

    def handle(self, event: DriftEvent, attestation: Attestation, at: str) -> str:
        if event.kind == "probe_failure":
            self.tickets.append({"kind": "internal", "event": event.kind, "probe": event.probe_id, "target": event.target_id})
            return "ticket"
        if not event.confirmed:
            self.review_queue.append({"kind": "confirm", "probe": event.probe_id, "target": event.target_id, "evidence_ref": event.evidence_ref})
            return "pending_confirmation"
        attestation.revoke(reason=f"confirmed state change on {event.probe_id}/{event.target_id}", evidence_ref=event.evidence_ref, at=at)
        self.review_queue.append({"kind": "delta_review", "attestation": attestation.id, "evidence_ref": event.evidence_ref})
        return "revoked"


# ---- portal / API ----------------------------------------------------------------------------------------
PORTAL_ENDPOINTS = {
    "GET /runs/{run_id}/bundle": "verifiable bundle download (zip with manifest, results, ledger proof)",
    "GET /runs/{run_id}/verify": "server-side re-verification report (the same as `mark-ledger verify`)",
    "GET /attestations/{id}/status": "conditional validity, signed; see attestation_status()",
    "MCP tool verify_bundle(run_id)": "the verification report as an MCP tool result",
}
