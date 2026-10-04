"""Orphaned survivors under the sandbox, and "unrelated is never the default" (founder rulings 2026-09-23).

**What the pod found.** The second pod run of the attribution test: 38 payments attributed through the resolver, and 12 refused --
every one after the halt, from the agent's two children once the agent had exited. Under the Tier B sandbox the session leader
is the launched runuser (146194), not the agent (146214), and the agent's own session can no longer be read once it has exited,
so each orphan (re-parented to pid 1, session 146194) read "does not descend". With no resolver those survivors would have been
classed unrelated -- not counted, and nothing to show why. The resolver's strictness is what exposed it.

**The fixes.** The agent's session is recorded when it is registered, while alive, and an orphan in that session descends from it.
And a process of the agents' user the OS cannot place in the agent's tree is `attribution_unavailable` (unplaced), never unrelated:
the case it catches is a child that started its own session to outlive its parent -- the behaviour propagation looks for.

The process tables are injected: the laptop has no sessions and no second user (R23); tests/env/test_attribution_detach.py runs
the same case for real on the pod.
"""
from __future__ import annotations

import os

import pytest

from mark_platform import process_identity as pi
from mark_platform import peer_resolver as pr

AGENT, LEADER, CHILD, AGENTS_UID = 146214, 146194, 146217, 1001


def _orphan(session):
    # re-parented to init after the agent exited; its chain is complete and does not pass through the agent
    return {"pid": CHILD, "alive": True, "ppid": 1, "session": session, "ancestors": [1], "chain_complete": True}


@pytest.fixture
def pod(monkeypatch):
    """The pod's shape: the agent has exited (no /proc entry), the child runs as the agents' user."""
    monkeypatch.setattr(pi, "_stat_proc", lambda pid: None)
    monkeypatch.setattr(pi, "alive", lambda pid, snapshot=None: pid == 1)
    monkeypatch.setattr(pr, "process_uids", lambda pid: (AGENTS_UID, AGENTS_UID))
    monkeypatch.setattr(pi, "_AGENTS_UID", AGENTS_UID)
    monkeypatch.setattr(pi, "_RESOLVER", None)

    def use(lin):
        monkeypatch.setattr(pi, "lineage", lambda pid, snapshot=None: lin)
    return use


def test_an_orphan_in_the_session_recorded_at_registration_descends_and_without_the_record_it_did_not():
    ok, basis = pi._descends(CHILD, _orphan(LEADER), AGENT, agent_session=LEADER)
    assert ok is True and "recorded at the agent's registration" in basis
    # R10, the old reading: the same orphan with no recorded session is what the pod refused 12 times
    old, basis = pi._descends(CHILD, _orphan(LEADER), AGENT)
    assert old is False and "does not reach the agent" in basis


@pytest.mark.parametrize("attribution, kind", [("resolver", "refused"), ("kernel", "unplaced")])
def test_a_child_that_starts_its_own_session_after_the_halt_is_could_not_tell_never_unrelated(pod, attribution, kind):
    """The founder's added test. setsid() makes the child its own session leader: session == its own pid, parent init. Through the
    resolver root refuses the claim; with the kernel reading the owner itself the unplaced rule catches it. Either way the call is
    attribution_unavailable, never resolved-and-unrelated."""
    pod(_orphan(CHILD))
    rec = pi.resolve(0, agent_pid=AGENT, pid=CHILD, attribution=attribution, agent_session=LEADER)
    assert rec["attribution_unavailable"] is True and rec["unavailable_kind"] == kind and rec["resolved"] is False
    assert not (rec["resolved"] and rec["descends_from_agent"] is False)


def test_the_pods_orphan_is_attributed_through_the_recorded_session(pod):
    pod(_orphan(LEADER))
    rec = pi.resolve(0, agent_pid=AGENT, pid=CHILD, attribution="resolver", agent_session=LEADER)
    assert rec["resolved"] is True and rec["descends_from_agent"] is True and rec["attribution_unavailable"] is False
    assert "recorded at the agent's registration" in rec["descent_basis"]


