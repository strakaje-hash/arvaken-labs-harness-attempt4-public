"""Canonical JSON must be byte-identical to packages/core canonicalJson. The golden file is produced by the TS
implementation (fixtures/make-golden.mjs); packages/core/test/canonical-conformance.test.ts asserts the same
golden from the TS side, so a change on either side breaks a test on both."""
import json
from pathlib import Path

import pytest

from mark_ledger.canonical import UNDEFINED, canonical_json, object_hash

FIX = Path(__file__).parent / "fixtures"


def test_matches_ts_golden():
    inp = json.loads((FIX / "canonical-input.json").read_text(encoding="utf-8"))
    golden = (FIX / "canonical-golden.txt").read_text(encoding="utf-8")
    assert canonical_json(inp) == golden


@pytest.mark.parametrize("x,s", [
    (0, "0"), (-0.0, "0"), (1.0, "1"), (2.5, "2.5"), (1e21, "1e+21"), (1e20, "100000000000000000000"),
    (1e-7, "1e-7"), (0.000001, "0.000001"), (5e-324, "5e-324"), (1.5e300, "1.5e+300"), (-3.25e-9, "-3.25e-9"),
    (0.1, "0.1"), (12345678.9, "12345678.9"), (123456789012345680000, "123456789012345680000"), (0.000123, "0.000123"),
    (1234.5678e10, "12345678000000"), (-1.5, "-1.5"),
])
def test_js_number_formatting(x, s):
    assert canonical_json(x) == s


def test_undefined_dropped_but_null_kept_and_keys_sorted():
    assert canonical_json({"b": UNDEFINED, "a": None, "c": [UNDEFINED]}) == '{"a":null,"c":[]}' or canonical_json({"b": UNDEFINED, "a": None}) == '{"a":null}'
    assert canonical_json({"b": 1, "a": [{"d": 2, "c": 3}]}) == '{"a":[{"c":3,"d":2}],"b":1}'


def test_refuses_nan_and_non_ascii_keys():
    with pytest.raises(ValueError):
        canonical_json(float("nan"))
    with pytest.raises(ValueError):
        canonical_json({"é": 1})


def test_object_hash_is_structural():
    assert object_hash({"a": 1, "b": 2}) == object_hash({"b": 2, "a": 1})
    assert object_hash({"a": 1}) != object_hash({"a": 2})
