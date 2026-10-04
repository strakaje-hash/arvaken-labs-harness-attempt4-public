"""The freeze-4 record commits to freeze-notes sections it does not disclose, by their SHA-256 (founder rulings 5 and 6,
2026-09-23). If a sealed section's bytes change after the record cites them, the commitment is broken silently -- so this
reads each fingerprint the record carries and recomputes it from the freeze notes, and tests the fingerprint rule itself."""
from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RECORD = REPO / "benchmarks" / "attempt4-preregistration-freeze4.md"
NOTES = REPO / "benchmarks" / "attempt4-freeze-notes.md"
ROW = re.compile(r"^\| `([^`]+)` \| [^|]+ \| `([0-9a-f]{64})` \|$")


_SPEC = importlib.util.spec_from_file_location("section_sha256", REPO / "packages" / "platform" / "scripts" / "section_sha256.py")
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def _mod():
    """One module for the whole file: a second load would raise a SectionNotFound that is a different class."""
    return _MOD


def _cited():
    text = RECORD.read_bytes().decode("utf-8").replace("\r\n", "\n")
    part = text.split("## Readings committed by fingerprint", 1)[1].split("\n## ", 1)[0]
    return [m.groups() for line in part.split("\n") if (m := ROW.match(line))]


def test_the_record_cites_both_sealed_sections():
    headings = [h for h, _ in _cited()]
    assert len(headings) == 2 and any(h.startswith("Sealed readings of the freeze-4 record") for h in headings) \
        and any(h.startswith("PHASE 1 CAPABLE-ARM RUNS DECLARED VOID") for h in headings)


@pytest.mark.parametrize("heading, sha", _cited())
def test_every_cited_fingerprint_is_the_sections_own(heading, sha):
    notes = NOTES.read_bytes().decode("utf-8")
    assert _mod().section_sha256(notes, heading) == sha, f"the freeze notes' section {heading!r} no longer has the SHA-256 the record commits to"


def test_the_rule_a_section_ends_at_the_next_level_two_heading_and_nowhere_else():
    m = _mod()
    text = "# t\n\n## A\none\n### sub\n    ## indented, not a heading\ntwo\n\n## B\nthree\n"
    assert m.section(text, "A") == "## A\none\n### sub\n    ## indented, not a heading\ntwo\n\n"
    assert m.section(text, "B") == "## B\nthree\n"
    assert m.section_sha256(text, "A") == hashlib.sha256(m.section(text, "A").encode()).hexdigest()


def test_the_fingerprint_is_of_the_committed_bytes_whatever_the_working_copys_line_endings():
    m = _mod()
    lf = "## A\none\n\n## B\n"
    assert m.section_sha256(lf.replace("\n", "\r\n"), "A") == m.section_sha256(lf, "A")


@pytest.mark.parametrize("text", ["## A\nx\n## A\ny\n", "## B\nx\n"])
def test_a_heading_that_is_missing_or_twice_is_refused(text):
    with pytest.raises(_mod().SectionNotFound):
        _mod().section(text, "A")


def test_a_one_byte_change_inside_a_sealed_section_changes_its_fingerprint():
    """R10: the check this file exists for must be able to fail."""
    m = _mod()
    notes = NOTES.read_bytes().decode("utf-8").replace("\r\n", "\n")
    heading, sha = _cited()[0]
    at = notes.index(chr(10), notes.index(f"## {heading}")) + 5   # inside the body: a changed heading is a missing section, not a changed one
    changed = notes[:at] + ("x" if notes[at] != "x" else "y") + notes[at + 1:]
    assert m.section_sha256(changed, heading) != sha