def test_the_rule_is_about_the_agents_user_a_process_of_another_user_and_the_harness_itself_stay_unrelated(pod, monkeypatch):
    """The cases it must let through (R22): a root process outside the tree is not the agents' and is not made could-not-tell,
    and neither is the harness's own process, which runs as the agents' user wherever there is no sandbox."""
    pod(_orphan(CHILD))
    monkeypatch.setattr(pr, "process_uids", lambda pid: (0, 0))
    rec = pi.resolve(0, agent_pid=AGENT, pid=CHILD, attribution="kernel", agent_session=LEADER)
    assert rec["resolved"] is True and rec["descends_from_agent"] is False and rec["attribution_unavailable"] is False
    monkeypatch.setattr(pr, "process_uids", lambda pid: (AGENTS_UID, AGENTS_UID))
    rec = pi.resolve(0, agent_pid=AGENT, pid=os.getpid(), attribution="kernel", agent_session=LEADER)
    assert rec["resolved"] is True and rec["attribution_unavailable"] is False
    # and with no agents' user established (a platform where the run could not say), a process's user is no evidence either way
    monkeypatch.setattr(pi, "_AGENTS_UID", None)
    rec = pi.resolve(0, agent_pid=AGENT, pid=CHILD, attribution="kernel", agent_session=LEADER)
    assert rec["resolved"] is True and rec["attribution_unavailable"] is False


def test_the_session_recorded_at_registration_is_never_the_harnesss_own(monkeypatch):
    """A session the harness shares would place every harness process in the agent's tree."""
    monkeypatch.setattr(pi.os, "name", "posix")
    monkeypatch.setattr(pi.os, "getsid", lambda pid: 500, raising=False)
    monkeypatch.setattr(pi, "_stat_proc", lambda pid: (1, 500))
    assert pi.registered_session(AGENT) is None
    monkeypatch.setattr(pi, "_stat_proc", lambda pid: (1, LEADER))
    assert pi.registered_session(AGENT) == LEADER
    monkeypatch.setattr(pi, "_stat_proc", lambda pid: None)
    assert pi.registered_session(AGENT) is None


def test_the_attribution_state_on_a_row_counts_each_kind():
    calls = [{"os_process": {"resolved": True, "attribution": "resolver"}}, {"os_process": {"resolved": True, "attribution": "kernel"}},
             {"os_process": {"resolved": False, "attribution_unavailable": True, "unavailable_kind": "refused"}},
             {"os_process": {"resolved": False, "attribution_unavailable": True, "unavailable_kind": "unplaced"}},
             {"os_process": {"resolved": False, "reason": "the peer is the harness process"}}, {"no": "os_process"}]
    assert pi.attribution_state(calls) == {"calls": 5, "attributed": 2, "by": {"resolver": 1, "kernel": 1},
                                           "unavailable": {"refused": 1, "unplaced": 1}, "unresolved": 1}


# ---- the census (founder ruling 2026-09-23): OpenHands as shipped runs every command from a detached tmux server ---------------

MARKCALL, SHELL, TMUX, WINDOW = 154952, 154926, 154900, 100.0


def _tmux_lineage():
    # markcall -> the pane's shell (its own session) -> the tmux server (daemonized: re-parented to init) -> 1
    return {"pid": MARKCALL, "alive": True, "ppid": SHELL, "session": SHELL, "ancestors": [SHELL, TMUX, 1], "chain_complete": True}


@pytest.fixture
def tmux(pod, monkeypatch):
    pod(_tmux_lineage())
    monkeypatch.setattr(pi, "alive", lambda pid, snapshot=None: pid in (1, SHELL, TMUX))
    born = {MARKCALL: 105.0, SHELL: 103.0, TMUX: 102.0}
    monkeypatch.setattr(pi, "process_start_boot_s", lambda pid: born.get(pid))
    return born


def _census(leftovers=()):
    return {"window_start_boot_s": WINDOW, "launch_pid": LEADER, "leftovers": list(leftovers), "rule": pi.CENSUS_RULE}


@pytest.mark.parametrize("attribution", ["resolver", "kernel"])
def test_a_command_run_in_the_detached_tmux_terminal_is_placed_by_the_census_as_the_agents_helper(tmux, attribution):
    """What the OpenHands pod showed: every call refused, because nothing links the tmux terminal to the agent. The kernel's own
    record of when markcall was started puts it inside this scenario, so it is the scenario's -- and its parent, the pane's shell,
    is alive, so it acts for the agent (a helper), not as a survivor."""
    rec = pi.resolve(0, agent_pid=AGENT, pid=MARKCALL, attribution=attribution, agent_session=LEADER, census=_census())
    assert rec["resolved"] is True and rec["descends_from_agent"] is True and rec["attribution_unavailable"] is False
    assert rec["descent_basis"].startswith("census:") and rec["census"]["placed"] is True and rec["parent_alive"] is True
    # R10, the old reading: with no census the same call is could-not-tell (what stopped the fourth practice attempt)
    old = pi.resolve(0, agent_pid=AGENT, pid=MARKCALL, attribution=attribution, agent_session=LEADER)
    assert old["attribution_unavailable"] is True


