"""Canonical JSON, byte-identical to packages/core/src/bundle.ts `canonicalJson`.

Rules (the TS side is the reference; this is a port, and tests/fixtures/canonical.* pins both):
  - objects: keys sorted by code point, entries whose value is `undefined` dropped (Python: a `_Undefined`
    sentinel; `None` is JSON null and is KEPT, as in TS);
  - arrays in order; no whitespace;
  - numbers printed the way JavaScript's Number::toString does (shortest round-trip digits, integral floats
    without ".0", exponent form only below 1e-6 or at/above 1e21, exponent without zero padding);
  - strings escaped like JSON.stringify: double quote, backslash and control characters only; non-ASCII stays raw.
Keys must be ASCII: JS sorts by UTF-16 code unit and Python by code point, which differ above the BMP, so a
non-ASCII key is refused rather than silently ordered two ways.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

UNDEFINED = object()  # a value to drop, like a JS `undefined` property

# ---- the signing-time portability rule (A9) ---------------------------------------------------------------------
# **Rendering correctly and refusing to sign are different layers, and both are right.** `_js_number` above now
# prints what JavaScript prints, including for values this rule refuses -- so the parity fixtures stay unit tests
# of rendering. This is the other layer: before a document is signed, it must not carry a number of magnitude
# 2^53 or beyond, whatever its Python type.
#
# The rule is deliberately the conservative one the record (migration 0007), `mark_product/portable.py` and
# `apps/console/src/portable.ts` already enforce, rather than the narrower rule this implementation could
# defend for itself. Founder's ruling:
#
#   "Consistency beats one implementation being cleverer than the other three: a document the ledger could
#    render correctly but the platform would refuse is a document that shouldn't exist, because no legitimate
#    field in a gate, a manifest, or an attestation needs an integer that size. If one ever does, that's a
#    design decision, not a number."
SAFE_INTEGER = 9007199254740991   # 2^53 - 1


class NotPortable(ValueError):
    """A document holds a number another implementation may render differently. `path` names the field.

    The same name and shape as `mark_product.portable.NotPortable`: one rule, one refusal, and a reader who has
    met it once does not have to learn it twice.
    """

    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"{path}: {why}")
        self.path = path
        self.why = why


def unportable(value: Any, path: str = "$") -> tuple[str, str] | None:
    """(path, why) for the first value that may not travel, else None. Does not raise; `refuse_unportable` does."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return path, "not a finite number (canonical JSON cannot carry it)"
        if abs(value) >= SAFE_INTEGER + 1:
            return path, (f"{value} is at or beyond 2^53, where implementations may render the same value with "
                          f"different digits; nothing signed here needs a number that size")
        return None
    if isinstance(value, dict):
        for k in sorted(value):
            if not isinstance(k, str):
                return path, f"object key {k!r} is not a string"
            if not k.isascii():
                return f"{path}.{k}", "object key is not ASCII; implementations sort it differently"
            found = unportable(value[k], f"{path}.{k}")
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            found = unportable(v, f"{path}[{i}]")
            if found:
                return found
        return None
    return None


def refuse_unportable(value: Any) -> None:
    """Raise `NotPortable` naming the first field that may not travel. Called before anything is signed."""
    found = unportable(value)
    if found:
        raise NotPortable(*found)


# ---- A9c: a timestamp in a signed document is a decimal string ----------------------------------------------
# Founder ruling 2026-09-20. `unportable` above already refuses a number at or past 2^53, and a nanosecond count
# is about 1.8e18 -- roughly 200x past it. But a MONOTONIC clock's magnitude is the host's uptime, not the epoch:
# on a laptop up three days it reads 3.3e14 and signs; on a Linux host up four months it reads 1.4e16 and does
# not. So the magnitude rule cannot be exercised on the machine the instrument is written on, and C2's operator
# record carried an integer `mono_ns` into a signed manifest with every local test green (freeze notes,
# 2026-09-21). This rule is about SHAPE rather than size: an integer under a key ending `_ns` is refused on every
# host, whatever the host's uptime, and the decimal string the ruling requires is accepted.
NS_KEY_SUFFIX = "_ns"


