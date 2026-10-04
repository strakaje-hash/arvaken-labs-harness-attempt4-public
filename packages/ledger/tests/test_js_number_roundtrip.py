"""A9b: this renders a number the way JavaScript renders it, including where the two could differ.

`canonical_json` is a port of the TypeScript `canonicalJson`, and a signature is taken over its bytes. If the
two implementations print one value differently, the same document signs to two hashes and a good signature
looks invalid -- which discredits real evidence at the moment somebody checks it.

**The bug this file exists for.** `_js_number` short-circuited integral floats with `str(int(x))`, which prints
the double's *exact* value. JavaScript prints its *shortest round-trip* digits. For 1.2345678901234568e20:

    str(int(x))  ->  123456789012345683968
    JS toString  ->  123456789012345680000

`test_canonical.py`'s parity cases did not catch it, and the reason is worth keeping: their inputs arrive as
JSON **integer literals**, so Python took the `isinstance(x, int)` branch and printed the same digits TS printed
from the double. The two agreed by taking different paths to one answer. A float reaching that value any other
way -- arithmetic, a computed total, a duration in nanoseconds -- diverged silently and would have been signed.

So these cases are all **floats**, deliberately: that is the path the fixtures leave uncovered.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mark_ledger.canonical import canonical_json

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize("value,expected", [
    # The case that was wrong. JS: (1.2345678901234568e20).toString() === "123456789012345680000"
    (1.2345678901234568e20, "123456789012345680000"),
    # Its neighbours, to pin that the fix is the shortest-round-trip rule and not a special case for one value.
    #
    # This expectation was wrong when first written, and how it was corrected matters: BOTH ...567000 and
    # ...568000 round-trip to this double, so "shortest" does not decide it. ECMA-262 breaks the tie by taking
    # the digits closest to the actual value -- which is 12345678901234567168, so ...567000 (168 away) wins
    # over ...568000 (832 away). Established from the standard, not by pasting what the code printed, which
    # would have made this test its own oracle.
    (1.2345678901234568e19, "12345678901234567000"),
    (9.007199254740994e15, "9007199254740994"),
    (1.7976931348623157e308, "1.7976931348623157e+308"),
    # Integral floats that were already right, so a regression in either direction shows here.
    (1e20, "100000000000000000000"),
    (1e21, "1e+21"),
    (123.0, "123"),
    (-4.0, "-4"),
    (0.0, "0"),
    (-0.0, "0"),
])
def test_an_integral_float_prints_its_shortest_round_trip(value, expected):
    assert canonical_json(value) == expected


def test_the_exact_double_value_is_not_what_javascript_prints():
    """The claim stated as its own assertion, so the reasoning is checkable and not just asserted in prose.

    If these two ever become equal, the value stopped being a case where the distinction matters and this file
    is testing nothing -- which is the failure mode of a regression test whose subject has moved."""
    x = 1.2345678901234568e20
    assert str(int(x)) == "123456789012345683968", "the double's exact value"
    assert canonical_json(x) == "123456789012345680000", "what JavaScript prints, and now what we print"
    assert str(int(x)) != canonical_json(x), "the two differ; that difference was the bug"


def test_an_integer_literal_and_the_float_it_parses_to_still_agree_where_they_should():
    """The coincidence that hid the bug, pinned so it stays a coincidence and not a dependency.

    A JSON integer literal takes the `int` branch and prints Python's digits; the same text read as a float
    takes the rendering path. Where JavaScript would print the same thing for both, so do we."""
    from_literal = canonical_json(json.loads("100000000000000000000"))
    from_float = canonical_json(1e20)
    assert from_literal == from_float == "100000000000000000000"


def test_the_typescript_golden_still_matches_byte_for_byte():
    """The reference check, against a fixture that can now fail it.

    `canonical-golden.txt` is produced by the TypeScript implementation. Regenerating it after A9b changed
    nothing, and that was the problem rather than the reassurance: every number in it arrived as a JSON
    **integer literal**, so Python took the `int` branch and the float path -- the one A9b fixed -- was never
    compared against TS at all. `123456789012345680000` sat in that fixture as an integer literal: the exact
    value that exposed the bug, rendered correctly by accident.

    The input now also carries those values in float form, so the two implementations are compared on the path
    where they could diverge. Checked: the pre-A9b canonicaliser does **not** match this golden.

    If this fails after a change to `_js_number`, the golden is regenerated from TypeScript -- never from here.
    TS is the reference for how JavaScript prints numbers; this is the port."""
    document = json.loads((FIXTURES / "canonical-input.json").read_text(encoding="utf-8"))
    golden = (FIXTURES / "canonical-golden.txt").read_text(encoding="utf-8").strip()
    assert canonical_json(document) == golden


def test_the_parity_fixture_still_has_teeth():
    """**A guard on the fixture, not on the code.** The parity fixture was blind for as long as it existed, and
    nothing would have said so -- it was green throughout. What made it blind was a property of the input:
    every number was an integer literal.

    So the property is asserted. The input must contain at least one float whose shortest-round-trip rendering
    differs from its exact integer value, because that is the only kind of value that can tell the two
    implementations apart. If someone later 'tidies' those entries away, this fails instead of the fixture
    quietly going back to sleep."""
    document = json.loads((FIXTURES / "canonical-input.json").read_text(encoding="utf-8"))

    def floats(v):
        if isinstance(v, dict):
            for x in v.values():
                yield from floats(x)
        elif isinstance(v, list):
            for x in v:
                yield from floats(x)
        elif isinstance(v, float):
            yield v

    discriminating = [f for f in floats(document)
                      if f == int(f) and abs(f) < 1e21 and str(int(f)) != canonical_json(f)]
    assert discriminating, ("canonical-input.json no longer contains a float that renders differently from its "
                            "exact integer value, so it cannot detect the divergence A9b fixed")
