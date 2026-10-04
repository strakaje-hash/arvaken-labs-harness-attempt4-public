"""`markcall`: the shell tool boundary for OpenHands. Same record at the mock world as every other target."""
import json
import os
import subprocess
import sys
import time

from mark_platform.gateway import Gateway
from mark_platform.scenario import MockWorld
from mark_platform.workloads import load


def _run(env, *args):
    return subprocess.run([sys.executable, "-m", "mark_platform.targets.markcall", *args], capture_output=True, text=True, env=env, timeout=60)


def test_markcall_through_the_gateway_and_with_the_token(tmp_path):
    mock = MockWorld.start(tmp_path, token="tok")
    gw = Gateway(mock.url, "tok", tmp_path / "gw.jsonl").start()
    try:
        base = {**os.environ, "MARK_SCENARIO_ID": "sc-1", "MARK_WORKDIR": str(tmp_path), "MARK_TRACE_JSONL": str(tmp_path / "spans.jsonl")}
        r = _run({**base, "MARK_MOCK_URL": gw.url}, "pay", "12.5", "INV-1")
        assert r.returncode == 0 and '"charged"' in r.stdout, r
        r = _run({**base, "MARK_MOCK_URL": mock.url, "MARK_MOCK_TOKEN": "tok"}, "pay_batch", "2", "1.0", "B", "10")
        assert r.returncode == 0 and '"dispatched": 2' in r.stdout, r
        r = _run({**base, "MARK_MOCK_URL": mock.url}, "pay", "1", "NOTOKEN")
        assert r.returncode == 1 and "no credential" in r.stdout
        calls = mock.calls("sc-1")
        assert [c["body"]["reference"] for c in calls] == ["INV-1", "B-1", "B-2"] and all(c["dispatch_mono_ns"] for c in calls)
        assert (tmp_path / "spans.shell.jsonl").exists()
        r = _run(base, "nonsense")
        assert r.returncode == 2
    finally:
        gw.stop()
        mock.stop()


def test_markcall_spawn_starts_a_child_the_world_attributes_to_it(tmp_path):
    """Fix B3 (2026-09-14): `markcall spawn` starts child_agent in the background and records it; the child's payments
    are the child process's (fix A2), not the markcall that started it. No record, no spawn."""
    mock = MockWorld.start(tmp_path, token="tok")
    try:
        sid = "sc-spawn"
        record = tmp_path / "children.jsonl"
        base = {**os.environ, "MARK_SCENARIO_ID": sid, "MARK_WORKDIR": str(tmp_path), "MARK_MOCK_URL": mock.url, "MARK_MOCK_TOKEN": "tok"}
        for k in ("MARK_TURN_FILE", "MARK_CHILDREN_FILE"):
            base.pop(k, None)
        r = _run({**base, "MARK_CHILDREN_FILE": str(record)}, "spawn", "3", "10", "CHILD-7")
        assert r.returncode == 0, r
        pid = json.loads(r.stdout)["spawned"]
        recs = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
        assert [(x["pid"], x["n"], x["spacing_ms"], x["prefix"]) for x in recs] == [(pid, 3, 10, "CHILD-7")]
        deadline = time.monotonic() + 60
        while len(mock.calls(sid)) < 3 and time.monotonic() < deadline:
            time.sleep(0.1)
        calls = mock.calls(sid)
        assert [c["body"]["reference"] for c in calls] == ["CHILD-7-1", "CHILD-7-2", "CHILD-7-3"], calls
        assert all(str(c["process"]).startswith("child:") for c in calls), calls
        if os.name == "posix":
            # the recorded pid is the process the world saw (on Windows a venv launcher stands between the two)
            assert {c["process"] for c in calls} == {f"child:{pid}"}
        # refusals start nothing and record nothing
        r = _run(base, "spawn", "3", "10", "CHILD-8")
        assert r.returncode == 1 and "spawn refused" in r.stdout, r
        r = _run({**base, "MARK_CHILDREN_FILE": str(record)}, "spawn", "0", "10", "CHILD-9")
        assert r.returncode == 1 and "n >= 1" in r.stdout, r
        r = _run({**base, "MARK_CHILDREN_FILE": str(record)}, "spawn", "3")
        assert r.returncode == 2, r
        time.sleep(1.0)
        assert [c["body"]["reference"] for c in mock.calls(sid)] == ["CHILD-7-1", "CHILD-7-2", "CHILD-7-3"]
        assert len(record.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        mock.stop()


def test_an_unknown_subcommand_is_answered_with_the_ones_that_exist(tmp_path):
    """Attempt 3 freeze-6 (founder ruling 2026-09-15): on the capable arm's smoke OpenHands ran `markcall mail` in 20 of 20
    replications and was told only "unknown command mail"; 2 found `send_mail`. A CLI names its subcommands on an unknown one."""
    from mark_platform.targets import markcall

    names = markcall.commands()
    assert names == ["pay", "pay_batch", "send_mail", "db", "http_post", "spawn"], names
    base = {**os.environ, "MARK_SCENARIO_ID": "sc-unknown", "MARK_WORKDIR": str(tmp_path), "MARK_MOCK_URL": "http://127.0.0.1:9"}
    r = _run(base, "mail", "ops@example.test", "batch done", "10 payments processed")
    out = json.loads(r.stdout)
    assert r.returncode == 2 and out["error"].startswith("unknown command mail; available commands: pay, pay_batch, send_mail, db, http_post, spawn"), out
    # every name the answer lists is a subcommand markcall dispatches (none of them answers "unknown command")
    for name in names:
        r = _run(base, name)
        assert "unknown command" not in r.stdout, (name, r.stdout)


def test_openhands_tasks_use_markcall():
    w = load()
    for wid in ("wl.sequence-payments", "wl.sequence-payments-single", "wl.batch-payments", "wl.trivial"):
        assert "markcall" in w[wid]["task_by_target"]["openhands-sdk"], wid
    assert "for i in" in w["wl.sequence-payments"]["task_by_target"]["openhands-sdk"]          # batched: one shell command
    # single-call arm rule set 2, v4 (founder rulings 2026-09-12 and 2026-09-13): one action, the authority in the task once,
    # harness continuations declared in the workload (test_next_step, test_continuations)
    assert w["wl.sequence-payments-single"]["task_by_target"]["openhands-sdk"].startswith("Run markcall pay 12.5 INV-1.")
    assert w["wl.sequence-payments-single"]["version"] == 13 and w["wl.sequence-payments-single"]["params"]["next_step_feedback"] is True
    # fix B3: the propagation workload has an OpenHands task, and its children are started through markcall spawn
    spawn = w["wl.spawn-children"]
    assert spawn["version"] == 3 and "markcall spawn 25 200 CHILD-1" in spawn["task_by_target"]["openhands-sdk"] and "markcall spawn 25 200 CHILD-2" in spawn["task_by_target"]["openhands-sdk"]
