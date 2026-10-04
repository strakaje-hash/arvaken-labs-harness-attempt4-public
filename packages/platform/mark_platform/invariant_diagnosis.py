"""A baseline invariant that fired: diagnosed before signing, or the bundle is informational (founder rulings 2026-09-12
and 2026-09-14, gates/clarifications/baseline_invariant_firing.v1 and .v2).

The pre-registered criterion "no baseline invariant fired" was written when a firing meant the instrument had found
itself broken. On decisive attempt 2 the ks.resume invariant fired because the INVARIANT was not scoped to the
variant; the probe it fired on was fully excluded, and nothing in the other probes' integrity depended on it. The
signed clarification v1 reads the criterion per probe, and closes the door behind it:

* a firing diagnosed as an invariant defect, with its probe fully excluded, leaves the other probes decisive;
* a firing that is unexplained at signing makes the bundle informational.

v2 (founder ruling 2026-09-14) follows fix A3: from commit 92832a6 the runner invalidates a fired invariant's probe on
the target and workload variant where it fired, and records `baseline_invariants.scope = target_variant`. v2 reads the
criterion per target and workload variant: a diagnosed firing with that cell fully excluded leaves the rest of the
bundle decisive. It applies only to bundles that record that scope, and only when the v2 clarification verifies as
signed; a bundle scoped per probe (attempts 2a and 2b) is read under v1 whatever v2's signature says, so v2 never
reopens a row measured before it. Unsigned v2 leaves a target-variant firing informational: v1 does not describe that
scope, and nothing is read in the direction that adds verdicts until the founder has signed it.

`record_invariant_diagnosis` writes the diagnosis into the ledger before signing (it refuses a signed bundle, a probe or
cell whose invariant did not fire, a probe or cell with any measured or decisive row left, and a second diagnosis).
`publication_reading` is computed by `platform run sign` and signed inside the manifest, so the reading is part of what
the signature covers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mark_ledger.canonical import object_hash, sha256_hex
from mark_ledger.store import Ledger, Provenance

CLARIFICATION_ID = "baseline_invariant_firing.v1"
CLARIFICATION_V2_ID = "baseline_invariant_firing.v2"
CLARIFICATION_V2_PATH = "clarifications/baseline_invariant_firing.v2"   # under gates/, as sign-gate names it
TARGET_VARIANT_SCOPE = "target_variant"
INVARIANT_DEFECT = "invariant_defect"


class DiagnosisRefused(Exception):
    pass


def clarification_signed(gates_dir: str | Path, clarification_path: str, root_public_hex: str | None, revocations: dict[str, Any] | None = None, *,
                         clarification_id: str | None = None) -> bool:
    """True only when <gates_dir>/<clarification_path>.signed.json verifies under the root for purpose 'gate' (how
    sign-gate signs a clarification) and, when given, names the expected clarification id. A draft is never signed."""
    from mark_ledger.keys import KeyCertError, SignedObject, verify_signed_object
    from mark_probes.gate import GATE_KEY_PURPOSE

    p = Path(gates_dir) / f"{clarification_path}.signed.json"
    if not root_public_hex or not p.exists():
        return False
    try:
        body = verify_signed_object(SignedObject.from_json(json.loads(p.read_text(encoding="utf-8"))), root_public_hex, GATE_KEY_PURPOSE, revocations=revocations)
    except (KeyCertError, KeyError, ValueError, TypeError):
        return False
    return clarification_id is None or body.get("clarification_id") == clarification_id


def _violations(results: dict[str, Any]) -> list[dict[str, Any]]:
    return (results.get("baseline_invariants") or {}).get("violations", [])


def _scope(results: dict[str, Any]) -> str | None:
    return (results.get("baseline_invariants") or {}).get("scope")


def fired_probes(results: dict[str, Any]) -> list[str]:
    return sorted({v["probe"] for v in _violations(results)})


def fired_cells(results: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(probe, target, variant) of every firing, as a runner scoped per target and variant records them."""
    return sorted({(v["probe"], str(v.get("target")), str(v.get("variant"))) for v in _violations(results)})


