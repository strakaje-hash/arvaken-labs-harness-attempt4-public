"""The close steps a bundle must pass before it is signed, enforced by the tool (founder ruling 2026-09-16).

On attempt 3 all six bundles were signed although neither pre-signing step had run: the offline re-decision
(`redecide`) and the pass-sample review of every decisive pass, read as raw timelines against the probe's question and
against `none`. Both lived only in the plan, and a check that lives in a plan gets skipped on a long night. So the order
is kept by the tool, the same way `redecide` refuses a signed bundle:

* `platform run sign` refuses a bundle whose ledger has no `close_redecision` record;
* it refuses a bundle whose ledger has no `pass_sample_review` record for the results.json it is about to sign (a review
  of results that were changed after it was recorded does not count);
* `record_pass_sample_review` refuses a signed bundle, a bundle not yet re-decided, a review of other bytes than the
  current results.json, and a review that leaves any decisive pass unread or unresolved.

A pass the review finds unearned is recorded as such and listed inside the manifest, so the signature covers it; the
paper excludes that cell and says why. The verdict in results.json is not rewritten: the review is a reading, not a
re-decision."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mark_ledger.canonical import object_hash, sha256_hex
from mark_ledger.store import Ledger, Provenance

REDECIDE_KIND = "close_redecision"
PASS_SAMPLE_KIND = "pass_sample_review"
PASS_SAMPLE_SCHEMA = "mark.pass-sample-review/1"
READINGS = ("earned", "unearned")


class CloseStepRefused(Exception):
    pass


def cell_id(row: dict[str, Any]) -> str:
    return f"{row['probe']['id']}/{row['target']['id']}/{row['control']['id']}/{row['workload']['id']}"


def decisive_passes(results: dict[str, Any]) -> list[str]:
    """Every row the gates decided as a pass: what the pass-sample rule says must be read."""
    return sorted(cell_id(r) for r in results["results"] if (r.get("verdict") or {}).get("decisive") and r["verdict"].get("label") == "pass")


def _payloads(led: Ledger, chain: str, kind: str) -> list[dict[str, Any]]:
    return [json.loads(led.get_object(rec.content_hash)) for rec in led.records(chain) if rec.kind == kind]


def missing_close_steps(run_dir: str | Path) -> list[str]:
    """What stands between this bundle and a signature; empty when both close steps are in its ledger."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(run_dir / "ledger")
    results_sha = sha256_hex((run_dir / "results.json").read_bytes())
    missing = []
    if not _payloads(led, chain, REDECIDE_KIND):
        missing.append("no close_redecision record in the ledger: run `platform run redecide` first")
    reviews = _payloads(led, chain, PASS_SAMPLE_KIND)
    if not any(p.get("results_sha256") == results_sha for p in reviews):
        missing.append("no pass_sample_review record for this results.json in the ledger: read every decisive pass and record it with "
                       "`platform run pass-sample-record`" + (" (a review exists, but of other results bytes)" if reviews else ""))
    return missing


def record_pass_sample_review(run_dir: str | Path, review: dict[str, Any], *, engine_version: str, repo_commit: str) -> dict[str, Any]:
    """Write the pass-sample review into the ledger before signing. `review` is
    {"reviewer": str, "method": str, "cells": {cell_id: {"reading": "earned" | "unearned", "basis": str, "replications_read": [...]}}}."""
    run_dir = Path(run_dir)
    rp, mp = run_dir / "results.json", run_dir / "manifest.unsigned.json"
    if (run_dir / "manifest.json").exists():
        raise CloseStepRefused("the bundle is already signed: the pass-sample review is recorded before signing; after it, write a correction beside the bundle")
    results = json.loads(rp.read_text(encoding="utf-8"))
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(run_dir / "ledger")
    v = led.verify(chain)
    if not v.ok or v.chain_root != manifest["evidence"]["chain_root"]:
        raise CloseStepRefused("the ledger does not verify or its root differs from the unsigned manifest")
    results_sha = sha256_hex(rp.read_bytes())
    if results_sha != manifest["evidence"]["results_sha256"]:
        raise CloseStepRefused("results.json hash differs from the unsigned manifest")
    if not _payloads(led, chain, REDECIDE_KIND):
        raise CloseStepRefused("the bundle has not been re-decided: the review reads the verdicts `redecide` leaves, so it runs after it")
    if not str(review.get("reviewer") or "").strip() or not str(review.get("method") or "").strip():
        raise CloseStepRefused("a review names its reviewer and its method")
    cells = review.get("cells") or {}
    passes = decisive_passes(results)
    unread = [c for c in passes if c not in cells]
    if unread:
        raise CloseStepRefused(f"decisive passes not read: {unread}")
    extra = sorted(c for c in cells if c not in passes)
    if extra:
        raise CloseStepRefused(f"cells that are not decisive passes in this results.json: {extra}")
    for c in passes:
        entry = cells[c]
        if entry.get("reading") not in READINGS:
            raise CloseStepRefused(f"{c}: reading must be one of {READINGS} (a questionable pass is resolved before it is recorded), not {entry.get('reading')!r}")
        if not str(entry.get("basis") or "").strip() or not entry.get("replications_read"):
            raise CloseStepRefused(f"{c}: a reading states its basis and the replications it read")
    unearned = [c for c in passes if cells[c]["reading"] == "unearned"]
    payload = {"schema": PASS_SAMPLE_SCHEMA, "results_sha256": results_sha, "reviewer": review["reviewer"], "method": review["method"],
               "decisive_passes": len(passes), "unearned": unearned, "cells": {c: cells[c] for c in passes},
               "recorded": "before signing", "engine_version": engine_version, "repo_commit": repo_commit}
    rec = led.append(chain, PASS_SAMPLE_KIND, payload, Provenance(engine_version, object_hash(results.get("environment") or {}), "platform-pass-sample"))
    from .anchoring import local_anchor

    local_anchor(led, chain)
    manifest.setdefault("environment", {})["pass_sample_review"] = {"results_sha256": results_sha, "decisive_passes": len(passes), "unearned": unearned,
                                                                     "ledger_record": rec.hash}
    manifest["evidence"]["chain_root"] = led.chain_root(chain)
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {"ok": True, "decisive_passes": len(passes), "unearned": unearned, "ledger_record": rec.hash, "chain_root_after": manifest["evidence"]["chain_root"]}
