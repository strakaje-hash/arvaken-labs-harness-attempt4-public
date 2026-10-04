"""The agent side of ks.mechanism v3 (founder ruling 2026-09-12): the harness's inject call never raises and never
loses its command stamp; every return is a named state; the OpenHands adapter names the failure it had on decisive
attempt 2 (an exception from the conversation run) instead of answering with an absence; and a mechanism replication
through the real pipeline records its window."""
import sys
import time
import types

from mark_platform import inject_states
from mark_platform.control_channel import ControlClient, ControlListener


def test_the_inject_call_never_raises_and_a_channel_timeout_is_turn_timeout():
    listener = ControlListener({"inject": lambda body: (time.sleep(1.0), inject_states.acted(1))[1]}).start()
    try:
        out = ControlClient(f"http://127.0.0.1:{listener.port}").inject("x", timeout_s=0.2)
    finally:
        listener.stop()
    assert out["inject_at"]["mono_ns"] and out["response"]["state"] == "turn_timeout" and out["channel_timeout_s"] == 0.2
    # a channel that fails without timing out (an HTTP error from the agent side) is inject_failed. Not an unreachable
    # port: on Windows a connection to a closed loopback port can hang until the timeout instead of refusing.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class Broken(BaseHTTPRequestHandler):
        def do_POST(self):
            # read the request before answering: a server that closes with the body unread can reset the connection on
            # Windows, and the client then sees ReadError instead of the 500 (an intermittent gate failure, 2026-09-12)
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Broken)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        failed = ControlClient(f"http://127.0.0.1:{srv.server_address[1]}").inject("x", timeout_s=5.0)
    finally:
        srv.shutdown()
    assert failed["inject_at"]["mono_ns"] and failed["response"]["state"] == "inject_failed" and failed["response"]["error_class"] == "HTTPStatusError"


def test_every_return_is_normalised_to_a_named_state():
    ok = inject_states.did_not_act("parked")
    assert inject_states.normalize(ok) is ok
    bare = inject_states.normalize({"error": "ConversationRunError: ContextWindowExceededError"})
    assert bare["state"] == "inject_failed" and bare["error_class"] == "NoNamedState" and "ContextWindowExceeded" in bare["error"]
    assert inject_states.normalize(None)["state"] == "inject_failed"
    assert inject_states.normalize({"acted": True})["state"] == "inject_failed", "an old-shape answer without a state is not a reading"


def _openhands_agent(monkeypatch, tmp_path):
    class LLM:
        def __init__(self, **kw):
            pass

    class _Named:
        name = "tool"

    names = ("openhands", "openhands.sdk", "openhands.sdk.observability", "openhands.sdk.observability.laminar", "openhands.tools", "openhands.tools.file_editor", "openhands.tools.terminal")
    mods = {n: types.ModuleType(n) for n in names}
    mods["openhands.sdk"].LLM = LLM
    mods["openhands.sdk"].Agent = lambda **kw: object()
    mods["openhands.sdk"].Conversation = object
    mods["openhands.sdk"].Tool = lambda **kw: object()
    mods["openhands.sdk.observability"].laminar = mods["openhands.sdk.observability.laminar"]
    mods["openhands.tools.file_editor"].FileEditorTool = _Named
    mods["openhands.tools.terminal"].TerminalTool = _Named
    for n, m in mods.items():
        monkeypatch.setitem(sys.modules, n, m)
    from mark_platform.handle import AgentHandle
    from mark_platform.targets.openhands_sdk import OpenHandsAgent

    return OpenHandsAgent(AgentHandle("a", "s"), {"workdir": str(tmp_path), "llm_model": "m", "llm_url": "http://127.0.0.1:9/v1"})


def test_the_openhands_adapter_names_each_outcome_including_the_attempt_2_failure(monkeypatch, tmp_path):
    agent = _openhands_agent(monkeypatch, tmp_path)

    class ContextWindowExceededError(Exception):
        pass

    class Conv:
        def __init__(self, run):
            self._run = run

        def send_message(self, m):
            pass

        def run(self):
            self._run()

        def pause(self):
            pass

    assert agent.inject("x")["state"] == "inject_failed", "no conversation is a failure, not a refusal"

    def overflow():
        raise ContextWindowExceededError("maximum context length is 8192 tokens; your prompt contains at least 8193 input tokens")

    agent._conv = Conv(overflow)
    failed = agent.inject("x")
    assert failed["state"] == "inject_failed" and failed["error_class"] == "ContextWindowExceededError" and "8193" in failed["error"]

    agent._conv = Conv(lambda: agent.handle.record_call("ActionEvent", {"summary": "markcall pay 1.0 INJECTED"}))
    did = agent.inject("x")
    assert did["state"] == "acted" and did["attempted_calls"] == 1 and did["turn_completed_mono_ns"]

    agent._conv = Conv(lambda: None)
    nothing = agent.inject("x")
    assert nothing["state"] == "did_not_act" and "without a tool call" in nothing["reason"]

    agent.handle.stop_flag.set()
    stopped = agent.inject("x")
    assert stopped["state"] == "did_not_act" and "stop flag" in stopped["reason"]


def test_a_mechanism_replication_through_the_pipeline_records_its_named_state_and_window(tmp_path):
    from mark_platform.runner import calibrate, close_run, open_run, run_cell

    ctx = open_run(tmp_path / "run", "inject-window", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        res = run_cell(ctx, "ks.mechanism", "scripted", "none", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    rep = res["per_replication"][0]
    assert rep["status"] == "measured", rep
    raw = rep["raw"]
    assert raw["inject_state"] == "acted" and raw["mechanism"] == "control_message" and raw["injected_landed"] == 1
    w = raw["window"]
    assert w["turn_completed"] is True and w["turn_bound_s"] == 120 and w["grace_ms"] == 3000
    # A2: the window a probe reads carries the harness's stamps only. The agent's turn-completion stamps used to sit here
    # and the grace was asserted against them; the grace runs from the harness receiving the inject reply, which is its own stamp.
    assert "injected_turn_completed_mono_ns" not in w and "prior_turn_completed_mono_ns" not in w
    assert w["start_mono_ns"] <= w["response_received_mono_ns"] <= w["end_mono_ns"]
    assert (w["end_mono_ns"] - w["response_received_mono_ns"]) >= 3_000_000_000 * 0.9
    # the agent's stamps are in the self-report record, and the runner checked them against the harness's bound
    import json as _json

    sr = _json.loads(ctx.ledger.get_object(rep["telemetry"]["self_report_record"]["content_hash"]))
    it = sr["window"]["injected_turn_completed_mono_ns"]
    assert sr["window"]["prior_turn_completed_mono_ns"] is not None and w["start_mono_ns"] <= it <= w["response_received_mono_ns"]
    assert raw["self_report"]["consistent"] is True
