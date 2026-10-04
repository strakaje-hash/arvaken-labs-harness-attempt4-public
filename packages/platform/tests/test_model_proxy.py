"""The model path's instrument (founder ruling 2026-09-12; constitution model-integrity).

- The proxy forwards byte for byte, records the SHA-256 of both bodies and measures its own overhead.
- Every model-error class is detected, in precedence order, and a clean call is not flagged.
- Streaming replies are reconstructed.
- Only calls inside the observation window classify a replication.
- Capture writes the raw bodies.
- Bundles measured before the proxy existed are classified at read time from the agent's own records (the
  attempt-2 shapes)."""
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mark_platform.model_integrity import classify_call, model_errors_from_agent_records, model_integrity
from mark_platform.model_proxy import ModelProxy, summarize_exchange

RESPONSE = b'{"id":"x",  "choices":[{"index":0,"message":{"role":"assistant","content":null,"tool_calls":[{"id":"t","type":"function","function":{"name":"pay","arguments":"{}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":5950,"completion_tokens":41,"total_tokens":5991}}\n'


@pytest.fixture
def upstream():
    seen = {}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen["body"] = self.rfile.read(n)
            seen["path"] = self.path
            seen["x-test"] = self.headers.get("X-Test")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Upstream", "vllm-fake")
            self.send_header("Content-Length", str(len(RESPONSE)))
            self.end_headers()
            self.wfile.write(RESPONSE)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", seen
    srv.shutdown()


def test_the_proxy_forwards_byte_identical_and_records_hashes_and_its_own_overhead(upstream, tmp_path):
    import hashlib

    url, seen = upstream
    px = ModelProxy(url, tmp_path).start()
    try:
        body = b'{"model": "m",   "messages": [{"role": "user", "content": "pay \\u00e9"}],\n "temperature": 0}'
        req = urllib.request.Request(px.agent_url("wl.x-openhands-sdk-none-3-ab12") + "/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Test": "kept"})
        with urllib.request.urlopen(req, timeout=10) as r:
            got, headers = r.read(), dict(r.headers)
    finally:
        px.stop()
    assert seen["body"] == body and seen["path"] == "/v1/chat/completions" and seen["x-test"] == "kept"
    assert got == RESPONSE and headers.get("X-Upstream") == "vllm-fake"
    calls = px.calls("wl.x-openhands-sdk-none-3-ab12")
    assert len(calls) == 1
    c = calls[0]
    assert c["request_sha256"] == hashlib.sha256(body).hexdigest() and c["response_sha256"] == hashlib.sha256(RESPONSE).hexdigest()
    assert c["proxy_overhead_ms"] >= 0 and c["upstream_ms"] >= 0 and c["tool_calls"] == 1 and c["prompt_tokens"] == 5950
    s = px.summary()
    assert s["calls"] == 1 and s["overhead_ms"]["max"] is not None and "best effort" in s["direct_access"]
    assert (tmp_path / "model-calls.jsonl").exists()


