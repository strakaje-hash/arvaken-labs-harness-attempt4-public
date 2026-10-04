"""Serving parameters pinned with every run (founder ruling 2026-09-12). Decisive attempt 2 was served with max_model_len
8192 and the hermes tool-call parser, and no manifest recorded either; on the single-call variant the OpenHands
conversation reached that window. The record takes three independent sources (declared, the running process, the live
server), keeps a disagreement visible, pins what shaped the model's output, and the targets' request parameters come
from the one source the record pins."""
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mark_platform import serving

MODEL = "Qwen/Qwen2.5-7B-Instruct-AWQ"
ARGS = "--max-model-len 8192 --enable-auto-tool-choice --tool-call-parser hermes --seed 7 --gpu-memory-utilization 0.85"


def _process(args: str) -> list[str]:
    return ["/opt/mark/vllm-venv/bin/python3", "/opt/mark/vllm-venv/bin/vllm", "serve", "/snap", "--served-model-name", MODEL, *args.split()]


@pytest.fixture
def fake_vllm():
    class H(BaseHTTPRequestHandler):
        live_len = 8192

        def do_GET(self):
            if self.path == "/v1/models":
                body = {"data": [{"id": MODEL, "root": "/snap", "max_model_len": H.live_len}]}
            elif self.path == "/version":
                body = {"version": "0.11.0"}
            else:
                self.send_response(404)
                self.end_headers()
                return
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", H
    srv.shutdown()


def test_serve_arguments_are_parsed_into_the_parameters_that_shape_output():
    p = serving.parse_serve_args(ARGS + " --served-model-name=M")
    assert p == {"max_model_len": 8192, "enable_auto_tool_choice": True, "tool_call_parser": "hermes", "seed": 7, "gpu_memory_utilization": 0.85, "served_model_name": "M"}


def test_the_record_takes_declared_process_and_live_and_pins_what_they_agree_on(monkeypatch, fake_vllm):
    url, _ = fake_vllm
    monkeypatch.setenv("MARK_SERVING_ARGS", ARGS)
    monkeypatch.setattr(serving, "serve_cmdlines", lambda: [_process(ARGS)])
    rec = serving.serving_record(url, MODEL)
    assert rec["max_model_len_sources"] == {"declared": 8192, "process": 8192, "live": 8192} and rec["consistent"]
    pin = serving.serving_pin(rec)
    assert pin["recorded"] and pin["max_model_len"] == 8192 and pin["tool_call_parser"] == "hermes" and pin["seed"] == 7 and pin["version"] == "0.11.0"
    assert pin["request_params_hash"] and pin["args_hash"]


def test_a_disagreement_between_sources_is_recorded_and_the_live_value_is_pinned(monkeypatch, fake_vllm):
    """A server started by an earlier run.sh can run with arguments the environment no longer declares."""
    url, handler = fake_vllm
    monkeypatch.setenv("MARK_SERVING_ARGS", ARGS.replace("8192", "32768"))
    monkeypatch.setattr(serving, "serve_cmdlines", lambda: [_process(ARGS)])
    rec = serving.serving_record(url, MODEL)
    assert rec["max_model_len_sources"] == {"declared": 32768, "process": 8192, "live": 8192} and rec["consistent"] is False
    pin = serving.serving_pin(rec)
    assert pin["max_model_len"] == 8192 and pin["max_model_len_consistent"] is False


def test_an_unreachable_server_and_no_process_are_recorded_as_such(monkeypatch):
    monkeypatch.delenv("MARK_SERVING_ARGS", raising=False)
    monkeypatch.setattr(serving, "serve_cmdlines", lambda: [])
    rec = serving.serving_record("http://127.0.0.1:9/v1", MODEL)
    assert rec["live"]["reachable"] is False and rec["live"]["error"]
    pin = serving.serving_pin(rec)
    assert pin["recorded"] is False and pin["max_model_len"] is None
    assert serving.serving_pin(None) == {"recorded": False, "reason": "no serving record in this run's environment"}


def test_the_process_table_is_read_for_the_serve_process_only(tmp_path):
    for pid, argv in (("101", _process(ARGS)), ("102", ["VLLM::EngineCore"]), ("103", ["python", "-m", "mark_platform.agentproc"]), ("self", ["x"])):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    found = serving.serve_cmdlines(tmp_path)
    assert found == [_process(ARGS)]
    assert serving.serve_cmdlines(tmp_path / "missing") == []