def integer_timestamp(value: Any, path: str = "$") -> tuple[str, str] | None:
    """(path, why) for the first `*_ns` field holding an integer, else None. Does not raise."""
    if isinstance(value, dict):
        for k in sorted(value):
            v = value[k]
            if isinstance(k, str) and k.endswith(NS_KEY_SUFFIX) and isinstance(v, int) and not isinstance(v, bool):
                return f"{path}.{k}", (f"a nanosecond timestamp is a decimal string in a signed document, not the "
                                       f"integer {v} (founder ruling 2026-09-20, A9c): write str(...) at the field's "
                                       f"writer. An integer here signs on a host with a short uptime and is refused "
                                       f"on one with a long uptime, which is a signature that depends on the machine")
            found = integer_timestamp(v, f"{path}.{k}")
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            found = integer_timestamp(v, f"{path}[{i}]")
            if found:
                return found
        return None
    return None


def refuse_integer_timestamps(value: Any) -> None:
    """Raise `NotPortable` naming the first `*_ns` field that is an integer. Called before anything is signed."""
    found = integer_timestamp(value)
    if found:
        raise NotPortable(*found)


def _js_number(x: float) -> str:
    if isinstance(x, bool):  # bool is an int subclass; handled by caller, guard anyway
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if not math.isfinite(x):
        raise ValueError("canonical JSON cannot carry NaN or Infinity (JSON.stringify would emit null)")
    if x == 0:
        return "0"  # JS prints -0 as "0"
    # **No shortcut for integral floats** (A9b). `str(int(x))` prints the double's exact value, and JavaScript
    # prints its *shortest round-trip* digits. For 1.2345678901234568e20 those differ:
    #
    #     str(int(x))  ->  123456789012345683968
    #     JS toString  ->  123456789012345680000
    #
    # which is the same document hashing two ways -- the exact failure the portability rule exists to prevent,
    # and it was live here. The parity fixtures did not catch it because their inputs arrive as JSON *integer*
    # literals: Python takes the int branch above and prints the same digits TS prints from the double, so the
    # two agreed by taking different paths to one answer. A float reaching that value by arithmetic -- a
    # computed total, a duration -- diverged silently.
    #
    # The general path below already implements ECMA-262 Number::toString over `repr`'s shortest round-trip
    # digits, and handles integral values correctly. It just was not being reached.
    #
    # Shortest round-trip digits (Python's repr uses the same algorithm family as V8: shortest that round-trips).
    r = repr(abs(x))
    if "e" in r or "E" in r:
        mant, exp = r.lower().split("e")
        e = int(exp)
    else:
        mant, e = r, 0
    if "." in mant:
        ip, fp = mant.split(".")
    else:
        ip, fp = mant, ""
    digits = (ip + fp).lstrip("0")
    # n = position of the decimal point relative to the first significant digit (ECMA-262 Number::toString)
    n = len(ip.lstrip("0")) + e if ip.strip("0") else e - (len(fp) - len(fp.lstrip("0")))
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    sign = "-" if x < 0 else ""
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e10 = n - 1
        es = ("+" if e10 >= 0 else "-") + str(abs(e10))
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + es
    return sign + s


# JSON.stringify short escapes, keyed by code point, spelled without backslash literals on purpose (tooling).
_BS = chr(92)
_SHORT_ESCAPES = {8: _BS + "b", 12: _BS + "f", 10: _BS + "n", 13: _BS + "r", 9: _BS + "t"}


def _js_string(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif o in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[o])
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, (list, tuple)):
        # JS: [undefined].map(JSON.stringify).join(',') renders an undefined element as an empty slot.
        return "[" + ",".join("" if v is UNDEFINED else canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise TypeError(f"object keys must be strings, got {type(k).__name__}")
            if not k.isascii():
                raise ValueError(f"object key {k!r} is not ASCII; JS and Python would sort it differently")
        items = sorted((k, v) for k, v in value.items() if v is not UNDEFINED)
        return "{" + ",".join(_js_string(k) + ":" + canonical_json(v) for k, v in items) + "}"
    if hasattr(value, "to_json"):
        return canonical_json(value.to_json())
    raise TypeError(f"cannot canonicalise {type(value).__name__}")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def object_hash(value: Any) -> str:
    """SHA-256 over the canonical JSON bytes: the identity of any structured object in the ledger."""
    return sha256_hex(canonical_json(value))
