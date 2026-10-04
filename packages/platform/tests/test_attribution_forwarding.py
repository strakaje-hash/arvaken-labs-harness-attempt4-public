"""How a call's attribution travels from the first harness hop to the world's record (founder ruling 2026-09-23).

On attempt 4's first matrix run every propagation payment reached the world with no pid and the reason "forwarded by
an upstream harness hop" -- the world could not say more, because the egress proxy forwarded no reason. The real one
("socket inode N is not open in any process this user can read") sat in egress.jsonl. So the first hop now forwards how
it established the pid and, when it established none, why; and the world keeps "the resolver told us nothing" as a
flag the probe reads, distinct from an ordinary unresolved call.
"""
from __future__ import annotations

import os
import subprocess
import sys

from mark_platform import hop
from mark_platform.hop import ARRIVED_HEADER, ATTRIBUTION_HEADER, PEER_PID_HEADER, REASON_HEADER, receive
from mark_platform.peer_resolver import METHOD, UNAVAILABLE
from mark_platform.process_identity import attribution_of, resolve


def test_the_four_words_for_how_a_pid_was_established():
    assert attribution_of(42, "/proc/net/tcp") == "kernel" and attribution_of(42, "GetExtendedTcpTable") == "kernel"
    assert attribution_of(42, f"{METHOD} (1.2 ms)") == "resolver"
    assert attribution_of(None, f"{UNAVAILABLE}: the resolver did not answer") == "unavailable"
    assert attribution_of(None, "no established socket with local port 9") == "none"
    assert attribution_of(None, "resolver_claim_refused: pid 7 does not exist") == "none"


def test_the_first_hop_forwards_unavailable_with_its_reason_and_the_next_hop_trusts_only_the_harness(monkeypatch):
    silent = f"{UNAVAILABLE}: the resolver did not answer (TimeoutError)"
    # the first hop: its peer is an agent (not this process), and asking about it produced no pid
    monkeypatch.setattr(hop, "owner_pid", lambda port, server_port=None: (None, silent))
    out, facts = receive({}, client_port=5000, server_port=6000, hop="egress")
    assert facts["pid"] is None and facts["attribution"] == "unavailable" and facts["pid_source"] == silent
    assert out[ATTRIBUTION_HEADER] == "unavailable" and out[REASON_HEADER].startswith(UNAVAILABLE) and PEER_PID_HEADER not in out
    # the next hop: its peer is the harness, so what arrived is trusted and carried on, reason included
    monkeypatch.setattr(hop, "owner_pid", lambda port, server_port=None: (os.getpid(), "/proc/net/tcp"))
    _, facts2 = receive(out, client_port=5001, server_port=6001, hop="world")
    assert facts2["trusted_upstream"] and facts2["attribution"] == "unavailable" and facts2["pid"] is None and facts2["pid_source"].startswith(UNAVAILABLE)
    # the same headers from an agent's socket are the agent's word: dropped, and the hop resolves its own peer
    monkeypatch.setattr(hop, "owner_pid", lambda port, server_port=None: (777, "/proc/net/tcp"))
    _, facts3 = receive(out, client_port=5002, server_port=6002, hop="world")
    assert not facts3["trusted_upstream"] and facts3["dropped_supplied"] and facts3["pid"] == 777 and facts3["attribution"] == "kernel"


def test_a_resolver_claim_is_accepted_for_the_agent_and_refused_for_a_process_outside_it():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        me = os.getpid()
        # the case it must let through (R22): the claim names the agent itself
        ok = resolve(0, agent_pid=me, pid=me, attribution="resolver")
        assert ok["resolved"] and ok["pid"] == me and ok["is_agent"] is True and ok["attribution"] == "resolver"
        # a claim naming a process that is not the agent and not under it -- here the agent's PARENT -- is refused, and kept
        bad = resolve(0, agent_pid=child.pid, pid=me, attribution="resolver")
        assert bad["resolved"] is False and bad["pid"] is None and bad["reason"].startswith("resolver_claim_refused")
        # a refusal is could-not-tell, the same as silence (founder ruling 2026-09-23; until then this line asserted False)
        assert bad["resolver_claim"]["pid"] == me and bad["attribution_unavailable"] is True and bad["unavailable_kind"] == "refused"
        # with no agent registered, descent cannot be checked, so the claim is not accepted
        blind = resolve(0, agent_pid=None, pid=me, attribution="resolver")
        assert blind["resolved"] is False and "no agent pid is registered" in blind["reason"]
        # the kernel's own answer is not subject to this check: it is not a claim (the same pid, the same agent, attribution kernel)
        kernel = resolve(0, agent_pid=child.pid, pid=me, attribution="kernel")
        assert kernel["resolved"] is True and kernel["pid"] == me
    finally:
        child.kill()
        child.wait()


