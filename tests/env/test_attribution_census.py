"""The census, for real: a command run in a detached tmux terminal is placed by it (founder ruling 2026-09-23).

OpenHands as shipped runs every command from a tmux server, because tmux is on the image and the SDK picks it. A tmux server
detaches -- it forks, starts its own session, and its launcher exits -- so the OS no longer links anything it runs to the agent:
on the fourth practice attempt every OpenHands call was refused. Here an agent launched through the real sandbox, the way the
harness launches one, starts a tmux session and exits; a command inside tmux then calls the harness. The census -- the kernel's
record of when each process of the agents' user was started, against the window this scenario opened -- must place it; without
the census the same call must be could-not-tell. And the end-of-scenario reap must leave nothing of the agents' user but the
resolver, which is what keeps the census's one-at-a-time rule true for the next scenario.

In its own file so that tests/env/test_attribution.py stays at exactly three tests.
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SANDBOX = ["bash", str(REPO / "packages" / "platform" / "pod" / "sandbox.sh"), "--"]

pytestmark = pytest.mark.skipif(not Path("/etc/platform").is_dir(), reason="pod only: tmux, the agents' user and its sessions are the pod's")

INNER = "import socket,time; s=socket.create_connection(('127.0.0.1',{port})); time.sleep(60)\n"
AGENT = ("import os,subprocess,sys\n"
         "print(os.getpid(), flush=True); sys.stdin.readline()\n"
         "subprocess.run(['tmux', '-L', 'mark-census-test', 'new-session', '-d', sys.executable + ' ' + {inner!r}], check=True)\n"
         "os._exit(0)\n")


def test_a_command_run_in_a_detached_tmux_terminal_is_placed_by_the_census_and_the_reap_leaves_only_the_resolver(agents_workdir):
    from mark_platform import process_identity as pi
    from mark_platform.peer_resolver import start_if_needed

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(60)
    port = srv.getsockname()[1]
    inner = agents_workdir / "inner.py"
    inner.write_text(INNER.format(port=port), encoding="utf-8")
    agent_py = agents_workdir / "agent.py"
    agent_py.write_text(AGENT.format(inner=str(inner)), encoding="utf-8")
    r, rec = start_if_needed(SANDBOX, log_path=agents_workdir / "resolver.log")
    pi.set_resolver(r)
    pi.set_agents_uid(rec.get("agents_uid"))
    agent = subprocess.Popen([*SANDBOX, sys.executable, str(agent_py)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                             cwd=str(agents_workdir), start_new_session=True)       # as scenario.py launches an agent
    try:
        agent_pid = int(agent.stdout.readline())
        session = pi.registered_session(agent_pid)
        census = pi.census_open(session)                     # opened at registration, as the world does
        assert census is not None and census["leftovers"] == [], census
        agent.stdin.write("go\n")
        agent.stdin.flush()
        agent.wait(timeout=30)                               # the agent has exited; tmux runs on, detached
        conn, (_, cport) = srv.accept()
        pid, method = pi.owner_pid(cport, server_port=port)
        assert pid is not None, method
        att = pi.attribution_of(pid, method)
        out = pi.resolve(0, agent_pid=agent_pid, pid=pid, attribution=att, agent_session=session, census=census)
        assert out["resolved"] is True and out["descends_from_agent"] is True and out["attribution_unavailable"] is False, out
        assert out["descent_basis"].startswith("census:") and out["census"]["placed"] is True, out
        assert out["session"] != session, "tmux did not detach into its own session: this test would not be testing the census"
        # R10 on the real host: the same call with no census is could-not-tell, which is what stopped the fourth practice attempt
        old = pi.resolve(0, agent_pid=agent_pid, pid=pid, attribution=att, agent_session=session)
        assert old["attribution_unavailable"] is True, old
        conn.close()
    finally:
        reaped = pi.reap_agents_user()
        left = [q for q in pi.agents_user_pids() if q != getattr(r, "pid", None)]
        pi.set_resolver(None)
        pi.set_agents_uid(None)
        if r is not None:
            r.stop()
        srv.close()
    assert reaped["attempted"] is True and any("tmux" in (x.get("args") or "") for x in reaped["reaped"]), reaped
    assert left == [], f"processes of the agents' user outlived the reap: {left}"
