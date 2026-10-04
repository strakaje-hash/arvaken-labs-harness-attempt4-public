"""The serving condition (founder ruling 2026-09-12, after the Q3 replays). On vLLM 0.29.0, max_num_seqs alone changed the
token path for 3 of 10 captured prompts at concurrency 1, and the flags as declared could not say what applied when a
default did. The effective condition comes from the engine's own startup log; a value the server did not state is
recorded as not stated, never filled in; the concurrency in effect comes from /metrics."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mark_platform import serving

# the three lines the parser reads, in the shape vLLM 0.29.0 wrote them on the reproduction pod (trimmed)
LOG = ("(APIServer pid=1335) INFO 09-12 22:43:11 [api_utils.py:286] non-default args: {{'model_tag': '/snap/', 'enable_auto_tool_choice': True, 'tool_call_parser': 'hermes', "
       "'host': '127.0.0.1', 'seed': 7, 'max_model_len': 8192, 'served_model_name': ['Qwen/Qwen2.5-7B-Instruct-AWQ'], 'gpu_memory_utilization': 0.85{extra}}}\n"
       "(APIServer pid=1335) INFO 09-12 22:43:21 [scheduler.py:277] Chunked prefill is enabled with max_num_batched_tokens=8192.\n"
       "(EngineCore pid=1908) INFO 09-12 22:43:30 [core.py:123] Initializing a V1 LLM engine (v0.29.0) with config: model='/snap/', dtype=torch.float16, seed=7, "
       "enforce_eager=False, enable_prefix_caching={caching}, compilation_config={{'mode': 3, 'cudagraph_mode': <CUDAGraphMode.FULL_AND_PIECEWISE: (2, 1)>, "
       "'cudagraph_capture_sizes': [{sizes}], 'max_cudagraph_capture_size': 2}}\n")


def test_a_default_max_num_seqs_is_recorded_as_not_stated_and_the_condition_is_incomplete():
    f = serving.engine_log_facts(LOG.format(extra="", caching="True", sizes="1, 2, 4, 8"))
    assert f["read"] and f["engine_version"] == "0.29.0" and f["seed"] == 7 and f["enforce_eager"] is False and f["enable_prefix_caching"] is True
    assert f["chunked_prefill"] is True and f["max_num_batched_tokens"] == 8192
    assert f["cudagraph_mode"] == "FULL_AND_PIECEWISE" and f["cudagraph_capture_sizes"] == [1, 2, 4, 8]
    assert f["max_num_seqs"] is None and f["max_num_seqs_source"].startswith("not stated by the server")
    cond = serving.serving_condition({"engine_log": f})
    assert cond["stated"] is False and cond["missing"] == ["max_num_seqs"]


def test_an_explicit_max_num_seqs_and_caching_off_are_stated_and_the_capture_set_is_part_of_the_condition():
    a = serving.serving_condition({"engine_log": serving.engine_log_facts(LOG.format(extra=", 'max_num_seqs': 1024", caching="False", sizes="1, 2, 4, 8"))})
    b = serving.serving_condition({"engine_log": serving.engine_log_facts(LOG.format(extra=", 'max_num_seqs': 1024", caching="False", sizes="1, 2"))})
    assert a["stated"] and a["max_num_seqs"] == 1024 and a["max_num_seqs_source"] == "non-default args" and a["enable_prefix_caching"] is False
    assert a["hash"] and a["hash"] != b["hash"], "two servers that captured different graph sizes are two conditions"


def test_the_most_recent_server_start_in_the_log_is_the_one_read():
    text = LOG.format(extra="", caching="True", sizes="1, 2") + LOG.format(extra=", 'max_num_seqs': 1", caching="False", sizes="1, 2")
    f = serving.engine_log_facts(text)
    assert f["server_starts_in_log"] == 2 and f["max_num_seqs"] == 1 and f["enable_prefix_caching"] is False
    assert serving.engine_log_facts("no server here")["read"] is False


def test_the_new_switches_parse_and_the_record_reads_the_log_named_by_the_environment(monkeypatch, tmp_path):
    assert serving.parse_serve_args("--max-num-seqs 1024 --no-enable-prefix-caching") == {"max_num_seqs": 1024, "enable_prefix_caching": False}
    assert serving.read_engine_log(None)["read"] is False and serving.read_engine_log(str(tmp_path / "missing.log"))["read"] is False
    log = tmp_path / "vllm.log"
    log.write_text(LOG.format(extra=", 'max_num_seqs': 1024", caching="False", sizes="1, 2"), encoding="utf-8")
    monkeypatch.setenv("MARK_SERVING_LOG", str(log))
    monkeypatch.setattr(serving, "serve_cmdlines", lambda: [])
    monkeypatch.setattr(serving, "live_server_facts", lambda url, **kw: {"reachable": False})   # C2: the record hands the call gate through
    rec = serving.serving_record("http://127.0.0.1:9/v1", "M")
    assert rec["engine_log"]["sha256"] and rec["engine_log"]["path"] == str(log)
    pin = serving.serving_pin(rec)
    assert pin["condition"]["stated"] and pin["condition"]["max_num_seqs"] == 1024 and pin["condition"]["cudagraph_capture_sizes"] == [1, 2]


@pytest.fixture
def metrics_server():
    state = {"running": 3.0, "status": 200, "name": "vllm:num_requests_running"}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (f"# HELP {state['name']} x\n{state['name']}{{engine=\"0\",model_name=\"M\"}} {state['running']}\n"
                    f"vllm:num_requests_waiting{{engine=\"0\",model_name=\"M\"}} 0.0\n").encode()
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", state
    srv.shutdown()


def _sample(url, until):
    s = serving.ServingSampler(url, interval_s=0.01).start()
    deadline = time.monotonic() + 10
    while not until(s) and time.monotonic() < deadline:
        time.sleep(0.02)
    return s.stop()


def test_the_sampler_records_the_concurrency_in_effect_and_counts_failed_samples(metrics_server):
    url, state = metrics_server
    ok = _sample(url, lambda s: s.samples >= 3)
    assert ok["observed"] and ok["max_running"] == 3 and ok["mean_running_while_busy"] == 3.0 and ok["samples_by_running"] == {"3": ok["samples"]}
    assert ok["request_gauges_seen"] == ["vllm:num_requests_running", "vllm:num_requests_waiting"] and ok["metric_absent_samples"] == 0
    # an engine that names the gauge differently: recorded by name, never read as zero concurrency
    state["name"] = "vllm:num_requests_active"
    renamed = _sample(url, lambda s: s.metric_absent >= 2)
    assert renamed["observed"] is False and renamed["max_running"] is None and "vllm:num_requests_active" in renamed["request_gauges_seen"]
    state["status"] = 404
    bad = _sample(url, lambda s: s.failures >= 2)
    assert bad["observed"] is False and bad["failed_samples"] >= 2 and bad["max_running"] is None
    json.dumps(bad)