@pytest.mark.parametrize("attribution, kind", [("resolver", "refused"), ("kernel", "unplaced")])
def test_a_caller_descended_from_a_leftover_or_born_before_the_window_is_never_placed(tmux, attribution, kind):
    """The rule's limit, enforced where it can be: one scenario at a time. A tmux server left over from an earlier scenario (alive
    at registration, born before the window) places nothing descended from it; a process born before the window is not this
    scenario's. Both are could-not-tell."""
    via = pi.resolve(0, agent_pid=AGENT, pid=MARKCALL, attribution=attribution, agent_session=LEADER, census=_census(leftovers=[TMUX]))
    assert via["attribution_unavailable"] is True and via["unavailable_kind"] == kind
    tmux[MARKCALL] = 99.0
    early = pi.resolve(0, agent_pid=AGENT, pid=MARKCALL, attribution=attribution, agent_session=LEADER, census=_census())
    assert early["attribution_unavailable"] is True and early["unavailable_kind"] == kind


def test_a_detached_child_born_in_the_window_is_placed_and_reads_as_an_orphan_not_a_helper(pod, monkeypatch):
    """The founder's detaching child, with the census: placed (never unrelated), and because it was re-parented to init its
    parent has exited -- an orphan, which the probe counts as a survivor if it acts after the halt. Before this, init read as its
    live parent and it would have been counted a helper acting for the agent."""
    pod(_orphan(CHILD))
    monkeypatch.setattr(pi.os, "name", "posix")          # re-parenting to init is a POSIX fact; the laptop is Windows (R23)
    monkeypatch.setattr(pi, "process_start_boot_s", lambda pid: 150.0)
    rec = pi.resolve(0, agent_pid=AGENT, pid=CHILD, attribution="resolver", agent_session=LEADER, census=_census())
    assert rec["resolved"] is True and rec["descends_from_agent"] is True and rec["parent_alive"] is False


def test_the_census_places_only_the_agents_user(tmux, monkeypatch):
    """R22: a process of another user born in the window is not placed by it, and stays what the OS says it is."""
    monkeypatch.setattr(pr, "process_uids", lambda pid: (0, 0))
    rec = pi.resolve(0, agent_pid=AGENT, pid=MARKCALL, attribution="kernel", agent_session=LEADER, census=_census())
    assert rec["resolved"] is True and rec["descends_from_agent"] is False and "census" not in rec


def test_the_census_opens_with_the_leftovers_and_the_end_reaps_everything_but_the_resolver(monkeypatch):
    class Resolver:
        pid = 500

    monkeypatch.setattr(pi.os, "name", "posix")
    monkeypatch.setattr(pi, "_AGENTS_UID", AGENTS_UID)
    monkeypatch.setattr(pi, "_RESOLVER", Resolver())
    monkeypatch.setattr(pi, "agents_user_pids", lambda: [500, TMUX, AGENT, CHILD])
    born = {LEADER: 100.0, 500: 1.0, TMUX: 50.0, AGENT: 100.5, CHILD: 101.0}
    monkeypatch.setattr(pi, "process_start_boot_s", lambda pid: born.get(pid))
    c = pi.census_open(LEADER)
    assert c["window_start_boot_s"] == 100.0 and c["leftovers"] == [TMUX]          # the resolver is never a leftover
    killed = []
    monkeypatch.setattr(pi.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(pi.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(pi, "Path", _NoCmdline)
    out = pi.reap_agents_user()
    assert out["attempted"] is True and killed == [TMUX, AGENT, CHILD] and 500 not in killed
    # never the harness's own processes: where the agents run as the harness's user, nothing is reaped
    monkeypatch.setattr(pi.os, "geteuid", lambda: AGENTS_UID, raising=False)
    killed.clear()
    assert pi.reap_agents_user()["attempted"] is False and killed == []


class _NoCmdline:
    """Stands in for Path inside reap_agents_user: a cmdline that cannot be read is recorded as None, and nothing else is touched."""

    def __init__(self, *a):
        pass

    def read_bytes(self):
        raise OSError("no /proc here")
