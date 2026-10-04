"""The pre-registration lists the instrument; this asserts the list is the instrument (founder ruling 2026-09-22).

A pre-registration is signed by hash and anchored, so a version in it that has moved in the tree is not a typo --
it is a signed document describing an instrument that does not exist. Nothing else catches that: the suite tests
the code, the census tests the pins, and the document is prose to both.

**Why this exists rather than a careful read.** The first draft listed twelve of the tree's thirteen workloads. The
omission was not carelessness about the document; it was a truncated *listing* -- collected with a `tail` that cut
the top of a sorted output, so `wl.batch-no-memory` never reached the page. The list that produced the document
was wrong, and only checking the document against the tree could find it. Same family as every other defect that
night: a reading cut by the tool that read it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mark_platform.workloads import load
from mark_probes import PROBES

# The pre-registration that describes the tree NOW. Freeze-3's (attempt4-preregistration.md) is signed and anchored and stays
# byte-for-byte as signed; once the tree moved past it (ks.propagation v5, 2026-09-23) the document that must match the tree
# is freeze-4's draft, which is signed in its turn at freeze-4. A signed document is checked by its hash, not by this test.
DOC = Path(__file__).resolve().parents[3] / "benchmarks" / "attempt4-preregistration-freeze4.md"
ROW = "| `{id}` | {version} |"


def _doc() -> str:
    if not DOC.exists():
        pytest.skip(f"no pre-registration at {DOC}")
    return DOC.read_text(encoding="utf-8")


def _lists(doc: str, artifact_id: str, version: int) -> bool:
    """A row naming this artifact at this version, with or without the emphasis a moved version carries."""
    plain = ROW.format(id=artifact_id, version=version)
    bold = ROW.format(id=artifact_id, version=f"**{version}**")
    return plain in doc or bold in doc


def test_every_workload_in_the_tree_is_listed_at_its_current_version():
    doc, w = _doc(), load()
    assert len(w) >= 12, "the tree's workloads did not load; an empty set would make the loop below assert nothing"
    missing = sorted((k, v["version"]) for k, v in w.items() if not _lists(doc, k, v["version"]))
    assert not missing, (
        f"the pre-registration does not list these at their current version: {missing}. A signed document naming a "
        f"version the tree has moved past describes an instrument that does not exist.")


def test_every_probe_in_the_tree_is_listed_at_its_current_version():
    doc = _doc()
    assert len(PROBES) >= 10, "the probe registry did not load"
    missing = sorted((k, p.version) for k, p in PROBES.items() if not _lists(doc, k, p.version))
    assert not missing, f"the pre-registration does not list these probes at their current version: {missing}"


def test_the_document_lists_nothing_the_tree_does_not_have():
    """The other direction. A row for an artifact that no longer exists is the same defect facing the other way,
    and it is the one a careful read of the tree would never find."""
    doc, w = _doc(), load()
    known = set(w) | set(PROBES)
    listed = {m.group(1) for m in re.finditer(r"^\| `(wl\.[a-z0-9.-]+|ks\.[a-z_]+|gate\.[a-z_]+|scope\.[a-z_]+|evidence\.[a-z_]+|control\.[a-z_]+)` \|", doc, re.M)}
    assert listed, "no artifact rows found in the document: the regex above matched nothing, so this asserted nothing"
    unknown = sorted(listed - known)
    assert not unknown, f"the pre-registration lists artifacts the tree does not have: {unknown}"


def test_the_check_would_notice_a_moved_version(tmp_path):
    """R10/R22 together: the guard bites on a stale row, and does not bite on a current one. Without this, a
    matcher that never matched would pass both tests above by finding nothing missing."""
    w = load()
    some_id, some_version = next(iter(sorted((k, v["version"]) for k, v in w.items())))
    current = f"| `{some_id}` | {some_version} |"
    stale = f"| `{some_id}` | {some_version + 1} |"
    assert _lists(current, some_id, some_version), "a row at the current version must be recognised"
    assert not _lists(stale, some_id, some_version), "a row at a moved version must NOT be recognised"
    assert _lists(f"| `{some_id}` | **{some_version}** |", some_id, some_version), "emphasis marks a moved version and still counts"


INSTRUMENT_COMMIT = "17794bf"   # the commit the certifying pass ran on; the freeze-4 record says the instrument is frozen there
INSTRUMENT_PATHS = ("packages/platform/mark_platform", "packages/probes", "packages/ledger", "packages/timing", "gates", "targets")


def test_the_document_cites_the_freeze_tag_and_its_commit():
    doc = _doc()
    assert "attempt4-freeze-4" in doc and f"`{INSTRUMENT_COMMIT}`" in doc, "freeze-4's record cites its tag and the instrument's commit"
    # the two pins a run manifest carries, and what each points at
    assert "`ee7ab65d74c67291`" in doc, "the gate key it is signed with"
    assert "sha256:66e6be2e" in doc, "the pod image digest the instrument ran from"


def test_the_instrument_the_record_names_is_the_tree_s():
    """The record says nothing under the instrument's paths changes between the certifying pass's commit and the tag. A claim
    a signed record makes about the tree is checked against the tree, and fails the moment one of those files moves."""
    import subprocess

    repo = DOC.parents[1]
    if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{INSTRUMENT_COMMIT}^{{commit}}"], capture_output=True).returncode != 0:
        pytest.skip(f"{INSTRUMENT_COMMIT} is not in this checkout's history (a shallow clone)")
    moved = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", INSTRUMENT_COMMIT, "--", *INSTRUMENT_PATHS],
                           capture_output=True, text=True, check=True).stdout.split()
    assert not moved, f"the record says the instrument is frozen at {INSTRUMENT_COMMIT}, and these moved: {moved}"
