"""Scope corrections (fix C3, attempt 3 fixes v1.1, 2026-09-14).

A scope line in a workload is spec-hashed into every row measured under it, so a later reading never edits it: the bundle
keeps the line it was measured with. When a later reading narrows a line, the correction is recorded in
benchmarks/scope-corrections.yaml, and the report prints it beside the hashed line in every bundle that carries that line
(same target, workload, one of the named workload versions, same status and model).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SCHEMA = "mark.scope-corrections/1"
_FIELDS = ("target", "workload", "workload_versions", "corrects_status", "declared_model", "fix", "date", "text", "source")


def default_path() -> Path:
    return Path(__file__).resolve().parents[3] / "benchmarks" / "scope-corrections.yaml"


def load_scope_corrections(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else default_path()
    if not p.exists():
        return []
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{p}: schema must be {SCHEMA}")
    out = []
    for n, c in enumerate(doc.get("corrections") or [], start=1):
        missing = [k for k in _FIELDS if not c.get(k)]
        if missing:
            raise ValueError(f"{p}: correction {n} missing {missing}")
        if not isinstance(c["workload_versions"], list) or not all(isinstance(v, int) for v in c["workload_versions"]):
            raise ValueError(f"{p}: correction {n}: workload_versions must be a list of integers")
        out.append({**c, "text": " ".join(str(c["text"]).split()), "date": str(c["date"])})
    return out


def scope_corrections_for(corrections: list[dict[str, Any]], target: str, workload: str, version: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in corrections if c["target"] == target and c["workload"] == workload and version in c["workload_versions"]
            and c["corrects_status"] == scope.get("status") and c["declared_model"] == scope.get("declared_model")]
