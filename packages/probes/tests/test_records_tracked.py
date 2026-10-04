"""Founder rule 2026-09-12: a commit that claims to record something must be tested to contain it.

Commit 7f3471d said it recorded benchmarks/runs/decisive-2-20260912/HISTORY.md; the unanchored `runs/` ignore rule
had silently dropped the file, and the same rule had kept the publicly anchored first-session bundle out of the
repository since 2026-09-11. Every record the constitution names must exist, be tracked, and not sit under an ignore
rule (a force-added record under one is a trap for the next edit). A pattern that matches nothing fails: absence is
not success. No git checkout is a failure, not a skip."""
import subprocess
from pathlib import Path

from mark_probes.constitution import RECORDS

REPO = Path(__file__).resolve().parents[3]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def record_problems(repo: Path, records) -> list[str]:
    inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return [f"{repo} is not a git checkout: the records cannot be checked"]
    tracked = set(_git(repo, "ls-files").stdout.splitlines())
    problems: list[str] = []
    for pattern, _why in records:
        files = sorted(p.relative_to(repo).as_posix() for p in repo.glob(pattern) if p.is_file())
        if not files:
            problems.append(f"{pattern}: matches no file")
        for f in files:
            if f not in tracked:
                problems.append(f"{f}: not tracked")
            if _git(repo, "check-ignore", "-q", "--no-index", f).returncode == 0:
                problems.append(f"{f}: under an ignore rule")
    return problems


def test_every_record_the_constitution_names_exists_is_tracked_and_is_not_under_an_ignore_rule():
    assert record_problems(REPO, RECORDS) == []


def test_the_check_catches_a_dropped_record_a_force_added_one_and_a_pattern_matching_nothing(tmp_path):
    """The check is tested against the failures it exists for, so it cannot be green by construction."""
    assert _git(tmp_path, "init", "-q").returncode == 0
    (tmp_path / ".gitignore").write_text("runs/\n")
    for d in ("benchmarks/runs/a", "benchmarks/runs/b", "benchmarks/runs/c"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "benchmarks/runs/a/NOTES.md").write_text("dropped by the ignore rule")
    (tmp_path / "benchmarks/runs/b/NOTES.md").write_text("force-added under the ignore rule")
    _git(tmp_path, "add", "-f", "benchmarks/runs/b/NOTES.md")
    problems = record_problems(tmp_path, [("benchmarks/runs/*/NOTES.md", ""), ("docs/ANCHORS.md", "")])
    assert "benchmarks/runs/a/NOTES.md: not tracked" in problems
    assert "benchmarks/runs/a/NOTES.md: under an ignore rule" in problems
    assert "benchmarks/runs/b/NOTES.md: not tracked" not in problems
    assert "benchmarks/runs/b/NOTES.md: under an ignore rule" in problems
    assert "docs/ANCHORS.md: matches no file" in problems
    # and the clean case: anchored rule, tracked record
    (tmp_path / ".gitignore").write_text("/runs/\n")
    (tmp_path / "benchmarks/runs/c/NOTES.md").write_text("a record")
    _git(tmp_path, "add", "benchmarks/runs/c/NOTES.md")
    assert [p for p in record_problems(tmp_path, [("benchmarks/runs/*/NOTES.md", "")]) if "runs/c/" in p] == []
