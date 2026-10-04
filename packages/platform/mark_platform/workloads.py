from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKLOADS_SCHEMA = "mark.workloads/1"
# What varies between a workload's replications (founder ruling 2026-09-12): declared, never implicit. On a model-driven
# target the per-replication scenario id reaches the prompt and drives the model down different token paths.
VARIATION_SOURCES = ("scenario_id",)
# the scope vocabulary (C3): mail:<address|@domain|pattern> payment:<reference|pattern> db:<key|pattern> api:<path|pattern>
SCOPE_SERVICES = ("mail", "payment", "db", "api")


def default_path() -> Path:
    return Path(__file__).resolve().parents[3] / "benchmarks" / "workloads.yaml"


def load(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(Path(path or default_path()).read_text(encoding="utf-8"))
    if doc.get("schema") != WORKLOADS_SCHEMA:
        raise ValueError(f"workloads schema must be {WORKLOADS_SCHEMA}")
    out = {}
    for w in doc["workloads"]:
        for k in ("id", "version", "description", "task", "script", "variation"):
            if k not in w:
                raise ValueError(f"workload {w.get('id')} missing {k}")
        if w["variation"] not in VARIATION_SOURCES:
            raise ValueError(f"workload {w.get('id')}: variation {w['variation']!r} is not a declared source {VARIATION_SOURCES}")
        # attempt 4, C3 (scope.side_channel): every workload declares the destinations its effects may reach. An empty block is a
        # declaration (nothing may be reached); a missing one is refused, because a workload without a scope cannot be measured
        # for a side channel and would silently read as never having one.
        if "scope" not in w or not isinstance(w["scope"], dict):
            raise ValueError(f"workload {w.get('id')}: missing scope block (a dict over {SCOPE_SERVICES}; empty when the workload reaches no scoped service)")
        for k, v in w["scope"].items():
            if k not in SCOPE_SERVICES or not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v):
                raise ValueError(f"workload {w.get('id')}: scope.{k} must be a list of non-empty strings over {SCOPE_SERVICES}, got {v!r}")
        w.setdefault("params", {})
        out[w["id"]] = w
    return out
