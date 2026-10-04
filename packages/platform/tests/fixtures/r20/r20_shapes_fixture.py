"""Fixture for the R20 check: each shape that must be flagged, and each ordinary conditional that must NOT be.

Not collected as a test (it lives under fixtures/ and is excluded by the suite's rootdir); read as source by
test_assertion_shapes.py. Every function here is deliberate.
"""
import json
from pathlib import Path

import pytest


# ---- must be FLAGGED ----

def test_flag_guarded_by_exists(tmp_path):
    p = tmp_path / "maybe.json"
    if p.exists():
        assert json.loads(p.read_text())["k"] == 1          # line 18: skipped when the path is absent


def test_flag_guarded_by_is_not_none(tmp_path):
    ev = json.loads((tmp_path / "x").read_text()) if (tmp_path / "x").exists() else None
    if ev is not None:
        assert ev["world_policy"] == ["INV-7"]              # line 25: the case itself


def test_flag_bare_nullable_guard(tmp_path):
    got = (tmp_path / "y").exists()
    if got:
        assert got is True                                  # line 31


def test_flag_swallowed_assertion():
    try:
        assert 1 == 2                                       # line 36: caught and discarded
    except AssertionError:
        pass


def test_flag_only_assertions_in_a_loop():
    rows = []
    for r in rows:
        assert r["ok"]                                      # line 44: a loop over nothing asserts nothing


# ---- must NOT be flagged ----

def test_ok_unconditional():
    assert 1 == 1


def test_ok_ordinary_conditional(tmp_path):
    n = 5
    if n > 3:
        assert n == 5                                       # a value test, not an existence guard


def test_ok_loop_with_a_count_assertion():
    rows = [{"ok": True}]
    checked = 0
    for r in rows:
        assert r["ok"]
        checked += 1
    assert checked == 1                                     # the loop ran, and that is asserted


def test_ok_try_that_does_not_swallow():
    try:
        assert 1 == 1
    except AssertionError:
        raise


def test_ok_raises_is_an_assertion():
    with pytest.raises(ValueError):
        raise ValueError("x")


def test_ok_exempted_with_a_reason(tmp_path):
    p = tmp_path / "optional.json"
    if p.exists():   # r20: the pod writes this file and the laptop does not; absence is the laptop case
        assert json.loads(p.read_text())["k"] == 1


def test_not_exempted_without_a_reason(tmp_path):
    p = tmp_path / "optional.json"
    if p.exists():   # r20
        assert json.loads(p.read_text())["k"] == 1          # line 88: a bare marker is a suppression, not an exception
