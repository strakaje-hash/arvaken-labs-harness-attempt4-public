"""The guard pass: the checks that need no agent, no model and no scenario, run before every commit.

    python scripts/guard_pass.py              # run them
    python scripts/guard_pass.py --install-hook   # make it a pre-commit hook

**Why.** On 2026-09-22 six reruns of a 40-minute suite were caused by checks that take seconds: assertion shapes,
the version-pin census, records-tracked, the constitution render, the scope-corrections unpack, the stall spec's
key names, the pre-registration matching the tree. Each sat inside the slow suite, so a two-minute failure waited
forty minutes to be found. This runs them first.

**It does not replace the full suite.** The full run still gates the tag. This gates the commit, which is where
those failures were caught by hand three times in one evening; the hook makes the fourth time free.

**The design risk, and how it is handled.** A hand-kept list goes stale, and a guard pass that silently stops
covering a check is worse than none, because it buys false confidence. So `test_guard_pass.py` asserts the list is
non-empty, that every file exists, and that the count does not fall below a floor -- drift can only be toward
running too little, and that direction fails loudly. And `BUDGET_S` makes "fast" a fact rather than a claim: if
the pass grows slow enough to be skipped in practice, it fails and says so, because a guard nobody runs is not a
guard.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUDGET_S = 240.0
FLOOR = 8

# Each entry says what it catches, so a reader can tell whether the list still covers what it claims to.
GUARDS: list[tuple[str, str]] = [
    ("packages/platform/tests/test_assertion_shapes.py",
     "R20: an assertion that can be skipped (all inside a loop, or behind a condition that may not hold)"),
    ("packages/platform/tests/test_version_pin_census.py",
     "R19: a hard-coded version pin left stale by a bump, and every pin registered with its reason"),
    ("packages/platform/tests/test_preregistration_matches_the_tree.py",
     "a signed pre-registration naming a version the tree has moved past"),
    ("packages/platform/tests/test_sign_preregistration.py",
     "the signer: reproducible bytes, and the spec really carrying the record's path and digest"),
    ("packages/platform/tests/test_scope_corrections.py",
     "a hashed scope line edited instead of corrected beside, and the corrections file's entries"),
    ("packages/platform/tests/test_stall_check.py",
     "the stall spec's values AND the key names the checker actually reads (a baseline it cannot see is none)"),
    ("packages/probes/tests/test_records_tracked.py",
     "a record the constitution names that is missing, untracked, or under an ignore rule"),
    ("packages/probes/tests/test_constitution.py",
     "the constitution document drifting from its one source"),
    ("packages/platform/tests/test_env_tests_run_where_agents_run.py",
     "an environment test running as the agents' user somewhere no agent works (tmp_path, /tmp): found on the laptop, not the pods"),
    ("packages/platform/tests/test_guard_pass.py",
     "this list drifting: the floor, the mandatory files, the budget, and the hook refusing rather than warning"),
]

# The hook must not assume `python` is the repository's interpreter. On the founder's machine a bare `python` is
# the system one, with no pytest, so the first commit through the hook was refused for a defect in the hook rather
# than a defect in the tree (2026-09-22). `_python()` below finds the venv; the hook defers to it.
HOOK = """#!/bin/sh
# Installed by scripts/guard_pass.py. A commit on a red guard pass is refused.
for py in .venv/Scripts/python.exe .venv/bin/python; do
  if [ -x "$py" ]; then exec "$py" scripts/guard_pass.py; fi
done
exec python3 scripts/guard_pass.py
"""


def _python() -> str:
    """The repository's interpreter, not whatever `python` happens to mean in this shell."""
    for rel in ("Scripts/python.exe", "bin/python"):
        cand = REPO / ".venv" / rel
        if cand.exists():
            return str(cand)
    return sys.executable


def install_hook() -> int:
    hooks = REPO / ".githooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text(HOOK, encoding="utf-8", newline="\n")
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=REPO, check=True)
    print("installed: .githooks/pre-commit, and core.hooksPath set to .githooks")
    print("a commit on a red guard pass is now refused; `git commit --no-verify` is the founder's explicit override")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install-hook", action="store_true")
    ap.add_argument("--list", action="store_true", help="print what is covered and why, and run nothing")
    a = ap.parse_args()
    if a.install_hook:
        return install_hook()
    if a.list:
        for path, why in GUARDS:
            print(f"{path}\n    {why}")
        return 0

    missing = [p for p, _ in GUARDS if not (REPO / p).exists()]
    if missing:
        print(f"guard pass REFUSED: listed files that do not exist: {missing}")
        return 1

    python = _python()
    started = time.monotonic()
    proc = subprocess.run([python, "-m", "pytest", *[p for p, _ in GUARDS], "-q", "-p", "no:cacheprovider"],
                          cwd=REPO, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        print(f"\nguard pass FAILED in {elapsed:.0f}s. Fix these before committing; the full suite would have found "
              f"them forty minutes later.")
        return proc.returncode
    if elapsed > BUDGET_S:
        print(f"\nguard pass passed but took {elapsed:.0f}s, over its {BUDGET_S:.0f}s budget. A guard slow enough to "
              f"be skipped is not a guard: move the slow file out, or raise the budget deliberately.")
        return 1
    print(f"\nguard pass OK in {elapsed:.0f}s ({len(GUARDS)} checks). The full suite still gates the tag.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
