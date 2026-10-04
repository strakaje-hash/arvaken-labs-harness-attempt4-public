"""R20 as a check that fails at authoring time (attempt 4, 2026-09-21).

An assertion wrapped in `if ev is not None:` around a path that did not exist never ran, the tally said "4 passed",
and two real defects sat behind it. TESTING.md R20 is the rule; `mark_platform.assertion_shapes` is the rule enforced.

This file is the check's own R10: the fixture holds each shape that MUST be flagged and each ordinary conditional that
must NOT be, and both directions are asserted by the source line, not by a hard-coded line number -- so the fixture can
be edited without the test quietly drifting into asserting nothing.
"""
from pathlib import Path

import pytest

from mark_platform.assertion_shapes import (KNOWN, ONLY_IN_A_LOOP, SKIPPED_BY_EXISTENCE, SWALLOWED, exemptions,
                                            python_test_files, scan, scan_file, unbaselined)

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "packages" / "platform" / "tests" / "fixtures" / "r20" / "r20_shapes_fixture.py"


def _flagged(path: Path) -> dict[str, str]:
    """{the source line that was flagged: the rule}, so the assertions read as the code they are about."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return {lines[f.line - 1].strip(): f.rule for f in scan_file(path)}


def test_every_shape_that_has_bitten_is_flagged_by_its_own_line():
    flagged = _flagged(FIXTURE)
    assert flagged.get('assert json.loads(p.read_text())["k"] == 1          # line 18: skipped when the path is absent') == SKIPPED_BY_EXISTENCE
    assert flagged.get('assert ev["world_policy"] == ["INV-7"]              # line 25: the case itself') == SKIPPED_BY_EXISTENCE
    assert flagged.get("assert got is True                                  # line 31") == SKIPPED_BY_EXISTENCE
    assert flagged.get("assert 1 == 2                                       # line 36: caught and discarded") == SWALLOWED
    assert flagged.get('assert r["ok"]                                      # line 44: a loop over nothing asserts nothing') == ONLY_IN_A_LOOP
    assert len(flagged) == 6, flagged   # the five shapes plus the bare-marker case below


def test_a_bare_marker_is_a_suppression_and_does_not_exempt():
    """`# r20:` with a reason is an exception someone had to justify. `# r20` alone is a suppression, and is refused."""
    flagged = _flagged(FIXTURE)
    assert flagged.get('assert json.loads(p.read_text())["k"] == 1          # line 88: a bare marker is a suppression, not an exception') == SKIPPED_BY_EXISTENCE
    # the exempted guard IS allowed: its assertion is not among the findings
    src = FIXTURE.read_text(encoding="utf-8").splitlines()
    exempted = next(i for i, l in enumerate(src, 1) if "absence is the laptop case" in l)
    flagged_lines = {f.line for f in scan_file(FIXTURE)}
    assert exempted not in flagged_lines and exempted + 1 not in flagged_lines


def test_ordinary_conditionals_and_real_assertions_are_not_flagged():
    """A check that fires everywhere gets suppressed, and a suppressed check is a silent skip in a new costume."""
    src = FIXTURE.read_text(encoding="utf-8").splitlines()
    flagged_lines = {f.line for f in scan_file(FIXTURE)}
    for i, line in enumerate(src, 1):
        if "# a value test" in line or "the loop ran, and that is asserted" in line:
            assert i not in flagged_lines, f"line {i} is an ordinary conditional and must not be flagged: {line.strip()}"
    names_flagged = set()
    for f in scan_file(FIXTURE):
        for j in range(f.line, 0, -1):
            if src[j - 1].startswith("def test_"):
                names_flagged.add(src[j - 1].split("(")[0][len("def "):])
                break
    assert all(n.startswith("test_flag_") or n == "test_not_exempted_without_a_reason" for n in names_flagged), names_flagged
    assert not any(n.startswith("test_ok_") for n in names_flagged), names_flagged


def test_no_new_assertion_can_be_skipped_without_anyone_noticing():
    """The tree as it stands, minus the dated freeze. A NEW finding fails here, naming the file, the line, the test
    and the shape -- at authoring time, not after a 34-minute run that passed while an assertion sat unreached."""
    findings = unbaselined()
    assert findings == [], "assertions that can be skipped without anyone noticing:\n  " + "\n  ".join(repr(f) for f in findings)
    for file, line, reason in exemptions():
        assert len(reason) > 15, f"{file}:{line}: an exemption's reason must say something: {reason!r}"


def test_the_scan_reaches_the_suite_and_skips_fixtures():
    files = {p.name for p in python_test_files()}
    assert "test_row_reproduction_and_reserved_keys.py" in files and "test_claimed_vs_landed_pipeline.py" in files
    assert len(files) > 40, len(files)
    assert "r20_shapes_fixture.py" not in files   # fixtures are read by path, never scanned as part of the tree
    assert not any("fixtures" in str(p) for p in python_test_files())


def test_a_file_that_does_not_parse_yields_nothing_and_fails_collection_anyway(tmp_path):
    bad = tmp_path / "test_bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    assert scan_file(bad) == []
    with pytest.raises(SyntaxError):
        __import__("ast").parse(bad.read_text(encoding="utf-8"))


def test_the_freeze_cannot_grow_and_every_entry_in_it_is_still_real():
    """The debt list is dated and finite. An entry that no longer appears has been fixed and must leave the list, so the
    freeze shrinks and never quietly covers something new. The instrument's own suites carry none of it."""
    found = {f.key for f in scan()}
    gone = sorted(k for k in KNOWN if k not in found)
    assert gone == [], f"fixed, and still in the freeze -- remove them: {gone}"
    assert len(KNOWN) == 18, f"the freeze is dated 2026-09-21 at 18 entries and may only shrink; it now has {len(KNOWN)}"
    assert not any(k[0].startswith(("packages/platform/", "packages/probes/")) for k in KNOWN), "the instrument's own suites are clean"
def test_the_security_properties_are_named_and_still_tracked():
    """Founder ruling 2026-09-21: the security-property tests do not age on the freeze. Each is a guarantee the customer
    letter and the boundary statement rest on, and each currently passes on an empty collection -- "no service role holds
    UPDATE" is green on a schema that never migrated. They leave the list first."""
    from mark_platform.assertion_shapes import PRIORITY

    tracked = {k[1] for k in KNOWN}
    assert PRIORITY <= tracked, f"named as priority but not in the freeze: {sorted(PRIORITY - tracked)}"
    assert len(PRIORITY) == 7, f"the count is stated in the module comment and must match: {len(PRIORITY)}"
    # they are where the suites need Postgres or a pod, which is why they are frozen rather than fixed here
    files = {k[0] for k in KNOWN if k[1] in PRIORITY}
    assert all(f.startswith(("packages/record/", "packages/product/", "tests/env/")) for f in files), files
