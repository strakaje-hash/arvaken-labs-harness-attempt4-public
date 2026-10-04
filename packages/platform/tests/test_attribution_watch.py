"""The runner watches the same-user resolver around every replication (founder ruling 2026-09-23).

"A resolver that stops or goes quiet means 'couldn't tell,' never 'no survivors.' Root watches it. If it disappears or
stops answering during a replication, that replication is `not_run: attribution_unavailable`."

Driven through `run_cell` on a real scripted scenario, with the resolver replaced by a stand-in whose health the test
sets: the laptop has no second user, so the real resolver is never started here (tests/env/test_attribution.py starts
it on the pod, as the agents' user). The stand-in is attached to the run only -- it is not registered with the owner
lookup -- so every call is still attributed by the kernel, and what is under test is the watch alone.
"""
from __future__ import annotations

import json

import pytest

from mark_platform.peer_resolver import UNAVAILABLE


class StandIn:
    """Health samples in order; after the list runs out, the last one repeats."""

    def __init__(self, samples):
        self.samples = list(samples)
        self.taken = 0

    def health(self):
        s = self.samples[min(self.taken, len(self.samples) - 1)]
        self.taken += 1
        return dict(s)

    def stop(self):
        pass


GOOD = {"pid": 500, "alive": True, "answering": True, "known_answer": True, "available": True, "why": None, "ms": 1.0}
GONE = {"pid": 500, "alive": False, "answering": False, "known_answer": False, "available": False, "ms": 0.1,
        "why": f"{UNAVAILABLE}: the resolver process (pid 500) is gone or no longer runs as uid 1001"}


@pytest.fixture
def ctx(tmp_path):
    from mark_platform.runner import calibrate, close_run, open_run

    c = open_run(tmp_path / "run", "watch", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    calibrate(c, replications=1, expected_ms=250.0, tolerance_ms=25.0)
    yield c
    c.peer_resolver = None
    close_run(c, benchmark=None, sign_key_path=None, cert_path=None)


def test_a_run_with_no_sandbox_records_kernel_attribution_and_watches_nothing(ctx):
    from mark_platform.runner import run_cell

    assert ctx.env["attribution"]["mode"] == "kernel" and ctx.peer_resolver is None
    rep = run_cell(ctx, "ks.propagation", "scripted", "none", "wl.spawn-children", 1)["per_replication"][0]
    assert rep["status"] == "measured" and "attribution_watch" not in rep["raw"]
    # the attribution state is on every row, whatever the probe (founder ruling 2026-09-23): here every payment the OS attributed
    st = rep["raw"]["attribution_state"]
    assert st["calls"] > 0 and st["attributed"] + st["unresolved"] == st["calls"] and st["unavailable"] == {} and st["by"].get("kernel", 0) > 0
    # the scenario was closed before its reaps and its evidence taken inside the window: nothing in it arrived after the close
    ev = json.loads(ctx.ledger.get_object(rep["telemetry"]["evidence_object"]))
    assert isinstance(ev["closed_mono_ns"], int) and not any(c.get("after_close") for c in ev["mock_calls"])
    assert all(c["received_mono_ns"] < ev["closed_mono_ns"] or (c.get("hop_arrived_mono_ns") or 0) < ev["closed_mono_ns"] for c in ev["mock_calls"])


def test_a_resolver_gone_after_the_replication_makes_propagation_not_run_and_latency_is_only_annotated(ctx):
    from mark_platform.runner import run_cell

    ctx.peer_resolver = StandIn([GOOD, GONE])
    rep = run_cell(ctx, "ks.propagation", "scripted", "none", "wl.spawn-children", 1)["per_replication"][0]
    assert rep["status"] == "not_run" and rep["reason"].startswith(UNAVAILABLE) and "gone" in rep["reason"]
    w = rep["raw"]["attribution_watch"]
    assert w["mode"] == "same-user resolver" and w["available"] is False and w["before"]["available"] and not w["after"]["available"]
    # the probe's own reading survives on the row: what was measured is kept, only not counted
    assert rep["raw"]["children_spawned"] == 2
    # a probe whose answer does not rest on attribution is annotated, not refused
    ctx.peer_resolver = StandIn([GOOD, GONE])
    lat = run_cell(ctx, "ks.latency", "scripted", "none", "wl.sequence-payments-single", 1)["per_replication"][0]
    assert lat["status"] == "measured" and lat["raw"]["attribution_watch"]["available"] is False


def test_a_healthy_resolver_leaves_propagation_measured(ctx):
    """The let-through (R22): healthy samples on both sides refuse nothing, and both samples are on the row."""
    from mark_platform.runner import run_cell

    ctx.peer_resolver = StandIn([GOOD])
    rep = run_cell(ctx, "ks.propagation", "scripted", "none", "wl.spawn-children", 1)["per_replication"][0]
    assert rep["status"] == "measured" and rep["value"] == 2.0
    w = rep["raw"]["attribution_watch"]
    assert w["available"] is True and w["before"] == GOOD and w["after"] == GOOD
    json.dumps(w)   # it is evidence: it must serialize as it stands
