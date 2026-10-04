"""Derive freeze-4's stall baselines and multiple from the practice runs' own records (founder ruling 2026-09-23).

    uv run python packages/platform/scripts/derive_stall_bounds.py runs/<practice run> [runs/<practice run> ...]

Freeze-3's multiple was chosen after its smokes were read: 3, because the worst ratio was 2.02. Freeze-4's is produced by a
rule written into the pre-registration draft BEFORE the practice runs started, and this script is that rule -- so the numbers
produce the value instead of informing a choice:

  * observations: every value of a bounded path each practice run recorded (`stall_measured`, on every replication whatever
    its status): the harness's reaction, and the first-effect latency of each turn received before the halt;
  * baseline, per target x model x path: the median of those observations pooled across the controls; a target that makes
    no model calls gets one baseline for any model;
  * worst ratio: the largest ratio of a single observation to the baseline the check will apply to it;
  * multiple: the smallest whole number strictly greater than the worst ratio, and never less than 2.

It prints the values, the observation counts behind each, and the worst observation by name, as JSON. It writes nothing:
the spec is edited by hand from its output, and the output is kept beside the freeze notes.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mark_platform.stall import ANY_MODEL  # noqa: E402

NO_MODEL_TARGETS = ("scripted",)   # the same default check_stall_baselines applies
FLOOR = 2
PATHS = ("reaction", "tool_path")


def observations(results: dict[str, Any], model: str) -> list[dict[str, Any]]:
    """One record per observed value of a bounded path, from one run's results.json."""
    out = []
    for row in results.get("results") or []:
        target = (row.get("target") or {}).get("id") if isinstance(row.get("target"), dict) else row.get("target")
        control = (row.get("control") or {}).get("id") if isinstance(row.get("control"), dict) else row.get("control")
        key = ANY_MODEL if target in NO_MODEL_TARGETS else model
        for rep in row.get("per_replication") or []:
            m = (rep.get("raw") or {}).get("stall_measured") or {}
            where = {"target": target, "model": key, "control": control, "scenario_id": rep.get("scenario_id")}
            if isinstance(m.get("reaction_ms"), (int, float)):
                out.append({**where, "path": "reaction", "ms": float(m["reaction_ms"])})
            for turn in m.get("tool_path_first_effect_ms_by_turn") or []:
                if turn.get("before_halt") and isinstance(turn.get("ms"), (int, float)):
                    out.append({**where, "path": "tool_path", "ms": float(turn["ms"]), "turn": turn.get("turn")})
    return out


def derive(obs: list[dict[str, Any]]) -> dict[str, Any]:
    if not obs:
        raise ValueError("no observations of a bounded path: nothing to derive the bounds from")
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for o in obs:
        groups.setdefault((o["target"], o["model"], o["path"]), []).append(o)
    baselines: dict[str, dict[str, dict[str, float]]] = {}
    counts: dict[str, dict[str, dict[str, int]]] = {}
    worst: dict[str, Any] | None = None
    for (target, model, path), g in sorted(groups.items()):
        median = statistics.median(o["ms"] for o in g)
        baselines.setdefault(target, {}).setdefault(model, {})[f"{path}_ms"] = round(median, 1)
        counts.setdefault(target, {}).setdefault(model, {})[path] = len(g)
        for o in g:
            ratio = o["ms"] / median if median > 0 else math.inf
            if worst is None or ratio > worst["ratio"]:
                worst = {**o, "baseline_ms": median, "ratio": ratio}
    assert worst is not None
    multiple = max(FLOOR, math.floor(worst["ratio"]) + 1)
    return {"rule": "multiple = the smallest whole number strictly greater than the worst ratio of an observation to its baseline, never below 2; "
                    "baseline = the median of the observations of that path, pooled across controls, per target x model",
            "multiple": multiple, "worst": worst, "baseline_ms_by_target_model": baselines, "observations_by_target_model": counts}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    obs: list[dict[str, Any]] = []
    for d in map(Path, argv):
        results = json.loads((d / "results.json").read_text(encoding="utf-8"))
        manifest = json.loads((d / "manifest.unsigned.json").read_text(encoding="utf-8"))
        obs += observations(results, manifest["pins"]["model"]["id"])
    print(json.dumps({"runs": [Path(a).name for a in argv], **derive(obs)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
