"""The same-user resolver (founder ruling 2026-09-23): what root checks of its claims, and what its silence means.

**Where each test can fail** (TESTING.md R23). The resolver exists because of how the pod's users are set up -- agents
as `runner`, the harness as root without CAP_SYS_PTRACE -- and no laptop is set up that way. So:

* the rules (which claims root accepts, which answers mean "could not tell") are tested here with the environment
  injected, on any machine, because they are logic;
* the routing inside `process_identity` reads /proc/net/tcp, so it runs on Linux, with the harness's uid injected;
* the cross-user path itself -- a resolver started through the real sandbox, as `runner`, answering for a real agent --
  is `tests/env/test_attribution.py`, which runs on every pod before any run (A7). That is the test that could have
  failed on attempt 4's first matrix run, and it is the one that counts.
"""
from __future__ import annotations

import socket
import sys

import pytest

from mark_platform import peer_resolver as pr
from mark_platform.peer_resolver import METHOD, UNAVAILABLE, SameUserResolver, verify_claim

AGENTS_UID, ROOT_UID = 1001, 0
RESOLVER_PID = 500


def _uids(table):
    return lambda pid: table.get(pid)


# ---- root's check of a claim ---------------------------------------------------------------------------------------

def test_root_accepts_a_claim_only_for_a_live_process_of_the_agents_user_that_is_not_the_resolver(monkeypatch):
    monkeypatch.setattr(pr, "process_uids", _uids({42: (AGENTS_UID, AGENTS_UID), 43: (ROOT_UID, ROOT_UID), RESOLVER_PID: (AGENTS_UID, AGENTS_UID)}))
    kw = {"socket_uid": AGENTS_UID, "resolver_pid": RESOLVER_PID, "resolver_uid": AGENTS_UID}
    # the case it must let through (R22): an agent-user process that exists, owning an agent-user socket
    assert verify_claim(42, **kw) is None
    assert "does not exist" in verify_claim(99, **kw)
    assert "runs as uid 0" in verify_claim(43, **kw)
    assert "named itself" in verify_claim(RESOLVER_PID, **kw)
    # a socket root's own table says belongs to someone else is refused whatever pid is named
    assert "belongs to uid 0" in verify_claim(42, **{**kw, "socket_uid": ROOT_UID})


# ---- what an answer means --------------------------------------------------------------------------------------------

def _resolver(monkeypatch, answer=None, raises=None):
    r = SameUserResolver([])
    r.pid, r.uid, r.sentinel_inode = RESOLVER_PID, AGENTS_UID, "777"

    def ask(req):
        if raises:
            raise raises
        return answer(req) if callable(answer) else answer

    monkeypatch.setattr(r, "_ask", ask)
    monkeypatch.setattr(pr, "process_uids", _uids({42: (AGENTS_UID, AGENTS_UID), RESOLVER_PID: (AGENTS_UID, AGENTS_UID)}))
    return r


def test_a_verified_answer_is_a_pid_with_the_resolver_named_as_the_method(monkeypatch):
    r = _resolver(monkeypatch, {"ok": True, "pid": 42})
    pid, method = r.resolve_inode("123", AGENTS_UID)
    assert pid == 42 and method.startswith(METHOD)


def test_a_resolver_that_does_not_answer_is_unavailable_never_no_owner(monkeypatch):
    r = _resolver(monkeypatch, raises=TimeoutError("timed out"))
    pid, why = r.resolve_inode("123", AGENTS_UID)
    assert pid is None and why.startswith(UNAVAILABLE) and "did not answer" in why


def test_no_owner_for_a_socket_still_established_is_unavailable_and_for_a_closed_one_is_not(monkeypatch):
    """The quiet lie: a resolver that answers "no owner" for every socket would otherwise turn every call into an
    unattributed one -- and a probe with some calls attributed and the rest not can read zero. An established socket is
    held by some process, so "no owner" from a working resolver is impossible, and the record says the resolver failed.
    The case that must NOT be flagged (R22): the caller's socket really did close in between -- a killed agent -- which
    is an ordinary unresolved call."""
    r = _resolver(monkeypatch, {"ok": True, "pid": None, "unreadable_fd_tables": 0})
    monkeypatch.setattr(pr, "socket_row", lambda inode: ("01", AGENTS_UID))
    pid, why = r.resolve_inode("123", AGENTS_UID)
    assert pid is None and why.startswith(UNAVAILABLE) and "still established" in why
    monkeypatch.setattr(pr, "socket_row", lambda inode: None)
    pid, why = r.resolve_inode("123", AGENTS_UID)
    assert pid is None and not why.startswith(UNAVAILABLE) and "closed before" in why


def test_a_claim_root_refuses_is_unresolved_and_is_not_mistaken_for_silence(monkeypatch):
    r = _resolver(monkeypatch, {"ok": True, "pid": RESOLVER_PID})
    pid, why = r.resolve_inode("123", AGENTS_UID)
    assert pid is None and why.startswith("resolver_claim_refused") and "named itself" in why


def test_health_is_available_only_when_the_process_is_there_and_names_its_own_socket(monkeypatch):
    ok = _resolver(monkeypatch, lambda req: {"ok": True, "pid": RESOLVER_PID})
    h = ok.health()
    assert h["available"] and h["alive"] and h["answering"] and h["known_answer"] and h["why"] is None
    wrong = _resolver(monkeypatch, lambda req: {"ok": True, "pid": 42})
    h = wrong.health()
    assert not h["available"] and h["why"].startswith(UNAVAILABLE) and "its own socket" in h["why"]
    silent = _resolver(monkeypatch, raises=OSError("connection refused"))
    h = silent.health()
    assert not h["available"] and h["why"].startswith(UNAVAILABLE) and "did not answer" in h["why"]
    gone = _resolver(monkeypatch, lambda req: {"ok": True, "pid": RESOLVER_PID})
    monkeypatch.setattr(pr, "process_uids", _uids({}))
    h = gone.health()
    assert not h["available"] and not h["alive"] and "gone" in h["why"]


# ---- when a resolver is started at all -------------------------------------------------------------------------------

def test_no_sandbox_means_the_kernel_and_no_resolver():
    r, rec = pr.start_if_needed([])
    assert r is None and rec["mode"] == "kernel" and rec["limit"] is None


# ---- the routing, on Linux ------------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc/net/tcp: the routing exists only on Linux (R23: the cross-user run is tests/env/test_attribution.py)")
def test_a_socket_owned_by_another_user_is_asked_of_the_resolver_and_ones_own_is_not(monkeypatch):
    from mark_platform import process_identity as pi

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    conn, (_, cport) = srv.accept()
    asked = []

    class Fake:
        def resolve_inode(self, inode, socket_uid):
            asked.append((inode, socket_uid))
            return 4242, f"{METHOD} (test)"

    try:
        monkeypatch.setattr(pi, "_RESOLVER", Fake())
        # this process's own socket: read directly, the resolver is not asked (the let-through, R22)
        pid, method = pi.owner_pid(cport, server_port=srv.getsockname()[1])
        assert pid == pi.os.getpid() and method == "/proc/net/tcp" and asked == []
        # the same socket, with the harness running as another uid: the socket is now another user's, so the resolver is asked
        monkeypatch.setattr(pi.os, "geteuid", lambda: pi.os.getuid() + 1)
        pid, method = pi.owner_pid(cport, server_port=srv.getsockname()[1])
        assert pid == 4242 and method.startswith(METHOD) and len(asked) == 1 and asked[0][1] == pi.os.getuid()
    finally:
        for s in (cli, conn, srv):
            s.close()
