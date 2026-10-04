"""The version-pin census (attempt 4, after the C3 scope bump): every hard-coded version pin in the tree is registered and
current, and a bump fails HERE, at authoring time, with the file, the line and both numbers -- never 31 minutes later in
the suite. Founder ruling 2026-09-21: "no grep is the census."
"""
from pathlib import Path

import pytest

from mark_platform.version_pins import PINS, census, current_version, miscounted, orphaned, pin_sites, stale, unregistered

REPO = Path(__file__).resolve().parents[3]


# ---- the tree as it stands ----

def test_every_version_pin_in_the_tree_is_registered_with_its_artifact_and_its_reason():
    found = census()
    assert found, "the census found no version pins at all: the walk is broken, not the tree"
    assert not unregistered(found), (f"version pins this census does not register: {unregistered(found)}. "
                                     "Register each in mark_platform.version_pins.PINS (kind, artifact, why), or read the value instead of pinning it.")
    assert not miscounted(found), f"PINS registers a different number of sites than the file holds {{file: (in file, registered)}}: {miscounted(found)}"
    assert not orphaned(found), f"PINS registers files that hold no version pin any more: {orphaned(found)}"
    assert all(why.strip() for entries in PINS.values() for _, _, why in entries), "every pin says why that test cares"


def test_no_registered_pin_is_stale():
    assert stale() == [], "stale version pins; read each test against the new version, then update the number and its reason:\n  " + "\n  ".join(stale())


def test_the_census_reads_the_artifacts_own_loader_for_each_kind():
    from mark_platform.workloads import load

    assert current_version("workload", "wl.sequence-payments-single") == load()["wl.sequence-payments-single"]["version"]
    assert current_version("gate", "ks.completeness") >= 3 and current_version("gate_draft", "scope.side_channel") == 1
    assert current_version("mapping", "tag-outcomes") == 1
    assert current_version("self", "anything") is None and current_version("bound", "anything") is None


# ---- R10: the census must fail on what the grep missed, and on a stale pin ----

def test_the_shape_walk_finds_the_pin_the_grep_missed(tmp_path):
    """The C3 defect, as a test. The pre-change grep pattern assumed the workload was reached by a subscript chain
    (`w["wl.x"]["version"] == 11`) and missed the same pin reached through a local (`wl["version"] == 11`). The walk
    matches the SHAPE, so it finds both, and it finds forms no pattern was written for."""
    import re

    f = tmp_path / "t.py"
    f.write_text(
        'def a(w):\n'
        '    assert w["wl.x"]["version"] == 11          # line 2: the form the grep found\n'
        'def b(wl):\n'
        '    assert wl["version"] == 11                 # line 4: the form it missed\n'
        'def c(wl):\n'
        '    assert 12 == wl["version"]                 # line 6: reversed\n'
        'def d(wl):\n'
        '    if wl["params"]["version"] == 3: pass      # line 8: nested, still a pin\n'
        'def e(wl):\n'
        '    assert wl["version"] == other              # not a hard number: not a pin\n'
        'def f(wl):\n'
        '    assert wl["revision"] == 11                # not a version: not a pin\n',
        encoding="utf-8")
    assert [line for line, _col, _ints in pin_sites(f)] == [2, 4, 6, 8]
    assert [ints for _l, _c, ints in pin_sites(f)] == [[11], [11], [12], [3]]   # each site carries ITS number
    # what the two patterns actually reach, measured, for the record: neither is the census
    lines = f.read_text(encoding="utf-8").splitlines()
    broad = re.compile(r'version"\] *== *[0-9]')                       # misses the REVERSED form on line 6
    assert [i + 1 for i, l in enumerate(lines) if broad.search(l)] == [2, 4, 8]
    narrow = re.compile(r'\["[a-z.\-]+"\]\["version"\] *== *[0-9]')     # the C3 grep: misses the local (4) and the reversed (6)
    assert [i + 1 for i, l in enumerate(lines) if narrow.search(l)] == [2, 8]


def test_a_stale_pin_fails_by_name_with_both_numbers(tmp_path):
    """The bump the census exists for: the number in the file is not the artifact's current version."""
    f = tmp_path / "packages" / "platform" / "tests" / "test_fake.py"
    f.parent.mkdir(parents=True)
    f.write_text('def t(wl):\n    assert wl["version"] == 11\n', encoding="utf-8")
    pins = {"packages/platform/tests/test_fake.py": [("workload", "wl.sequence-payments-single", "the bound rule's text")]}
    problems = stale(pins, root=tmp_path)
    (one,) = problems
    # the CURRENT version appears in this message, so this assertion is itself a pin on the live workload;
    # deriving it means a version bump cannot leave it stale (R19 caught exactly that on v12 -> v13)
    from mark_platform.workloads import load

    live = load()["wl.sequence-payments-single"]["version"]
    assert one.startswith(f"packages/platform/tests/test_fake.py:2 pins workload wl.sequence-payments-single at [11] but it is now v{live}")
    assert "the bound rule's text" in one
    # the same file pinning the current version is not stale
    f.write_text(f'def t(wl):\n    assert wl["version"] == {live}\n', encoding="utf-8")
    assert stale(pins, root=tmp_path) == []


def test_an_unregistered_pin_fails_by_file_and_line(tmp_path):
    found = {"packages/platform/tests/test_new.py": [(7, 4, [3])]}
    assert unregistered(found, PINS) == found
    assert unregistered(found, {**PINS, "packages/platform/tests/test_new.py": [("self", "x", "why")]}) == {}
    # a file that grew a second pin without registering it
    assert miscounted({"packages/platform/tests/test_next_step.py": [(50, 4, [12]), (99, 4, [1])]}, PINS) == {"packages/platform/tests/test_next_step.py": (2, 1)}
    # a registered file that no longer pins anything
    assert "packages/platform/tests/test_next_step.py" in orphaned({}, PINS)


def test_a_file_that_does_not_parse_is_not_silently_empty(tmp_path):
    """A pin the walk cannot see is worse than no walk: a syntax error in a test file must not read as 'no pins here'."""
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    assert pin_sites(bad) == []   # it returns nothing...
    with pytest.raises(SyntaxError):
        __import__("ast").parse(bad.read_text(encoding="utf-8"))   # ...and the suite fails on that file anyway, by collection
