"""What a replication is on a model-driven target (founder ruling 2026-09-12). Swapping only the scenario id in a captured
first request flipped the model's reply both ways in every cell tried, so the id is a declared variation source; every
replication records its prompt hash and first-reply path hash; every cell counts its distinct first-reply paths and
flags a cell that sampled one behaviour. The hashes never touch the forwarded bytes, and whitespace in tool-call
arguments cannot split one path into two."""
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from mark_platform.model_proxy import ModelProxy, summarize_exchange
from mark_platform.reply_paths import cell_reply_paths, first_reply


def _resp(arguments='{"command": "markcall pay 12.5 INV-1"}', content=None, finish="tool_calls", name="terminal"):
    return json.dumps({"choices": [{"message": {"content": content, "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()


REQ = json.dumps({"model": "M", "messages": [{"role": "user", "content": "pay"}], "prompt_cache_key": "a"}).encode()


def test_the_reply_path_ignores_argument_whitespace_and_nothing_else():
    base = summarize_exchange(REQ, 200, _resp())["reply_path_sha256"]
    assert summarize_exchange(REQ, 200, _resp('{ "command" :  "markcall pay 12.5 INV-1" }'))["reply_path_sha256"] == base
    assert summarize_exchange(REQ, 200, _resp('{"command": "markcall pay 12.5 INV-2"}'))["reply_path_sha256"] != base
    assert summarize_exchange(REQ, 200, _resp(content="thinking"))["reply_path_sha256"] != base
    assert summarize_exchange(REQ, 200, _resp(finish="stop"))["reply_path_sha256"] != base
    assert summarize_exchange(REQ, 200, _resp(name="finish"))["reply_path_sha256"] != base
    assert summarize_exchange(REQ, 500, b"boom").get("reply_path_sha256") is None


def test_the_prompt_hash_ignores_the_per_conversation_cache_key_only():
    other_key = json.dumps({"model": "M", "messages": [{"role": "user", "content": "pay"}], "prompt_cache_key": "b"}).encode()
    other_prompt = json.dumps({"model": "M", "messages": [{"role": "user", "content": "pay now"}], "prompt_cache_key": "a"}).encode()
    h = summarize_exchange(REQ, 200, _resp())["prompt_sha256"]
    assert summarize_exchange(other_key, 200, _resp())["prompt_sha256"] == h
    assert summarize_exchange(other_prompt, 200, _resp())["prompt_sha256"] != h


def test_a_streamed_reply_has_the_same_path_as_the_same_reply_unstreamed():
    chunks = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "terminal", "arguments": '{"command": '}}]}}]},
              {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"markcall pay 12.5 INV-1"}'}}]}, "finish_reason": "tool_calls"}]}]
    stream = ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
    s = summarize_exchange(json.dumps({"stream": True, "messages": []}).encode(), 200, stream, "text/event-stream")
    assert s["tool_calls"] == 1 and s["reply_path_sha256"] == summarize_exchange(REQ, 200, _resp())["reply_path_sha256"]


@pytest.fixture
def upstream():
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = _resp()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_the_first_exchange_of_every_scenario_is_captured_and_nothing_else(upstream, tmp_path):
    px = ModelProxy(upstream, tmp_path, capture_first=True).start()
    try:
        for sid in ("s1", "s1", "s2"):
            urllib.request.urlopen(urllib.request.Request(px.agent_url(sid) + "/chat/completions", data=REQ, method="POST"), timeout=10).read()
        urllib.request.urlopen(urllib.request.Request(px.url + "/v1/chat/completions", data=REQ, method="POST"), timeout=10).read()
    finally:
        px.stop()
    cap = tmp_path / "model-capture"
    assert sorted(p.name for p in (cap / "s1").iterdir()) == ["000001.request.json", "000001.response.json"]
    assert len(list((cap / "s2").iterdir())) == 2 and not (cap / "_unscoped").exists()
    assert (cap / "s1" / "000001.request.json").read_bytes() == REQ
    assert first_reply(px.calls("s1"))["seq"] == 1 and first_reply(px.calls("s1"))["reply_path_sha256"] and px.summary()["capture_first_exchange"] is True


def _rep(status, path, prompt="p"):
    return {"status": status, "raw": {"model": {"first_reply": {"reply_path_sha256": path, "prompt_sha256": prompt}}}}


def test_a_cell_counts_distinct_first_reply_paths_and_flags_one_sample_of_behaviour():
    mixed = cell_reply_paths([_rep("measured", "a", "p1"), _rep("measured", "a", "p2"), _rep("measured", "b", "p3"), _rep("not_run", "c")],
                             model_driven=True, variation="scenario_id", min_replications=20)
    assert mixed["measured"] == 3 and mixed["distinct_first_reply_paths"] == 2 and mixed["distinct_prompts"] == 3 and mixed["single_path"] is False
    assert mixed["min_replications_against_distinct_paths"] == "not met" and "informational" in mixed["reading"]
    one = cell_reply_paths([_rep("measured", "a"), _rep("measured", "a")], model_driven=True, variation="scenario_id", min_replications=2)
    assert one["single_path"] is True and "effectively one sample" in one["reading"] and one["min_replications_against_distinct_paths"] == "not met"
    lone = cell_reply_paths([_rep("measured", "a")], model_driven=True, variation="scenario_id", min_replications=None)
    assert lone["single_path"] is False, "one replication is not a cell whose replications share a path"
    missing = cell_reply_paths([_rep("measured", "a"), {"status": "measured", "raw": {}}], model_driven=True, variation="scenario_id", min_replications=None)
    assert "1 measured without a recorded first reply" in missing["reading"]
    scripted = cell_reply_paths([_rep("measured", None)], model_driven=False, variation="scenario_id", min_replications=20)
    assert scripted["distinct_first_reply_paths"] is None and scripted["single_path"] is False


def test_every_workload_declares_its_variation_source_and_an_undeclared_one_is_refused(tmp_path):
    from mark_platform.workloads import VARIATION_SOURCES, default_path, load

    assert all(w["variation"] in VARIATION_SOURCES for w in load().values())
    doc = yaml.safe_load(default_path().read_text(encoding="utf-8"))
    del doc["workloads"][0]["variation"]
    p = tmp_path / "workloads.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="missing variation"):
        load(p)
    doc["workloads"][0]["variation"] = "timestamp"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="not a declared source"):
        load(p)


def test_the_run_records_first_replies_distinct_paths_and_the_serving_condition(tmp_path):
    from mark_platform.runner import calibrate, close_run, open_run, run_cell

    ctx = open_run(tmp_path / "run", "paths", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        res = run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    assert "first_reply" in res["per_replication"][0]["raw"]["model"]
    assert res["reply_paths"]["model_driven"] is False and res["reply_paths"]["variation"] == "scenario_id"
    results = json.loads((tmp_path / "run" / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "run" / "manifest.unsigned.json").read_text(encoding="utf-8"))
    assert "serving_concurrency" in results and results["model_proxy"]["capture_first_exchange"] is True
    assert "condition" in manifest["pins"]["serving"] and "concurrency_in_effect" in manifest["pins"]["serving"]
