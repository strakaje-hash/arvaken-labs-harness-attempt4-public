"""Task 1.4 (HARD STOP): what the kernel under this pod allows. Run on the live pod:

  pytest tests/env/test_isolation.py -k "test_unshare_capability and test_iptables_available"

These tests RECORD, they do not demand: each writes what it found to `$MARK_RUNS/isolation-capabilities.json`
and asserts only that the measurement was made and is internally consistent. The tier chosen from the record is
asserted in test_tier_matches_capabilities.

**The record goes to the run directory, never into the checkout** (2026-09-22). It was written beside this file
until then, which left the pod's tree dirty after every env-test -- and a bench run refuses a dirty checkout, so
it would have blocked the matrix at the moment it started. An ignore rule would have been the wrong fix: it makes
the next artifact land there too. Nothing a test writes belongs in the checkout. What the manifest records is not
this file anyway: `runner` calls `isolation.probe()` itself, so this is the copy a person reads.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mark_platform.isolation import probe, summary_line

RECORD = Path(os.environ.get("MARK_RUNS") or tempfile.gettempdir()) / "isolation-capabilities.json"


@pytest.fixture
def caps(agents_workdir, monkeypatch):
    # the probe runs runuser commands as the agents' user from whatever folder it is in: that folder is where agents work
    monkeypatch.chdir(agents_workdir)
    rec = probe()
    RECORD.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    print("\n" + summary_line(rec))
    return rec


def test_unshare_capability(caps):
    assert caps["platform"] == "linux"
    for k in ("unshare_user", "unshare_net", "unshare_pid", "bwrap"):
        assert "ok" in caps[k] and "detail" in caps[k]
    # consistency: bwrap with --unshare-user cannot succeed where unshare -U fails
    if not caps["unshare_user"]["ok"]:
        assert not caps["bwrap"]["ok"], caps["bwrap"]
    assert caps["seccomp_mode"] is not None and caps["cap_eff"]


def test_iptables_available(caps):
    assert "ok" in caps["iptables"]
    if not caps["cap_net_admin"]:
        assert not caps["iptables"]["ok"], "iptables succeeded without CAP_NET_ADMIN; the capability record is wrong"


def test_tier_matches_capabilities(caps):
    if caps["unshare_user"]["ok"] and caps["bwrap"]["ok"] and caps["cap_net_admin"] and caps["iptables"]["ok"]:
        assert caps["tier"] == "A" and caps["egress_control"] == "enforced"
    elif caps["runuser_runner"]["ok"]:
        assert caps["tier"] == "B" and caps["egress_control"] == "best_effort"
    else:
        assert caps["tier"] == "none"
