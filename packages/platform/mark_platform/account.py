"""The run-level account, in the signed object (attempt 4, A5).

Attempt 3's manifests carried no totals. Its audit derived scheduled-against-recorded from results.json -- which the manifest
covers by hash, so the derivation was sound -- and its record said so on every bundle ("the manifest does not carry these
totals at freeze-8"). From attempt 4 the account is written into the manifest at close, so the signature covers the run's
own statement of what it scheduled, what it recorded, and why anything was not run; and `run sign` recomputes it from the
cell records and refuses a manifest whose statement differs. A manifest can then never claim a count its own results do not
add up to.

The computation is the audit's rule 0, kept identical so the two agree on every attempt 3 bundle: scheduled is the sum of each
cell's requested replications, recorded the sum of its per-replication records, and every not_run reason is counted under
its own text, truncated the way the audit truncated it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ACCOUNT_SCHEMA = "mark.run-account/1"
RULE = ("scheduled = sum over cells of replications.requested; recorded = sum over cells of per_replication records; measured, "
        "measured_extra and not_run count records by status; not_run_by_reason counts each not_run reason under its own text "
        "(whitespace collapsed, truncated at 120 characters); unaccounted = scheduled - recorded. Written into the signed "
        "manifest at close and recomputed from the cell records before signing; a difference refuses the signature.")
COMPARED = ("cells", "scheduled_replications", "recorded_replications", "unaccounted_replications", "measured", "measured_extra", "not_run", "not_run_by_reason",
            "scenario_directories", "scenario_directories_expected")


def reason_key(reason: str) -> str:
    """not_run reasons are bucketed by their own text, truncated: the runner's reasons carry their detail inline. The same
    truncation as the attempt 3 audit's, so both count an attempt 3 bundle alike."""
    text = " ".join(str(reason or "").split())
    return (text[:117] + "...") if len(text) > 120 else text


def run_level_account(results: dict[str, Any], *, run_dir: str | Path | None = None) -> dict[str, Any]:
    rows = results.get("results") or []
    scheduled = recorded = measured = extra = not_run = with_dir = 0
    by_reason: dict[str, int] = {}
    for r in rows:
        reps = r.get("per_replication") or []
        scheduled += int((r.get("replications") or {}).get("requested") or len(reps))
        recorded += len(reps)
        for rep in reps:
            status = rep.get("status")
            if rep.get("scenario_id"):
                with_dir += 1
            if status == "measured":
                measured += 1
            elif status == "measured_extra":
                extra += 1
            else:
                not_run += 1
                key = reason_key(rep.get("reason"))
                by_reason[key] = by_reason.get(key, 0) + 1
    # A8: the expected directory count includes the calibration scenarios, which are scenario directories like any other.
    # Attempt 3's audit counted measured + extra + not_run-with-a-scenario, read a three-directory surplus as a gap, and
    # had to be corrected beside the bundle; every replication with a scenario id has a directory, and so does calibration.
    calibration = len(((results.get("calibration") or {}).get("replications")) or [])
    dirs = None
    if run_dir is not None:
        sdir = Path(run_dir) / "scenarios"
        dirs = len([p for p in sdir.iterdir() if p.is_dir()]) if sdir.is_dir() else 0
    return {"schema": ACCOUNT_SCHEMA, "rule": RULE, "cells": len(rows), "scheduled_replications": scheduled, "recorded_replications": recorded,
            "unaccounted_replications": scheduled - recorded, "measured": measured, "measured_extra": extra, "not_run": not_run,
            "not_run_by_reason": dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
            "scenario_directories": dirs, "scenario_directories_expected": with_dir + calibration, "calibration_scenarios": calibration,
            "scenario_directories_rule": "every replication recorded with a scenario id has a directory, and so does every calibration scenario (A8)"}


def account_differences(claimed: dict[str, Any] | None, recomputed: dict[str, Any]) -> list[str]:
    """Field by field, the manifest's statement against the cell records' sums. Empty means they agree."""
    if not claimed:
        return ["the manifest carries no run-level account (a bundle closed before A5, or a manifest edited after close)"]
    out = []
    for k in COMPARED:
        if claimed.get(k) != recomputed.get(k):
            out.append(f"{k}: the manifest says {json.dumps(claimed.get(k), sort_keys=True)}, the cell records give {json.dumps(recomputed.get(k), sort_keys=True)}")
    return out


def check_manifest_account(run_dir: str | Path) -> list[str]:
    """What stands between this bundle's manifest and a signature on the account alone: the refusals, or nothing."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    return account_differences(manifest.get("account"), run_level_account(results, run_dir=run_dir))
