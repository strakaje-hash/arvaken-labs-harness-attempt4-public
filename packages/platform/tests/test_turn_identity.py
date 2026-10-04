"""A1: turn identity is assigned by the harness, not read from the agent.

The plan names two tests, and they are the first two here:

  (a) a probe that halts and re-prompts (mechanism, resume) produces turn ids that match the model calls sent --
      `test_every_effect_carries_the_number_of_replies_sent_before_it_across_a_halt_and_a_continuation`;
  (b) an agent whose own counter runs 1, 3, 5 does not perturb the harness's --
      `test_an_agent_counter_running_1_3_5_does_not_perturb_the_harness_turn`.

The rest are the edges the first two would leave open: a reply with no tool call (the OpenHands shape that broke
attempt 3's audit), the ordering guarantee the whole design rests on, a scenario nobody registered, an error reply,
and the R3 control -- the identity check shown to report a breach before its silence is read as consistency.

Every test drives the real pieces: a `ModelProxy` in front of a fake upstream, `MockTools` reading the turn file
the proxy writes, and the `MockWorld` recording and enforcing. The "agent" is the test's own loop, because what
the agent believes is exactly what must not matter.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mark_platform.handle import AgentHandle
from mark_platform.model_proxy import ModelProxy
from mark_platform.scenario import MockWorld
from mark_platform.targets.mocktools import MockTools
from mark_platform.turns import HarnessTurns, TurnFile, check_turns


def _reply(tool_calls: int, content: str | None = None) -> bytes:
    calls = [{"id": f"t{i}", "type": "function", "function": {"name": "pay", "arguments": "{}"}} for i in range(tool_calls)]
    msg = {"role": "assistant", "content": content, "tool_calls": calls}
    return json.dumps({"choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()


@pytest.fixture
def upstream():
    """A fake model server that answers from a script the test fills in. Runs out of script = HTTP 500, loudly."""
    script: list[bytes] = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            if not script:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = script.pop(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", script
    srv.shutdown()


class Rig:
    """One scenario wired the way scenario.py wires it: proxy owns the turn file, tools read it, the world enforces."""

    def __init__(self, tmp_path, monkeypatch, upstream_url: str, sid: str = "s-a1", *, register: bool = True, policy: bool = True):
        self.sid = sid
        self.mock = MockWorld.start(tmp_path)
        self.mock.set_policy(sid, single_call_per_turn=policy)
        # the runner creates the run dir before the proxy opens its log in it. Without this the proxy threw on every
        # record's log write -- after the in-memory append and, on the success path, after the reply was sent -- so
        # every test here stayed green while the handler thread died each call. Found by the one path that records
        # before it sends (the 502 reply), which is the only reason it was found at all.
        (tmp_path / "run").mkdir(exist_ok=True)
        self.px = ModelProxy(upstream_url, tmp_path / "run").start()
        self.tf = tmp_path / "turn"
        if register:
            self.px.assign_turns(sid, self.tf)
            monkeypatch.setenv("MARK_TURN_FILE", str(self.tf))
        else:
            monkeypatch.delenv("MARK_TURN_FILE", raising=False)
        monkeypatch.setenv("MARK_SCENARIO_ID", sid)
        self.handle = AgentHandle(agent_id="fake-agent", session_id=sid)
        self.tools = MockTools(self.handle, self.mock.url, tmp_path / "work")

    def model_call(self) -> tuple[int, int | None]:
        """One model call through the proxy. Returns (tool calls in the reply, the turn file as read the instant the
        reply's headers arrived -- before the body is read, before the agent could act on it)."""
        import time

        n0 = len(self.px.calls(self.sid))
        req = urllib.request.Request(self.px.agent_url(self.sid) + "/chat/completions", data=b'{"model":"m","messages":[]}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            turn_on_arrival = TurnFile(self.tf).read()
            body = json.loads(r.read())
        # The proxy records a call AFTER its reply is on the wire (by design: the agent is never delayed by logging), and
        # urlopen returns as soon as the body is read, so a reader that acts on the reply can look before the record exists.
        # Under the full suite's load that gap was wide enough to fail once. The turn file is written BEFORE the reply
        # (the ordering test above proves it); the record is not, so it is waited for here, bounded.
        deadline = time.monotonic() + 5.0
        while len(self.px.calls(self.sid)) <= n0:
            if time.monotonic() > deadline:
                raise AssertionError(f"the proxy had not recorded the call 5 s after its reply was read (had {n0} before)")
            time.sleep(0.005)
        return len(body["choices"][0]["message"].get("tool_calls") or []), turn_on_arrival

    def effects(self):
        return [c for c in self.mock.calls(self.sid) if not c["refused"]]

    def close(self):
        self.px.stop()
        self.mock.stop()


# ---------------------------------------------------------------- (a) the plan's first test

def test_every_effect_carries_the_number_of_replies_sent_before_it_across_a_halt_and_a_continuation(tmp_path, monkeypatch, upstream):
    url, script = upstream
    script += [_reply(1), _reply(1), _reply(1), _reply(1)]
    rig = Rig(tmp_path, monkeypatch, url)
    try:
        assert TurnFile(rig.tf).read() == 0                       # visible before the first reply
        # three model calls, each followed by the effect its reply asked for
        for n in (1, 2, 3):
            calls, _ = rig.model_call()
            assert calls == 1
            assert "error" not in rig.tools.pay(1.0, f"ref-{n}")
        # the halt: nothing the harness does here opens a turn ...
        rig.handle.stop()
        assert TurnFile(rig.tf).read() == 3
        # ... and the continuation the harness sends is answered by the agent's next model call, which does
        rig.handle.resume()
        calls, _ = rig.model_call()
        assert "error" not in rig.tools.pay(1.0, "ref-after-continuation")
        world = rig.mock.calls(rig.sid)
        assert [c["turn"] for c in world] == [1, 2, 3, 4]
        assert all(c["refused"] is None for c in world)
        assert [m["turn"] for m in rig.px.calls(rig.sid)] == [1, 2, 3, 4]
        # the identity, stated once and checked on every call
        assert check_turns(world, rig.px.calls(rig.sid)) == []
    finally:
        rig.close()


# ---------------------------------------------------------------- (b) the plan's second test

def test_an_agent_counter_running_1_3_5_does_not_perturb_the_harness_turn(tmp_path, monkeypatch, upstream):
    """The agent advances its own counter twice per model call, as both frameworks do on continuations and on
    replies with no tool call. The world's `turn` is the proxy's count regardless; the agent's is recorded beside it."""
    url, script = upstream
    script += [_reply(1), _reply(1), _reply(1)]
    rig = Rig(tmp_path, monkeypatch, url)
    try:
        for _ in range(3):
            rig.handle.agent_turn_advanced()          # the framework "begins a turn" ...
            rig.model_call()
            assert "error" not in rig.tools.pay(1.0, "x")
            rig.handle.agent_turn_advanced()          # ... and again on the reply, without a tool call
        world = rig.mock.calls(rig.sid)
        assert [c["turn"] for c in world] == [1, 2, 3]
        assert [c["agent_turn"] for c in world] == [1, 3, 5]
        assert check_turns(world, rig.px.calls(rig.sid)) == []
        # and the disagreement is visible in the agent's own record too, not only at the world
        assert rig.handle.agent_turn == 6 and rig.px.turn(rig.sid) == 3
    finally:
        rig.close()


# ---------------------------------------------------------------- the edges

def test_a_reply_with_no_tool_call_still_opens_a_turn(tmp_path, monkeypatch, upstream):
    """The OpenHands shape: a text-only reply. It opened no effect, but it was reply 1, so the next reply is 2 and the
    effect it provokes carries 2. Attempt 3's audit rule (1 <= turn <= tool calls sent) would flag exactly this;
    under A1 it is the identity holding, and a turn with no effects is a fact."""
    url, script = upstream
    script += [_reply(0, "I will pay now."), _reply(1)]
    rig = Rig(tmp_path, monkeypatch, url)
    try:
        calls, _ = rig.model_call()
        assert calls == 0 and TurnFile(rig.tf).read() == 1
        calls, _ = rig.model_call()
        assert calls == 1 and TurnFile(rig.tf).read() == 2
        assert "error" not in rig.tools.pay(1.0, "x")
        world = rig.mock.calls(rig.sid)
        assert [c["turn"] for c in world] == [2]
        assert check_turns(world, rig.px.calls(rig.sid)) == []
    finally:
        rig.close()


def test_the_turn_is_published_before_the_reply_reaches_the_agent(tmp_path, monkeypatch, upstream):
    """The ordering the design rests on. If the proxy wrote the file after forwarding, a fast agent could dispatch
    reply N's tool while the file still said N-1 -- and the world would file the effect under the wrong turn with
    every test above still green, because they read the file only after the body. This reads it the instant the
    headers arrive."""
    url, script = upstream
    script += [_reply(1)] * 5
    rig = Rig(tmp_path, monkeypatch, url)
    try:
        for n in range(1, 6):
            _, on_arrival = rig.model_call()
            assert on_arrival == n, f"reply {n} arrived while the turn file read {on_arrival}"
    finally:
        rig.close()


def test_a_scenario_nobody_registered_gets_no_turn_and_is_refused_under_the_policy(tmp_path, monkeypatch, upstream):
    """No harness owner, no id. The tools do not fall back to the agent's counter -- that fallback was the defect --
    so the world sees nothing and, under the policy, refuses. Fail closed, and the refusal names why."""
    url, script = upstream
    script += [_reply(1)]
    rig = Rig(tmp_path, monkeypatch, url, register=False)
    try:
        rig.handle.agent_turn_advanced()
        rig.model_call()
        assert rig.px.calls(rig.sid)[0]["turn"] is None
        out = rig.tools.pay(1.0, "x")
        assert out.get("refused") and "no turn id" in out["error"], out
        rec = rig.mock.calls(rig.sid)[0]
        assert rec["turn"] is None and rec["agent_turn"] == 1
    finally:
        rig.close()


def test_an_error_reply_opens_a_turn_too(tmp_path, monkeypatch):
    """Reply N is reply N whatever it held. An upstream that cannot be reached still answered the agent (with a 502),
    so the count moves; otherwise the next good reply would be numbered as if the failed one never happened, and the
    proxy's record and the world's would disagree about which reply provoked which effect."""
    mock = MockWorld.start(tmp_path)
    (tmp_path / "run").mkdir(exist_ok=True)
    px = ModelProxy("http://127.0.0.1:9/v1", tmp_path / "run").start()   # port 9: discard, nothing listens
    try:
        tf = tmp_path / "turn"
        px.assign_turns("s-err", tf)
        req = urllib.request.Request(px.agent_url("s-err") + "/chat/completions", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=10)
        assert caught.value.code == 502
        assert TurnFile(tf).read() == 1
        c = px.calls("s-err")[0]
        assert c["error_class"] == "upstream_unreachable" and c["turn"] == 1
    finally:
        px.stop()
        mock.stop()


def test_the_scripted_drivers_turns_are_the_harness_turns(tmp_path, monkeypatch):
    """The other owner. The scripted reference has no reply sequence; its script is the harness's, so its step
    counter is the id. HarnessTurns writes 0 on construction and each step after, and the tools read it."""
    mock = MockWorld.start(tmp_path)
    try:
        sid = "s-scripted"
        mock.set_policy(sid, single_call_per_turn=True)
        tf = tmp_path / "turn"
        monkeypatch.setenv("MARK_TURN_FILE", str(tf))
        monkeypatch.setenv("MARK_SCENARIO_ID", sid)
        turns = HarnessTurns(TurnFile.from_env(), assigned_by="scripted-driver")
        assert TurnFile(tf).read() == 0
        tools = MockTools(AgentHandle(agent_id="scripted", session_id=sid), mock.url, tmp_path / "work")
        for step in (1, 2, 3):
            assert turns.advance() == step
            assert "error" not in tools.pay(1.0, f"step-{step}")
            assert tools.pay(1.0, f"step-{step}-again").get("refused")
        assert [c["turn"] for c in mock.calls(sid)] == [1, 1, 2, 2, 3, 3]
    finally:
        mock.stop()


# ---------------------------------------------------------------- R3: the check can see a breach

def test_check_turns_reports_a_turn_that_is_not_the_reply_count(tmp_path, monkeypatch, upstream):
    """Positive control for the identity check. A silent `check_turns` is read as "consistent" by scenario.py, so it
    has to be shown reporting a breach first: the same records with one turn moved by one, and with one removed."""
    url, script = upstream
    script += [_reply(1), _reply(1)]
    rig = Rig(tmp_path, monkeypatch, url)
    try:
        for _ in range(2):
            rig.model_call()
            rig.tools.pay(1.0, "x")
        world, model = rig.mock.calls(rig.sid), rig.px.calls(rig.sid)
        assert check_turns(world, model) == []
        off_by_one = [dict(world[0]), {**world[1], "turn": 3}]
        found = check_turns(off_by_one, model)
        assert [f["seq"] for f in found] == [world[1]["seq"]] and found[0]["turn"] == 3 and found[0]["turns_opened_at_or_before_receipt"] == 2
        missing = [{**world[0], "turn": None}, dict(world[1])]
        assert [f["turn"] for f in check_turns(missing, model)] == [None]
        # a calibration call carries no turn and is not the agent's: never a breach
        assert check_turns([{**world[0], "path": "/calibration/ping", "turn": None}], model) == []
    finally:
        rig.close()
