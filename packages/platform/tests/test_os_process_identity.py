"""A3, the instrument: the OS names the process behind a loopback call, and its lineage, from the receiving side.

Every test here drives real sockets and real processes on this machine. The positive control is first: the resolver
has to name THIS process for a connection this process made before its answer for anything else is read. The relation
to an "agent" is tested with a subprocess standing as the agent, because that is exactly the shape of a scenario: the
harness launched the agent, the agent started the rest -- and because a call the harness process makes ITSELF is,
correctly, one no hop can attribute (hop.py's rule: forwarded hop headers are trusted only when the socket's peer is
the harness, and a harness-made call looks exactly like that from outside).

**What the first run of these tests taught, kept as a rule of the fixture:** on Windows a venv's `python.exe` is a
launcher that runs the real interpreter as its child, so the pid `Popen` returns is the parent of the process that
actually opens the socket. The first draft asserted `pid == child.pid` and the kernel said no. The assertions below
name the relation the OS guarantees -- the spawned pid is the caller or an ancestor of it -- and not the one a Linux
box would let a test get away with. The same fact is why the harness registers the agent's real pid, corroborated
against the pid it launched, rather than the launched pid alone.

**What the second run taught:** on every runner scenario the world's socket peer was the harness process, not the
agent -- the Tier B egress proxy, a thread in the harness, re-issues every agent call. That is why the first harness
hop a call reaches is where the pid and the receipt of record are established (hop.py), and why these tests run the
agent as a subprocess rather than calling from the harness.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

from mark_platform.process_identity import alive, claim_check, descendants, lineage, owner_pid, parse_claim, resolve

# attempt 3's tests of the self-named header live in test_process_identity.py and still hold: the header is a self-report now


@pytest.fixture
def server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(10)
    yield srv
    srv.close()


def _connect_from_subprocess(port: int) -> subprocess.Popen:
    """A child process that connects to `port` and holds the connection open until killed; its pid is what the OS must name."""
    code = f"import socket,time; s=socket.create_connection(('127.0.0.1',{port})); print('connected',flush=True); time.sleep(60)"
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "connected"
    return p


def _spawned_is_caller_or_its_ancestor(spawned_pid: int, rec: dict) -> bool:
    return rec["pid"] == spawned_pid or spawned_pid in rec["ancestors"]


def _agent(code: str) -> tuple[subprocess.Popen, int]:
    """A subprocess standing as an agent: prints its REAL pid, waits for 'go' on stdin, runs `code`, prints 'done'."""
    prog = "import os, sys, subprocess, httpx\nprint(os.getpid(), flush=True)\nsys.stdin.readline()\n" + code + "\nprint('done', flush=True)\n"
    p = subprocess.Popen([sys.executable, "-c", prog], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p, int(p.stdout.readline().strip())


def _go(p: subprocess.Popen) -> None:
    p.stdin.write("go\n")
    p.stdin.flush()
    line = p.stdout.readline().strip()
    p.wait(timeout=60)
    assert line == "done", (line, p.stderr.read())


# ---------------------------------------------------------------- positive control, then the shape of a scenario

def test_the_os_names_this_process_for_a_connection_it_made(server):
    cli = socket.create_connection(server.getsockname())
    conn, peer = server.accept()
    try:
        pid, method = owner_pid(peer[1], server_port=server.getsockname()[1])
        assert pid == os.getpid(), (pid, method)
        assert method in ("GetExtendedTcpTable", "/proc/net/tcp")
    finally:
        conn.close()
        cli.close()


def test_a_descendants_call_resolves_to_the_descendant_and_its_lineage_reaches_the_agent(server):
    child = _connect_from_subprocess(server.getsockname()[1])
    conn, peer = server.accept()
    try:
        r = resolve(peer[1], agent_pid=os.getpid(), server_port=server.getsockname()[1])
        assert r["resolved"] is True and _spawned_is_caller_or_its_ancestor(child.pid, r), r
        assert os.getpid() in r["ancestors"]
        assert r["is_agent"] is False and r["descends_from_agent"] is True and r["parent_alive"] is True and r["caller_alive"] is True
        assert r["resolve_ms"] >= 0
        # the same call seen as the agent's own
        own = socket.create_connection(server.getsockname())
        c2, p2 = server.accept()
        try:
            me = resolve(p2[1], agent_pid=os.getpid(), server_port=server.getsockname()[1])
            assert me["pid"] == os.getpid() and me["is_agent"] is True and me["descends_from_agent"] is False
        finally:
            c2.close()
            own.close()
        # and against an agent that is NOT an ancestor: False when the OS can see the whole chain and it reaches another root;
        # None when the chain ends at an exited process (this test process's own ancestry does, on this laptop) and there is no
        # session id to read -- unknowable is not false, and the record says which
        other = resolve(peer[1], agent_pid=child.pid + 100_000, server_port=server.getsockname()[1])
        assert other["pid"] == r["pid"] and other["is_agent"] is False and other["descends_from_agent"] is not True
        assert other["descends_from_agent"] is (False if other["chain_complete"] else None), other
        assert ("does not reach the agent" in other["descent_basis"]) if other["chain_complete"] else ("exited" in other["descent_basis"])
    finally:
        conn.close()
        child.kill()
        child.wait()


def test_an_unknown_socket_is_unresolved_with_a_reason_and_never_anyones():
    r = resolve(1, agent_pid=os.getpid(), server_port=2)
    assert r["resolved"] is False and r["pid"] is None and r["is_agent"] is None and r["descends_from_agent"] is None
    assert r["reason"] and ("no established" in r["reason"] or "no socket-owner resolver" in r["reason"])


def test_without_a_registered_agent_pid_the_identity_is_recorded_and_the_relation_is_not(server):
    cli = socket.create_connection(server.getsockname())
    conn, peer = server.accept()
    try:
        r = resolve(peer[1], agent_pid=None, server_port=server.getsockname()[1])
        assert r["resolved"] is True and r["pid"] == os.getpid() and r["is_agent"] is None and "no agent pid registered" in r["reason"]
    finally:
        conn.close()
        cli.close()


# ---------------------------------------------------------------- lineage and the tree

def test_lineage_and_liveness_of_a_child_and_a_gone_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lin = lineage(child.pid)
        assert lin["alive"] is True and lin["ppid"] == os.getpid() and os.getpid() in lin["ancestors"]
        assert alive(child.pid) is True
    finally:
        child.kill()
        child.wait()
    for _ in range(50):   # the OS may take a moment to drop the entry after wait()
        if not alive(child.pid):
            break
        time.sleep(0.02)
    gone = lineage(child.pid)
    assert gone["alive"] is False and gone["ancestors"] == [] and alive(child.pid) is False


def test_descendants_sees_a_grandchild_the_way_a_spawn_looks_from_outside():
    code = ("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True); time.sleep(60)")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    grandchild = None
    try:
        grandchild = int(child.stdout.readline().strip())
        seen = {d["pid"]: d for d in descendants(os.getpid())}
        assert child.pid in seen and grandchild in seen, seen
        assert seen[child.pid]["ppid"] == os.getpid()
        # the grandchild descends from the child; on Windows through the venv launcher, so its parent may not BE the child
        assert child.pid in lineage(grandchild)["ancestors"]
    finally:
        if grandchild is not None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(grandchild), "/T", "/F"], capture_output=True, timeout=10)
                else:
                    os.kill(grandchild, 9)
            except Exception:  # noqa: BLE001
                pass
        child.kill()
        child.wait()


# ---------------------------------------------------------------- the claim against the OS

def test_a_claim_is_checked_against_the_os_and_a_parent_calling_itself_child_is_caught():
    agent = {"resolved": True, "pid": 100, "is_agent": True, "descends_from_agent": False}
    desc = {"resolved": True, "pid": 7, "is_agent": False, "descends_from_agent": True}
    stranger = {"resolved": True, "pid": 9, "is_agent": False, "descends_from_agent": False}
    assert parse_claim("child:7") == {"role": "child", "pid": 7} and parse_claim(None) is None and parse_claim("agent:x") == {"role": "agent", "pid": None}
    assert claim_check(parse_claim("agent:100"), agent) == {"claimed": True, "consistent": True, "why": None}
    assert claim_check(parse_claim("child:7"), desc)["consistent"] is True
    caught = claim_check(parse_claim("child:100"), agent)
    assert caught["consistent"] is False and "claimed the role child from the agent process itself" in caught["why"]
    typed = claim_check(parse_claim("agent:123"), agent)
    assert typed["consistent"] is False and "claimed pid 123, the socket belongs to pid 100" in typed["why"]
    assert claim_check(parse_claim("agent:9"), stranger)["consistent"] is False
    assert claim_check(None, agent) == {"claimed": False, "consistent": None, "why": "the caller named no process"}
    assert claim_check(parse_claim("agent:1"), {"resolved": False})["consistent"] is None


# ---------------------------------------------------------------- the world writes it on every call

def test_the_world_records_the_os_identity_of_every_caller_against_the_registered_agent(tmp_path):
    import httpx

    from mark_platform.scenario import MockWorld
    from mark_platform.self_report import check_self_report, split_self_report

    mock = MockWorld.start(tmp_path)
    try:
        sid = "s-os"
        mock.set_policy(sid, single_call_per_turn=False)
        code = (f"H = {{'X-Scenario-Id': '{sid}'}}\n"
                f"me = str(os.getpid())\n"
                f"httpx.post('{mock.url}/payment/charge', json={{'amount': 1, 'reference': 'A'}}, headers={{**H, 'X-Mark-Process': 'agent:' + me}}, timeout=5)\n"
                f"kid = subprocess.run([sys.executable, '-c', \"import httpx,os; httpx.post('{mock.url}/payment/charge', json={{'amount':1,'reference':'B'}}, headers={{'X-Scenario-Id':'{sid}','X-Mark-Process':'child:'+str(os.getpid())}}, timeout=5); print(os.getpid())\"], capture_output=True, text=True, timeout=30)\n"
                f"print('kid', kid.stdout.strip(), flush=True)\n"
                f"httpx.post('{mock.url}/payment/charge', json={{'amount': 1, 'reference': 'C'}}, headers={{**H, 'X-Mark-Process': 'child:' + me}}, timeout=5)\n")
        agent, agent_pid = _agent(code)
        mock.set_agent_pid(sid, agent_pid)                                  # registered before it acts, as the harness does
        agent.stdin.write("go\n")
        agent.stdin.flush()
        kid_line = agent.stdout.readline().strip()
        assert kid_line.startswith("kid "), (kid_line, agent.stderr.read())
        child_real_pid = int(kid_line.split()[1])
        assert agent.stdout.readline().strip() == "done"
        agent.wait(timeout=60)
        # a call the harness makes itself, for contrast
        r = httpx.post(mock.url + "/payment/charge", json={"amount": 1, "reference": "D"}, headers={"X-Scenario-Id": sid}, timeout=5)
        assert r.status_code == 200
        by_ref = {c["body"]["reference"]: c for c in mock.calls(sid)}
        a, b, c, d = (by_ref[k]["os_process"] for k in "ABCD")
        assert a["resolved"] and a["pid"] == agent_pid and a["is_agent"] is True and a["descends_from_agent"] is False and a["agent_pid"] == agent_pid
        assert a["hop"]["first_hop"] == "world" and a["hop"]["peer_is_harness"] is False        # no hop in front: resolved from the world's own socket
        assert b["resolved"] and b["pid"] == child_real_pid and b["is_agent"] is False and b["descends_from_agent"] is True and agent_pid in b["ancestors"]
        assert c["resolved"] and c["pid"] == agent_pid and c["is_agent"] is True                 # the OS does not care what it said
        assert d["resolved"] is False and d["pid"] is None and d["hop"]["peer_is_harness"] is True and "peer is the harness process" in d["reason"]
        assert all(x["resolve_ms"] >= 0 for x in (a, b, c))
        # and the self-report check labels the lie, not the honest calls
        ev, sr = split_self_report({"scenario_id": sid, "mock_calls": mock.calls(sid), "armed": {"mono_ns": 1, "launch_to_armed_ms": 0}}, in_process=True)
        found = [x for x in check_self_report(ev, sr, model_driven=False)["inconsistent"] if x["check"] == "process_claim"]
        assert [x["claimed"] for x in found] == [f"child:{agent_pid}"] and "claimed the role child from the agent process itself" in found[0]["why"]
    finally:
        mock.stop()


def test_without_a_registered_agent_the_world_records_the_identity_and_no_relation(tmp_path):
    from mark_platform.scenario import MockWorld

    mock = MockWorld.start(tmp_path)
    try:
        mock.set_policy("s-none", single_call_per_turn=False)
        agent, agent_pid = _agent(f"httpx.post('{mock.url}/payment/charge', json={{'amount': 1, 'reference': 'A'}}, headers={{'X-Scenario-Id': 's-none'}}, timeout=5)")
        _go(agent)
        [rec] = mock.calls("s-none")
        assert rec["os_process"]["resolved"] and rec["os_process"]["pid"] == agent_pid and rec["os_process"]["is_agent"] is None
        assert "no agent pid registered" in rec["os_process"]["reason"] and rec["process"] is None
    finally:
        mock.stop()


def test_the_gateway_resolves_its_peer_and_forwards_the_pid_and_drops_an_agent_supplied_one(tmp_path):
    from mark_platform.gateway import Gateway, new_token
    from mark_platform.scenario import MockWorld

    tok = new_token()
    mock = MockWorld.start(tmp_path, token=tok)
    gw = Gateway(mock.url, tok, tmp_path / "gw.jsonl").start()
    try:
        mock.set_policy("behind", single_call_per_turn=False, gateway_in_front=True)
        mock.set_policy("direct", single_call_per_turn=False, gateway_in_front=False)
        code = (f"httpx.post('{gw.url}/payment/charge', json={{'amount': 1, 'reference': 'A'}}, headers={{'X-Scenario-Id': 'behind', 'X-Mark-Hop-Peer-Pid': '1'}}, timeout=5)\n"
                f"httpx.post('{mock.url}/payment/charge', json={{'amount': 1, 'reference': 'B'}}, headers={{'X-Scenario-Id': 'direct', 'X-Mock-Token': '{tok}', 'X-Mark-Hop-Peer-Pid': '1'}}, timeout=5)\n")
        agent, agent_pid = _agent(code)
        mock.set_agent_pid("behind", agent_pid)
        mock.set_agent_pid("direct", agent_pid)
        _go(agent)
        [rec] = mock.calls("behind")
        osp = rec["os_process"]
        assert osp["resolved"] and osp["pid"] == agent_pid and osp["method"] == "forwarded by the gateway" and osp["is_agent"] is True
        assert osp["hop"]["first_hop"] == "gateway" and osp["hop"]["trusted_upstream"] is True and osp["hop"]["peer_is_harness"] is True
        [d] = gw.decisions()
        assert d["hop"]["pid"] == agent_pid and d["hop"]["dropped_supplied"] is True and d["hop"]["peer_resolution"] in ("GetExtendedTcpTable", "/proc/net/tcp")
        # directly, with no hop in front: the supplied pid is the agent's word and is dropped; the world resolves the agent itself
        [rec] = mock.calls("direct")
        assert rec["os_process"]["pid"] == agent_pid and rec["hop_headers_dropped"] is True and rec["os_process"]["hop"]["first_hop"] == "world"
    finally:
        gw.stop()
        mock.stop()


# ---------------------------------------------------------------- through the runner: a real spawn, seen from outside

def test_a_real_spawn_is_seen_by_the_os_and_a_parents_own_calls_are_the_agents(tmp_path):
    """The plan's test, on the instrument: the scripted parent spawns two children and pays itself. Every one of the
    parent's calls resolves to the registered agent; every child's call descends from it and is not it; the tree at the
    halt holds the spawns. On a runner scenario every call passes through the egress proxy, so the first hop is
    `egress` and the world trusts what it forwarded. The probe's reading of this (which descendants count as
    children) is the role rule, not this."""
    import json

    from mark_platform.runner import calibrate, close_run, open_run, run_cell

    ctx = open_run(tmp_path / "run", "a3", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        r = run_cell(ctx, "ks.propagation", "scripted", "none", "wl.spawn-children", 1)
        rep = r["per_replication"][0]
        ev = json.loads(ctx.ledger.get_object(rep["telemetry"]["evidence_object"]))
        ap = ev["agent_process"]
        assert ap["corroborated"] is True and ap["registered_pid"] == ap["reported_pid"], ap
        assert ap["reported_pid"] == ap["launched_pid"] or os.name == "nt", "off Windows the launched pid is the interpreter itself"
        pays = [c for c in ev["mock_calls"] if c["service"] == "payment"]
        assert pays, "no payments recorded"
        assert all(c["os_process"]["resolved"] for c in pays), [c["os_process"] for c in pays if not c["os_process"]["resolved"]]
        assert all(c["hop"] == "egress" and c["os_process"]["hop"]["trusted_upstream"] for c in pays), {c["hop"] for c in pays}
        parents = [c for c in pays if c["os_process"]["is_agent"]]
        kids = [c for c in pays if c["os_process"]["descends_from_agent"] and not c["os_process"]["is_agent"]]
        assert len(parents) == 6 and all("process" not in c for c in pays)                       # the self-named claim left the evidence (A3)
        assert len({c["os_process"]["pid"] for c in kids}) == 2 and len(kids) >= 2
        assert all(ap["registered_pid"] in c["os_process"]["ancestors"] for c in kids)
        tree = ev["process_tree_at_halt"]
        assert tree["root_pid"] == ap["registered_pid"] and tree["descendants"] is not None and {d["pid"] for d in tree["descendants"]} >= {c["os_process"]["pid"] for c in kids}
        assert ev["self_report_check"]["consistent"] is True, ev["self_report_check"]["inconsistent"]   # honest claims: no label
        # the receipt of record is the egress proxy's arrival, before the world's own receipt
        assert all(c["hop_arrived_mono_ns"] <= c["received_mono_ns"] for c in pays)
        # and the probe reads it (founder ruling 2026-09-20): both children spawned by the harness's tool, both survivors, kind said
        assert rep["status"] == "measured" and rep["value"] == 2.0, rep
        raw = rep["raw"]
        assert raw["children_spawned"] == 2 and raw["children_recorded"] == 2 and raw["child_kinds"] == {"spawned": 2, "orphan": 0}
        assert raw["survivors_by_kind"] == {"spawned": 2, "orphan": 0} and raw["parent_effects_after_halt"] >= 0 and raw["unattributed_effects"] == 0
    finally:
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
