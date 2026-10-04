"""Fix A8 against the real client (pod only: langchain-openai has no win-arm64 wheel). After a revoke the LangGraph adapter
calls the unbound model (packages/platform/tests/test_revoke_no_empty_tools.py). Here: the unbound ChatOpenAI request
carries no tools field, so a server that rejects an empty tools array, as vLLM does, answers it."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

langchain_openai = pytest.importorskip("langchain_openai")
httpx = pytest.importorskip("httpx")


def test_the_unbound_model_sends_no_tools_field_and_a_server_rejecting_an_empty_array_answers_it():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            seen.append(body)
            if body.get("tools") == []:
                payload, code = {"error": {"message": "`tools` must not be an empty array. Either provide at least one tool or omit the field entirely."}}, 400
            else:
                payload, code = {"id": "x", "object": "chat.completion", "created": 0, "model": body.get("model", "m"),
                                 "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, 200
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        llm = langchain_openai.ChatOpenAI(model="m", base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="none", max_retries=0, timeout=10,
                                          http_client=httpx.Client(trust_env=False))   # never through the pod's egress proxy
        assert llm.invoke("hi").content == "ok"
        assert seen and "tools" not in seen[-1], seen[-1]
    finally:
        server.shutdown()
