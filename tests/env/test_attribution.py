"""Attribution where the harness and the agents run as different users (TESTING.md R23; founder ruling 2026-09-23).

**Why this file exists.** `process_identity.owner_pid` passed every test on the laptop, where the harness and the agent
are one user. On the pod the agents run as `runner` and the harness as root without CAP_SYS_PTRACE, and root cannot read
their fd links: attempt 4's first matrix run attributed none of 64 propagation replications. A check whose outcome
depends on how the users are set up is run where they differ -- here, before every run (A7 refuses a run whose
environment test failed).

What it asserts, in order:
  1. the run decides its attribution from the kernel's facts: the resolver when the agents' uid differs and root lacks
     CAP_SYS_PTRACE, the kernel otherwise; and the resolver, when started, runs as the agents' user;
  2. a socket a sandboxed process holds is attributed to that process -- and without the resolver, root alone cannot;
  3. through the runner, the scripted parent's two children are both survivors, every payment attributed; and once the
     resolver is killed, the next replication is `not_run: attribution_unavailable` -- never a measured zero.
"""
from __future__ import annotations

import os
import pwd
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SANDBOX = ["bash", str(REPO / "packages" / "platform" / "pod" / "sandbox.sh"), "--"]
LLM_URL = os.environ.get("MARK_LLM_URL", "http://127.0.0.1:8000/v1")
LLM_MODEL = os.environ.get("MARK_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct-AWQ")

pytestmark = pytest.mark.skipif(not Path("/etc/platform").is_dir(), reason="pod only: the users this tests are the pod's (runner and root)")


def test_the_run_decides_attribution_from_the_kernel_and_the_resolver_runs_as_the_agents_user(agents_workdir):
    from mark_platform.peer_resolver import has_cap_sys_ptrace, start_if_needed

    r, rec = start_if_needed(SANDBOX, log_path=agents_workdir / "resolver.log")
    try:
        expected = "kernel" if has_cap_sys_ptrace() else "same-user resolver"
        assert rec["mode"] == expected, rec
        assert (r is None) == (expected == "kernel"), rec
        if r is None:  # r20: a host granting CAP_SYS_PTRACE starts no resolver; the two asserts above pin which host this is
            pytest.skip("kernel attribution on this host: the harness reads the agents' fd links itself")
        # on this image the sandbox is Tier B: the resolver is the agents' user by construction, and it says the limit
        runner_uid = pwd.getpwnam("runner").pw_uid
        assert r.uid == runner_uid and rec["resolver_uid"] == runner_uid and rec["first_health"]["available"], rec
        assert "not hostile" in rec["limit"], rec
    finally:
        if r is not None:
            r.stop()


def test_a_sandboxed_processs_socket_is_attributed_to_it_and_root_alone_cannot(agents_workdir):
    from mark_platform import process_identity as pi
    from mark_platform.peer_resolver import METHOD, has_cap_sys_ptrace, process_uids, start_if_needed

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(60)
    port = srv.getsockname()[1]
    code = f"import os,socket,sys,time; s=socket.create_connection(('127.0.0.1',{port})); print(os.getpid(),flush=True); sys.stdin.read()"
    held = subprocess.Popen([*SANDBOX, sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=str(agents_workdir))
    r = None
    try:
        conn, (_, cport) = srv.accept()
        claimed = int(held.stdout.readline())
        runner_uid = pwd.getpwnam("runner").pw_uid
        assert process_uids(claimed) == (runner_uid, runner_uid), "the sandboxed process is not the agents' user"
        # root alone: on this pod it cannot read runner's fd links (the defect), on a host with CAP_SYS_PTRACE it can
        pi.set_resolver(None)
        alone, why = pi.owner_pid(cport, server_port=port)
        assert (alone == claimed) == has_cap_sys_ptrace(), (alone, why)
        # with the run's attribution in place, the socket is attributed to the process that holds it
        r, rec = start_if_needed(SANDBOX, log_path=agents_workdir / "resolver.log")
        pi.set_resolver(r)
        pid, method = pi.owner_pid(cport, server_port=port)
        assert pid == claimed, (pid, method, rec)
        assert method.startswith(METHOD) == (rec["mode"] == "same-user resolver"), method
        conn.close()
    finally:
        pi.set_resolver(None)
        if r is not None:
            r.stop()
        held.kill()
        held.wait()
        srv.close()


def test_through_the_runner_children_are_survivors_and_a_killed_resolver_is_not_run(agents_workdir):
    """The run directory is under $MARK_RUNS, where every run lives, because the agents run as `runner` and must traverse
    into their scenario directory (run.sh makes /root and $MARK_RUNS mode 711 for exactly this). **The first pod run of
    this test, 2026-09-23, put it under pytest's tmp_path** -- /tmp/pytest-of-root, mode 700 -- and the sandboxed agent
    could not read its own workload.json (PermissionError), exited before its first call, and the test failed with no
    payments at all, before attribution was reached. The same class it was written to catch (R23): one user on the
    laptop, where every directory is readable, hid it."""
    import json

    from mark_platform.runner import calibrate, close_run, open_run, run_cell

    run_dir = agents_workdir / "run"   # under $MARK_RUNS, checked readable by the agents' user before any agent is launched
    ctx = open_run(run_dir, "envtest-attribution", llm_url=LLM_URL, llm_model=LLM_MODEL, tools_mode="inproc", sandbox_cmd=SANDBOX)
    try:
        assert ctx.env["attribution"]["mode"] in ("same-user resolver", "kernel"), ctx.env["attribution"]
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        rep = run_cell(ctx, "ks.propagation", "scripted", "none", "wl.spawn-children", 1)["per_replication"][0]
        ev = json.loads(ctx.ledger.get_object(rep["telemetry"]["evidence_object"]))
        pays = [c for c in ev["mock_calls"] if c["service"] == "payment"]
        unresolved = [c["os_process"].get("reason") for c in pays if not c["os_process"]["resolved"]]
        assert pays and not unresolved, unresolved[:3]
        assert rep["status"] == "measured" and rep["value"] == 2.0 and rep["raw"]["child_kinds"]["spawned"] == 2, rep
        if ctx.peer_resolver is None:  # r20: a host with CAP_SYS_PTRACE has no resolver to kill; the first test asserts which host this is
            pytest.skip("kernel attribution on this host: no resolver, so nothing to go quiet")
        # the resolver goes: "could not tell", never "no survivors"
        os.kill(ctx.peer_resolver.pid, signal.SIGKILL)
        rep2 = run_cell(ctx, "ks.propagation", "scripted", "none", "wl.spawn-children", 1)["per_replication"][0]
        assert rep2["status"] == "not_run" and rep2["reason"].startswith("attribution_unavailable"), rep2
        assert rep2["value"] is None
    finally:
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
