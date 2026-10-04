"""A child that starts its own session to outlive its parent is attributed or could-not-tell, never unrelated (founder ruling
2026-09-23). On the pod, for real: the laptop has no second user and no sessions to change (R23), so tests/../packages/platform/
tests/test_orphan_attribution.py can only inject the process table. Here the agent is launched through the real sandbox, as
the harness launches one (its own session), spawns a child, and exits; the child calls setsid() -- the standard way a background
program detaches -- and then calls the harness. Root must not write it off as not the agent's.

In its own file so that tests/env/test_attribution.py stays at exactly three tests: the practice runs require that file alone to
show three passes before anything else runs.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SANDBOX = ["bash", str(REPO / "packages" / "platform" / "pod" / "sandbox.sh"), "--"]

pytestmark = pytest.mark.skipif(not Path("/etc/platform").is_dir(), reason="pod only: the agents' user and its sessions are the pod's")

CHILD = "import os,socket,time; os.setsid(); time.sleep(1.0); s=socket.create_connection(('127.0.0.1',{port})); time.sleep(60)"
PARENT = ("import os,subprocess,sys; print(os.getpid(), flush=True); sys.stdin.readline(); "
          "subprocess.Popen([sys.executable, '-c', {child!r}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
          "os._exit(0)")


def test_a_child_that_detaches_after_its_parent_exits_is_attributed_or_could_not_tell_never_unrelated(agents_workdir):
    from mark_platform import process_identity as pi
    from mark_platform.peer_resolver import start_if_needed

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(60)
    port = srv.getsockname()[1]
    r, rec = start_if_needed(SANDBOX)
    pi.set_resolver(r)
    pi.set_agents_uid(rec.get("agents_uid"))
    agent = subprocess.Popen([*SANDBOX, sys.executable, "-c", PARENT.format(child=CHILD.format(port=port))], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, cwd=str(agents_workdir), start_new_session=True)   # as scenario.py launches an agent
    child_pid = None
    try:
        agent_pid = int(agent.stdout.readline())
        session = pi.registered_session(agent_pid)          # recorded at registration, while the agent is alive
        assert session is not None, "the agent has no session to record"
        agent.stdin.write("go\n")
        agent.stdin.flush()
        agent.wait(timeout=30)                               # the agent has exited: the child is an orphan, and detached
        conn, (_, cport) = srv.accept()
        pid, method = pi.owner_pid(cport, server_port=port)
        child_pid = pid
        out = pi.resolve(0, agent_pid=agent_pid, pid=pid, attribution=pi.attribution_of(pid, method), agent_session=session)
        # the rule: never resolved-and-unrelated
        assert not (out["resolved"] and out["descends_from_agent"] is False), out
        assert out["attribution_unavailable"] is True or out["descends_from_agent"] is True, out
        conn.close()
    finally:
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except OSError:
                pass
        pi.set_resolver(None)
        pi.set_agents_uid(None)
        if r is not None:
            r.stop()
        srv.close()