def test_the_world_records_could_not_tell_as_a_flag_with_the_first_hops_reason(tmp_path):
    import httpx

    from mark_platform.clock import mono_ns
    from mark_platform.scenario import MockWorld

    mock = MockWorld.start(tmp_path)
    try:
        sid = "s-att"
        mock.set_policy(sid, single_call_per_turn=False)
        silent = f"{UNAVAILABLE}: the resolver did not answer (TimeoutError)"
        # this process is the harness: its headers are what an upstream hop would have forwarded
        up = {"X-Scenario-Id": sid, ARRIVED_HEADER: str(mono_ns()), "X-Mark-Hop": "egress"}
        httpx.post(mock.url + "/payment/charge", json={"amount": 1, "reference": "A"}, headers={**up, ATTRIBUTION_HEADER: "unavailable", REASON_HEADER: silent}, timeout=5)
        httpx.post(mock.url + "/payment/charge", json={"amount": 1, "reference": "B"},
                   headers={**up, ATTRIBUTION_HEADER: "none", REASON_HEADER: "the caller's socket (inode 9) closed before its owner could be read"}, timeout=5)
        by_ref = {c["body"]["reference"]: c["os_process"] for c in mock.calls(sid)}
        a, b = by_ref["A"], by_ref["B"]
        assert a["resolved"] is False and a["attribution_unavailable"] is True and a["reason"] == silent
        # an ordinary unresolved call is NOT flagged as silence (R22): the probe treats the two differently
        assert b["resolved"] is False and b["attribution_unavailable"] is False and "closed before" in b["reason"]
    finally:
        mock.stop()


def test_a_call_that_reaches_the_harness_after_the_scenario_closed_is_after_close_by_its_receipt_not_its_recording(tmp_path):
    """Founder ruling 2026-09-23. The census run's first smoke stopped on a child's payment that reached the world after the window
    and after the evidence: closed before the reaps, such a call is `after_close`. What decides is when the call reached the harness
    (its receipt of record), so a call the first hop received before the close is inside the window even if it is recorded after."""
    import httpx

    from mark_platform.clock import mono_ns
    from mark_platform.scenario import MockWorld

    mock = MockWorld.start(tmp_path)
    try:
        sid = "s-close"
        mock.set_policy(sid, single_call_per_turn=False)
        before = mono_ns()
        httpx.post(mock.url + "/payment/charge", json={"amount": 1, "reference": "IN"}, headers={"X-Scenario-Id": sid}, timeout=5)
        mock.close_scenario(sid)
        httpx.post(mock.url + "/payment/charge", json={"amount": 1, "reference": "LATE"}, headers={"X-Scenario-Id": sid}, timeout=5)
        # forwarded by an upstream hop that received it before the close (this process is the harness, so its hop headers are trusted)
        httpx.post(mock.url + "/payment/charge", json={"amount": 1, "reference": "EARLY-HOP"},
                   headers={"X-Scenario-Id": sid, ARRIVED_HEADER: str(before), "X-Mark-Hop": "egress"}, timeout=5)
        by_ref = {c["body"]["reference"]: c for c in mock.calls(sid)}
        assert by_ref["IN"]["after_close"] is False and by_ref["LATE"]["after_close"] is True
        assert by_ref["EARLY-HOP"]["after_close"] is False
        assert mock.close_scenario(sid) == mock.log.closed_at[sid]          # idempotent: the first close stands
    finally:
        mock.stop()