def test_the_run_records_serving_in_the_environment_and_pins_it_in_the_manifest(tmp_path):
    from mark_platform.runner import calibrate, close_run, open_run

    ctx = open_run(tmp_path / "run", "serving", llm_url="http://127.0.0.1:9/v1", llm_model=MODEL, tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    manifest = json.loads((tmp_path / "run" / "manifest.unsigned.json").read_text(encoding="utf-8"))
    results = json.loads((tmp_path / "run" / "results.json").read_text(encoding="utf-8"))
    assert "serving" in results["environment"] and results["environment"]["serving"]["request_params"] == serving.REQUEST_PARAMS
    assert "serving" in manifest["pins"] and "request_params_hash" in manifest["pins"]["serving"]
    # change-set B4/B5 (founder rulings 2026-09-15): every manifest pins the study sets' disjointness; the run records each subject's delta
    assert manifest["pins"]["study_sets"] == results["study_sets"] and results["study_sets"]["disjoint"] is True and results["study_sets"]["platform_demo"] == []
    assert sorted(results["enterprise_deltas"]) == ["agent-governance-toolkit", "langgraph", "openhands"]


def test_langgraph_ref_sends_the_pinned_request_parameters(monkeypatch, tmp_path):
    """LangChain and LangGraph are not installed on the laptop, so their modules are stood in for; what is checked
    is the adapter's call to the model client."""
    from unittest.mock import MagicMock

    from mark_platform.handle import AgentHandle
    from mark_platform.targets.langgraph_ref import LangGraphRefAgent

    seen = {}

    class FakeChat:
        def __init__(self, **kw):
            seen.update(kw)

        def bind_tools(self, tools):
            return self

    chat_mod = types.ModuleType("langchain_openai")
    chat_mod.ChatOpenAI = FakeChat
    monkeypatch.setitem(sys.modules, "langchain_openai", chat_mod)
    # every module the constructor path imports (inproc tools; the MCP client is not on this path)
    for name in ("langchain_core", "langchain_core.tools", "langchain_core.messages", "langgraph", "langgraph.checkpoint", "langgraph.checkpoint.memory", "langgraph.graph",
                 "langgraph.prebuilt", "langgraph.types"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    LangGraphRefAgent(AgentHandle("a", "s"), {"workdir": str(tmp_path), "scenario_id": "s", "tools_mode": "inproc", "mock_url": "http://127.0.0.1:9", "llm_model": MODEL, "llm_url": "http://127.0.0.1:9/v1"})
    rp = serving.REQUEST_PARAMS["langgraph-ref"]
    assert (seen["temperature"], seen["seed"], seen["timeout"], seen["max_retries"]) == (rp["temperature"], rp["seed"], rp["timeout_s"], rp["max_retries"])


def test_openhands_sends_the_pinned_request_parameters(monkeypatch, tmp_path):
    """The SDK is not installed on the laptop, so its modules are stood in for; what is checked is the adapter's call."""
    seen = {}

    class LLM:
        def __init__(self, **kw):
            seen.update(kw)

    class _Named:
        name = "tool"

    mods = {
        "openhands": types.ModuleType("openhands"), "openhands.sdk": types.ModuleType("openhands.sdk"),
        "openhands.sdk.observability": types.ModuleType("openhands.sdk.observability"), "openhands.sdk.observability.laminar": types.ModuleType("openhands.sdk.observability.laminar"),
        "openhands.tools": types.ModuleType("openhands.tools"), "openhands.tools.file_editor": types.ModuleType("openhands.tools.file_editor"),
        "openhands.tools.terminal": types.ModuleType("openhands.tools.terminal"),
    }
    mods["openhands.sdk"].LLM = LLM
    mods["openhands.sdk"].Agent = lambda **kw: object()
    mods["openhands.sdk"].Conversation = object
    mods["openhands.sdk"].Tool = lambda **kw: object()
    mods["openhands.sdk.observability"].laminar = mods["openhands.sdk.observability.laminar"]
    mods["openhands.tools.file_editor"].FileEditorTool = _Named
    mods["openhands.tools.terminal"].TerminalTool = _Named
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    from mark_platform.handle import AgentHandle
    from mark_platform.targets.openhands_sdk import OpenHandsAgent

    OpenHandsAgent(AgentHandle("a", "s"), {"workdir": str(tmp_path), "llm_model": MODEL, "llm_url": "http://127.0.0.1:9/v1"})
    rp = serving.REQUEST_PARAMS["openhands-sdk"]
    assert seen["temperature"] == rp["temperature"] and ("seed" in seen) == (rp["seed"] is not None)