def _excluded(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(int((r.get("replications") or {}).get("measured") or 0) == 0 and not r["verdict"].get("decisive") for r in rows)


def probe_fully_excluded(results: dict[str, Any], probe_id: str) -> bool:
    return _excluded([r for r in results.get("results", []) if r["probe"]["id"] == probe_id])


def cell_fully_excluded(results: dict[str, Any], probe_id: str, target: str, variant: str) -> bool:
    return _excluded([r for r in results.get("results", []) if r["probe"]["id"] == probe_id and r["target"]["id"] == target
                      and ((r.get("context") or {}).get("variant") or "unspecified") == variant])


def _cell_name(cell: tuple[str, str, str]) -> str:
    return f"{cell[0]} on {cell[1]} ({cell[2]})"


def publication_reading(results: dict[str, Any], manifest: dict[str, Any], *, v2_signed: bool = False) -> dict[str, Any]:
    fired = fired_probes(results)
    if not fired:
        return {"reading": "per_probe", "why": "no baseline invariant fired"}
    defects = [d for d in (manifest.get("environment") or {}).get("invariant_diagnoses", []) if d.get("classification") == INVARIANT_DEFECT]
    if _scope(results) != TARGET_VARIANT_SCOPE:
        # v1: a bundle scoped per probe (attempts 2a and 2b), whatever v2's signature says
        diagnosed = {d["probe"] for d in defects}
        unexplained = [p for p in fired if p not in diagnosed or not probe_fully_excluded(results, p)]
        if unexplained:
            return {"reading": "informational", "clarification": CLARIFICATION_ID,
                    "why": f"a baseline invariant fired on {unexplained} and is unexplained at signing (no invariant-defect diagnosis with the probe fully excluded)"}
        return {"reading": "per_probe", "clarification": CLARIFICATION_ID, "not_measured_in_this_bundle": fired,
                "why": f"every baseline invariant firing ({fired}) was diagnosed before signing as an invariant defect, with its probe fully excluded; the other probes stay decisive"}
    cells = fired_cells(results)
    names = [_cell_name(c) for c in cells]
    if not v2_signed:
        return {"reading": "informational", "clarification": CLARIFICATION_V2_ID,
                "why": f"a baseline invariant fired on {names} in a bundle scoped per target and workload variant; reading it per cell needs the signed "
                       f"clarification {CLARIFICATION_V2_ID}, which does not verify, and {CLARIFICATION_ID} does not describe that scope"}
    diagnosed_cells = {(d["probe"], str(d.get("target")), str(d.get("variant"))) for d in defects}
    unexplained_cells = [_cell_name(c) for c in cells if c not in diagnosed_cells or not cell_fully_excluded(results, *c)]
    if unexplained_cells:
        return {"reading": "informational", "clarification": CLARIFICATION_V2_ID,
                "why": f"a baseline invariant fired on {unexplained_cells} and is unexplained at signing (no invariant-defect diagnosis with that target and variant fully excluded)"}
    return {"reading": "per_target_variant", "clarification": CLARIFICATION_V2_ID, "not_measured_in_this_bundle": names,
            "why": f"every baseline invariant firing ({names}) was diagnosed before signing as an invariant defect, with its probe on that target and workload variant "
                   "fully excluded; the rest of the bundle stays decisive"}


def record_invariant_diagnosis(run_dir: str | Path, *, probe_id: str, classification: str, diagnosis: str, evidence: str, fix_commit: str, engine_version: str, repo_commit: str,
                               target: str | None = None, variant: str | None = None, v2_signed: bool = False) -> dict[str, Any]:
    run_dir = Path(run_dir)
    rp, mp = run_dir / "results.json", run_dir / "manifest.unsigned.json"
    if (run_dir / "manifest.json").exists():
        raise DiagnosisRefused("the bundle is already signed: a diagnosis is recorded before signing, never after")
    if classification != INVARIANT_DEFECT:
        raise DiagnosisRefused(f"only an {INVARIANT_DEFECT!r} diagnosis leaves the other probes decisive; anything else leaves the bundle informational and needs no record")
    results = json.loads(rp.read_text(encoding="utf-8"))
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    if probe_id not in fired_probes(results):
        raise DiagnosisRefused(f"no baseline invariant fired on {probe_id} in this run")
    existing = (manifest.get("environment") or {}).get("invariant_diagnoses", [])
    per_cell = _scope(results) == TARGET_VARIANT_SCOPE
    if per_cell:
        if not target or not variant:
            raise DiagnosisRefused("this bundle is scoped per target and workload variant: a diagnosis names the cell (target and variant)")
        cell = (probe_id, target, variant)
        if cell not in fired_cells(results):
            raise DiagnosisRefused(f"no baseline invariant fired on {_cell_name(cell)} in this run")
        if not cell_fully_excluded(results, *cell):
            raise DiagnosisRefused(f"{_cell_name(cell)} is not fully excluded: a row still has measured replications or a decisive verdict")
        if any((d.get("probe"), d.get("target"), d.get("variant")) == cell for d in existing):
            raise DiagnosisRefused(f"{_cell_name(cell)} already carries a diagnosis in this bundle")
        violation = next(x for x in _violations(results) if (x["probe"], str(x.get("target")), str(x.get("variant"))) == cell)
        clarification = CLARIFICATION_V2_ID
    else:
        if not probe_fully_excluded(results, probe_id):
            raise DiagnosisRefused(f"{probe_id} is not fully excluded: a row still has measured replications or a decisive verdict")
        if any(d.get("probe") == probe_id for d in existing):
            raise DiagnosisRefused(f"{probe_id} already carries a diagnosis in this bundle")
        violation = next(x for x in _violations(results) if x["probe"] == probe_id)
        clarification = CLARIFICATION_ID
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(run_dir / "ledger")
    v = led.verify(chain)
    if not v.ok or v.chain_root != manifest["evidence"]["chain_root"]:
        raise DiagnosisRefused("the ledger does not verify or its root differs from the unsigned manifest")
    if sha256_hex(rp.read_bytes()) != manifest["evidence"]["results_sha256"]:
        raise DiagnosisRefused("results.json hash differs from the unsigned manifest")
    payload = {"schema": "mark.invariant-diagnosis/1", "probe": probe_id, "invariant": violation.get("invariant"), "cell": violation.get("cell"),
               "classification": classification, "diagnosis": diagnosis, "evidence": evidence, "fix_commit": fix_commit, "clarification": clarification,
               "recorded": "before signing", "engine_version": engine_version, "repo_commit": repo_commit}
    if per_cell:
        payload.update(target=target, variant=variant)
    rec = led.append(chain, "invariant_diagnosis", payload, Provenance(engine_version, object_hash(results.get("environment") or {}), "platform-diagnose"))
    from .anchoring import local_anchor

    local_anchor(led, chain)
    entry = {"probe": probe_id, "classification": classification, "fix_commit": fix_commit, "clarification": clarification, "ledger_record": rec.hash}
    if per_cell:
        entry.update(target=target, variant=variant)
    manifest.setdefault("environment", {})["invariant_diagnoses"] = [*existing, entry]
    manifest["evidence"]["chain_root"] = led.chain_root(chain)
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {"ok": True, **entry, "chain_root_after": manifest["evidence"]["chain_root"], "reading_now": publication_reading(results, manifest, v2_signed=v2_signed)}
