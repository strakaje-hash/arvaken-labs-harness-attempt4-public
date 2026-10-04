"""The census of hard-coded version pins (attempt 4, after the C3 scope bump).

Founder ruling 2026-09-21: *"a pattern that finds one access path and not another is a hand-kept list of access paths...
a version pin in a test should read the version from the workload it's pinning, or be the one place a hard number is
allowed and be found by a check that walks every `["version"] ==` in the tree and compares it to the loader's answer.
Either way, a bump then fails every stale pin at authoring time, with the name, and no grep is the census."*

A pin is a **tripwire**, not an assertion: it says *this test was written against v12 of this workload; if the workload
moves, come read this test again*. It cannot be made to read the loader, because a test whose expected value comes from
the thing under test cannot fail. So the hard number stays and this module is the census:

- `pin_sites` walks a file's AST for the comparison **shape** `<anything>["version"] == <int>`. The shape is what is
  matched, so a pin reached through a local name (`wl["version"] == 11` -- the one the C3 grep missed, because its
  pattern assumed a subscript chain) is found exactly like any other.
- `unregistered` and `miscounted` require every site found in the tree to be registered in `PINS`, so a new hard pin
  nobody registered fails.
- `stale` compares each registered pin against the artifact's current version from its own loader and names the file,
  the line, both numbers and why that test cares.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
SKIP = ("/.venv/", "/node_modules/", "/__pycache__/", "/.git/", "/benchmarks/runs/")

# file (repo-relative) -> one entry per pinned SITE, in line order: (kind, artifact id, why this test cares).
# kind: workload | gate | gate_draft | mapping -- a PIN, compared to that artifact's current version;
#       self  -- an artifact's own version number, nothing to compare it against;
#       bound -- an inequality (a validity floor in a validator, a deliberately loose assertion), never a tripwire.
# The walk is deliberately broader than "pins": it finds every comparison of a `["version"]` to a hard number, and the
# registry is where each is classified, so nothing is invisible and the judgment is readable.
PINS: dict[str, list[tuple[str, str, str]]] = {
    "packages/platform/tests/test_continuation_cap_and_bounds.py": [
        ("workload", "wl.sequence-payments-single", "asserts the observation-window bound rule's text, which v11 corrected"),
    ],
    "packages/platform/tests/test_scope_corrections.py": [
        ("workload", "wl.sequence-payments-single", "asserts the hashed scope line is UNCHANGED beside the second dated reading: the pin is the point, since adding the reading to the workload would have moved its hash without moving its version"),
    ],
    "packages/platform/tests/test_next_step.py": [
        ("workload", "wl.sequence-payments-single", "asserts the next-step feedback rules v11 declared"),
    ],
    "packages/platform/tests/test_markcall.py": [
        ("workload", "wl.sequence-payments-single", "asserts the single-call arm's task text and feedback params"),
        ("workload", "wl.spawn-children", "asserts the OpenHands spawn task fix B3 added"),
    ],
    "packages/platform/tests/test_scope_side_channel_pipeline.py": [
        ("workload", "wl.sequence-payments", "the row's workload version reaches the bundle"),
        ("workload", "wl.sequence-payments-sidechannel", "the positive control's version reaches the bundle"),
    ],
    "packages/platform/tests/test_self_report.py": [
        ("gate", "ks.completeness", "the signed gate version whose precondition the row must carry"),
    ],
    "packages/platform/tests/test_variants.py": [
        ("gate", "ks.completeness", "the signed gate version whose precondition the row must carry"),
    ],
    "packages/probes/tests/test_gate_v2_enforcement.py": [
        ("gate", "ks.completeness", "the signed gate's version, thresholds and issue time"),
    ],
    "packages/probes/tests/test_scope_side_channel.py": [
        ("gate_draft", "scope.side_channel", "the draft the founder signed"),
    ],
    "packages/platform/tests/test_replay_fidelity.py": [
        ("self", "CLASSIFICATION_RULES", "the classification rules' own version, not an instrument artifact's"),
    ],
    "packages/product/tests/test_aws_manifest.py": [
        ("self", "aws manifest", "the product's own manifest version, not an instrument artifact's"),
    ],
    # inequalities: validity floors and deliberately loose assertions. Registered so the walk stays broad and nothing hides.
    "packages/platform/mark_platform/permitted_calls.py": [
        ("bound", "permitted-calls declaration", "the loader's validity floor: a declaration has an integer version >= 1"),
    ],
    "packages/probes/mark_probes/tag_mapping.py": [
        ("bound", "tag mapping", "the loader's validity floor: a mapping has an integer version >= 1"),
    ],
    "packages/platform/tests/test_pipeline.py": [
        ("bound", "tag-outcomes", "asserts the bundle pins SOME mapping version, deliberately not which one"),
    ],
}


def pin_sites(path: str | Path) -> list[tuple[int, int, list[int]]]:
    """Every `<anything>["version"] <op> <int>` comparison in the file as (line, column, the integers compared IN THAT
    comparison). One entry per comparison, not per line: two pins on one line are two sites, and each carries its own
    number, so a stale one cannot be excused by its neighbour's."""
    p = Path(path)
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    sites: list[tuple[int, int, list[int]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        subscripts_version = any(isinstance(s, ast.Subscript) and isinstance(s.slice, ast.Constant) and s.slice.value == "version" for s in sides)
        ints = [s.value for s in sides if isinstance(s, ast.Constant) and isinstance(s.value, int) and not isinstance(s.value, bool)]
        if subscripts_version and ints:
            sites.append((node.lineno, node.col_offset, ints))
    return sorted(sites)


def python_files(root: str | Path = REPO) -> list[Path]:
    root = Path(root)
    return sorted(p for p in root.rglob("*.py") if not any(s in f"/{p.relative_to(root).as_posix()}" for s in SKIP))


def census(root: str | Path = REPO) -> dict[str, list[tuple[int, int, list[int]]]]:
    """Every file in the tree holding at least one version comparison against a hard number, with its sites."""
    root = Path(root)
    found = {}
    for p in python_files(root):
        sites = pin_sites(p)
        if sites:
            found[p.relative_to(root).as_posix()] = sites
    return found


def unregistered(found: dict[str, Any], pins: dict[str, list[tuple[str, str, str]]] = PINS) -> dict[str, Any]:
    return {f: sites for f, sites in found.items() if f not in pins}


def miscounted(found: dict[str, Any], pins: dict[str, list[tuple[str, str, str]]] = PINS) -> dict[str, tuple[int, int]]:
    return {f: (len(sites), len(pins[f])) for f, sites in found.items() if f in pins and len(sites) != len(pins[f])}


def orphaned(found: dict[str, Any], pins: dict[str, list[tuple[str, str, str]]] = PINS) -> list[str]:
    return sorted(f for f in pins if f not in found)


def current_version(kind: str, artifact_id: str, root: str | Path = REPO) -> int | None:
    """The artifact's version now, from its own loader. None for `self`: nothing to compare against."""
    root = Path(root)
    if kind == "workload":
        from .workloads import load

        return int(load()[artifact_id]["version"])
    if kind == "gate":
        from mark_probes.gate import load_gate

        root_pub = root / "packages" / "bundles" / "keys" / "root.pub"
        return int(load_gate(root / "gates", artifact_id, root_pub.read_text().strip() if root_pub.exists() else None).version)
    if kind == "gate_draft":
        return int(json.loads((root / "gates" / f"{artifact_id}.draft.json").read_text(encoding="utf-8"))["version"])
    if kind == "mapping":
        return int(json.loads((root / "mappings" / f"{artifact_id}.draft.json").read_text(encoding="utf-8"))["version"])
    return None   # self and bound: nothing to compare against


def stale(pins: dict[str, list[tuple[str, str, str]]] = PINS, root: str | Path = REPO) -> list[str]:
    """Registered pins whose number is not the artifact's current version, each named with file, line, both numbers and why.
    The number compared is the one in THAT comparison, taken from the AST -- never a regex over the line, which would let a
    stale pin be excused by a current number sitting beside it."""
    root = Path(root)
    out: list[str] = []
    for file, entries in pins.items():
        sites = pin_sites(root / file)
        for (kind, artifact_id, why), (line, _col, ints) in zip(entries, sites):
            now = current_version(kind, artifact_id, root)
            if now is None:
                continue
            if now not in ints:
                out.append(f"{file}:{line} pins {kind} {artifact_id} at {ints} but it is now v{now} -- {why}")
    return out