def _body(content=None, tool_calls=None, finish="stop", prompt=100, completion=10):
    msg = {"role": "assistant", "content": content, "tool_calls": tool_calls or []}
    return json.dumps({"choices": [{"message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}}).encode()


def test_each_class_is_detected_in_precedence_and_a_clean_call_is_not_flagged():
    req = b'{"messages": [1, 2]}'
    clean = summarize_exchange(req, 200, _body(tool_calls=[{"id": "t"}], finish="tool_calls"))
    assert classify_call(clean, 8192) is None
    ctx_err = summarize_exchange(req, 400, b'{"error": {"message": "This model\'s maximum context length is 8192 tokens. However, you requested 0 output tokens"}}')
    assert classify_call(ctx_err, 8192) == "context_window_exceeded"
    at_limit = summarize_exchange(req, 200, _body(content='<tool_call>\n{"name": "', finish="length", prompt=8145, completion=47))
    assert classify_call(at_limit, 8192) == "truncated_at_context_limit"
    short = summarize_exchange(req, 200, _body(content="partial", finish="length", prompt=100, completion=16))
    assert classify_call(short, 8192) == "truncated"
    # a tool call as text is a model error only when the request offered a tool to call (founder ruling 2026-09-23; the other
    # way is in test_tool_call_without_tools.py)
    with_tools = b'{"messages": [1, 2], "tools": [{"type": "function", "function": {"name": "terminal"}}]}'
    unparsed = summarize_exchange(with_tools, 200, _body(content="<tool_call>\n{\"name\": \"terminal\"}\n</tool_call>\n<tool_call>\n{\"name\": \"terminal\"}\n</tool_call>", finish="stop", completion=723))
    assert classify_call(unparsed, 8192) == "unparsed_tool_call"
    server = summarize_exchange(req, 500, b"internal")
    assert classify_call(server, 8192) == "http_error:500"
    mi = model_integrity([{**unparsed, "seq": 1, "request_mono_ns": 1}, {**ctx_err, "seq": 2, "request_mono_ns": 2}], 8192)
    assert mi["error_class"] == "context_window_exceeded" and [e["class"] for e in mi["errors"]] == ["context_window_exceeded", "unparsed_tool_call"]
    assert mi["context_limit_hits"] == 1 and mi["max_prompt_tokens"] == 100


def test_a_streamed_reply_is_reconstructed():
    sse = ("data: " + json.dumps({"choices": [{"delta": {"content": "<tool_call>"}, "finish_reason": None}]}) + "\n\n"
           + "data: " + json.dumps({"choices": [{"delta": {"content": "{}"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 8150, "completion_tokens": 42, "total_tokens": 8192}}) + "\n\n"
           + "data: [DONE]\n\n").encode()
    s = summarize_exchange(b'{"stream": true}', 200, sse, "text/event-stream")
    assert s["stream"] and s["finish_reasons"] == ["length"] and s["content_has_tool_call_markup"] and classify_call(s, 8192) == "truncated_at_context_limit"


def test_only_calls_inside_the_observation_window_classify_the_replication():
    bad = {**summarize_exchange(b"{}", 500, b"x"), "request_mono_ns": 200}
    assert model_integrity([bad], 8192, window_end_mono_ns=100)["error_class"] is None
    assert model_integrity([bad], 8192, window_end_mono_ns=300)["error_class"] == "http_error:500"
    assert model_integrity(None)["observed"] is False and model_integrity([])["error_class"] is None


def test_capture_writes_the_raw_bodies_and_unscoped_calls_are_recorded(upstream, tmp_path):
    url, _ = upstream
    px = ModelProxy(url, tmp_path, capture=True).start()
    try:
        for path in (px.agent_url("s1") + "/chat/completions", px.url + "/v1/chat/completions"):
            urllib.request.urlopen(urllib.request.Request(path, data=b'{"messages": []}', method="POST"), timeout=10).read()
    finally:
        px.stop()
    assert (tmp_path / "model-capture" / "s1").is_dir() and len(list((tmp_path / "model-capture" / "s1").iterdir())) == 2
    assert px.summary()["unscoped_calls"] == 1


def test_bundles_before_the_proxy_are_classified_at_read_time_from_the_agents_own_records():
    """The attempt-2 shapes: an agent error record carrying ContextWindowExceededError; a reply cut off mid tool call
    at 8.14K input; a complete multi-call reply returned as text at 5.95K input."""
    err = {"status": "error", "reason": "ConversationRunError: litellm.ContextWindowExceededError: maximum context length is 8192 tokens"}
    assert model_errors_from_agent_records(err, "", 8192)["error_class"] == "context_window_exceeded"
    cut = "Message from Agent ─────\n\n<tool_call>\n{\"name\": \"terminal\"}\n</tool_call>\n<tool_call>\n{\"name\": \"\nTokens: ↑ input 8.14K (total 14.1K) • ↓ output 47 (total 528)"
    r = model_errors_from_agent_records({"status": "ok"}, cut, 8192)
    assert r["error_class"] == "truncated_at_context_limit" and {c["source"] for c in r["classes"]} >= {"agent log (reply text)"}
    whole = "Message from Agent ─────\n\n<tool_call>\n{\"name\": \"terminal\"}\n</tool_call>\n<tool_call>\n{\"name\": \"terminal\"}\n</tool_call>\nDONE\nTokens: ↑ input 5.95K (total 5.95K) • ↓ output 723 (total 723)"
    assert model_errors_from_agent_records({"status": "ok"}, whole, 8192)["error_class"] == "unparsed_tool_call"
    parsed = "Agent Action ─────\n\nSummary: terminal: {\"command\": \"markcall pay 12.5 INV-1\"}\nTokens: ↑ input 5.95K (total 5.95K) • ↓ output 481 (total 481)"
    assert model_errors_from_agent_records({"status": "ok"}, parsed, 8192)["error_class"] is None


# ---- task d67450f4: the in-memory record must never silently outlive the on-disk one ----

def _settled(px, sid, n, timeout_s=5.0):
    """The proxy records a call after its reply is on the wire, so a client that has its reply may be ahead of the record."""
    import time

    t0 = time.monotonic()
    while len(px.calls(sid)) < n:
        assert time.monotonic() - t0 < timeout_s, f"{len(px.calls(sid))} of {n} calls recorded after {timeout_s}s"
        time.sleep(0.02)


def test_a_proxy_refuses_a_run_dir_that_does_not_exist_at_construction_not_per_call(tmp_path):
    from mark_platform.model_proxy import ModelProxyLogError

    with pytest.raises(ModelProxyLogError, match="does not exist"):
        ModelProxy("http://127.0.0.1:9/v1", tmp_path / "no-such-run")
    assert not (tmp_path / "no-such-run").exists()   # it did not create one either: the run dir is the runner's to make


def test_a_log_write_lost_mid_run_is_counted_named_on_the_entry_and_makes_the_log_incomplete(upstream, tmp_path):
    """Before the fix the memory append came first and the failed open killed the handler thread after the agent had its
    reply: memory 2, disk 1, nothing said so. Now the disk line is written first, the failure is on the entry and in the
    summary, and the agent still gets its reply (the loss is the instrument's to report, not the agent's to suffer)."""
    url, _ = upstream
    px = ModelProxy(url, tmp_path).start()
    try:
        body = b'{"model": "m", "messages": []}'
        req = urllib.request.Request(px.agent_url("s1") + "/chat/completions", data=body, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
        _settled(px, "s1", 1)   # the first record lands after its reply; under load the swap below would otherwise beat it
        # the log path is what record() reads; pointing it into a directory that does not exist is the loss, mid-run
        px.log_path = tmp_path / "gone" / "model-calls.jsonl"
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200 and r.read() == RESPONSE   # the agent's reply is whole
        _settled(px, "s1", 2)   # the record lands after the reply is on the wire; wait for it, bounded
        s = px.summary()
        calls = px.calls("s1")
    finally:
        px.stop()
    assert len(calls) == 2 and "log_write_error" not in calls[0] and calls[1]["log_write_error"].startswith("FileNotFoundError")
    assert s["calls"] == 2 and s["log_lines"] is None and s["log_write_errors"] == 1 and s["log_complete"] is False
    # and the one line that did land is the first call, on the original path
    lines = (tmp_path / "model-calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["seq"] == 1


def test_a_whole_log_reads_complete_line_for_call(upstream, tmp_path):
    url, _ = upstream
    px = ModelProxy(url, tmp_path).start()
    try:
        req = urllib.request.Request(px.agent_url("s1") + "/chat/completions", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        for _ in range(3):
            urllib.request.urlopen(req, timeout=10).read()
        _settled(px, "s1", 3)
        s = px.summary()
    finally:
        px.stop()
    assert s["calls"] == 3 and s["log_lines"] == 3 and s["log_write_errors"] == 0 and s["log_complete"] is True
    # a proxy with no run dir has no log to be short of
    assert ModelProxy(url).summary()["log_complete"] is True


def test_a_run_whose_model_call_log_is_short_fails_at_close_like_a_dropped_span(tmp_path):
    from mark_platform.runner import calibrate, close_run, open_run

    ctx = open_run(tmp_path / "run", "short-log", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        ctx.model_proxy.record("s1", {"method": "POST", "path": "/v1/chat/completions"}, b"{}", b"{}")          # lands
        ctx.model_proxy.log_path = tmp_path / "run" / "gone" / "model-calls.jsonl"
        ctx.model_proxy.record("s1", {"method": "POST", "path": "/v1/chat/completions"}, b"{}", b"{}")          # lost
        out = close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    results = json.loads(Path(out["results"]).read_text(encoding="utf-8"))
    assert results["run_failed"] is True and "model-call log incomplete: None line(s) on disk for 2 call(s), 1 write error(s)" in results["run_failure_reason"]
    assert results["model_proxy"]["log_complete"] is False and results["model_proxy"]["log_write_errors"] == 1
    from mark_platform.report import report_run

    assert "**RUN FAILED**" in report_run(out["run_dir"])
