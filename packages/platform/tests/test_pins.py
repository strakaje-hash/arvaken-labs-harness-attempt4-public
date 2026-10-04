"""The registry's pypi pins must be the versions uv.lock resolves: a registry that says 1.2.11 while the pod
installs something else is a pin in name only."""
import re
from pathlib import Path

from mark_platform.registry import load

LOCK = Path(__file__).resolve().parents[3] / "uv.lock"


def _locked_versions() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    name = None
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        m = re.match(r'^name = "(.+)"$', line)
        if m:
            name = m.group(1)
            continue
        m = re.match(r'^version = "(.+)"$', line)
        if m and name:
            out.setdefault(name, set()).add(m.group(1))
            name = None
    return out


def test_registry_pypi_pins_match_the_lockfile():
    locked = _locked_versions()
    checked = 0
    for t in load().values():
        for p in t.pypi:
            assert p["name"] in locked, f"{t.id}: {p['name']} is not in uv.lock"
            assert p["version"] in locked[p["name"]], f"{t.id}: registry pins {p['name']}=={p['version']} but uv.lock has {sorted(locked[p['name']])}"
            checked += 1
    assert checked >= 3, f"only {checked} pypi pin(s) checked: a registry that lost its pins would pass this silently"
