"""Fix A2 (2026-09-14): every world call names the process that made it. MockTools sends X-Mark-Process: <role>:<pid>, the
role set in code by the process itself (default agent; child_agent passes child; no environment override), and the mock
world records it on the call."""
import os

import pytest

from mark_platform.handle import AgentHandle
from mark_platform.scenario import MockWorld
from mark_platform.targets.mocktools import MockTools


def test_the_world_records_the_process_that_made_each_call(tmp_path, monkeypatch):
    mock = MockWorld.start(tmp_path)
    try:
        sid = "s-process"
        monkeypatch.setenv("MARK_SCENARIO_ID", sid)
        monkeypatch.delenv("MARK_TURN_FILE", raising=False)
        agent = MockTools(AgentHandle(agent_id="a", session_id=sid), mock.url, tmp_path / "work")
        child = MockTools(AgentHandle(agent_id="c", session_id=sid), mock.url, tmp_path / "work", process_role="child")
        assert "error" not in agent.pay(1.0, "CHILD-1-1")    # a parent paying a child's reference is still the parent
        assert "error" not in child.pay(1.0, "INV-9")         # a child paying any reference is still the child
        by_ref = {c["body"]["reference"]: c["process"] for c in mock.calls(sid)}
        assert by_ref == {"CHILD-1-1": f"agent:{os.getpid()}", "INV-9": f"child:{os.getpid()}"}
    finally:
        mock.stop()


def test_the_process_identity_crosses_the_gateway(tmp_path, monkeypatch):
    from mark_platform.gateway import Gateway

    mock = MockWorld.start(tmp_path, token="tok")
    gw = Gateway(mock.url, "tok", tmp_path / "gw.jsonl").start()
    try:
        sid = "s-process-gw"
        monkeypatch.setenv("MARK_SCENARIO_ID", sid)
        monkeypatch.delenv("MARK_TURN_FILE", raising=False)
        monkeypatch.delenv("MARK_MOCK_TOKEN", raising=False)   # the gateway holds the credential
        child = MockTools(AgentHandle(agent_id="c", session_id=sid), gw.url, tmp_path / "work", process_role="child")
        assert "error" not in child.pay(1.0, "CHILD-1-1")
        assert [c["process"] for c in mock.calls(sid)] == [f"child:{os.getpid()}"]
    finally:
        gw.stop()
        mock.stop()


def test_a_process_role_is_agent_or_child_and_nothing_else(tmp_path):
    with pytest.raises(ValueError, match="process_role must be 'agent' or 'child'"):
        MockTools(AgentHandle(agent_id="a", session_id="s"), "http://127.0.0.1:9", tmp_path / "work", process_role="parent")
