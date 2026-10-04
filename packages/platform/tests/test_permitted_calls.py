"""C2 (attempt 4): the harness makes no outbound call it has not declared; the declaration lists only read-only calls; every
call made is logged and counted."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mark_platform.permitted_calls import DEFAULT_DECLARATION, CallGate, CallNotPermitted, DeclarationError, load_declaration


@pytest.fixture
def server():
    hits = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _any(self):
            hits.append((self.command, self.path))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_PUT = do_POST = _any

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    srv.shutdown()


PROVISION = {"where": "test", "frozen_by": "the image digest", "fetches": [{"id": "image.base", "what": "base", "pin": "sha256:aa", "verified": "digest"}]}


def _decl(tmp_path, calls, version=2, provision=PROVISION):
    p = tmp_path / "permitted-calls.json"
    body = {"schema": "mark.permitted-calls/2", "version": version, "why": "test", "calls": calls}
    if provision is not None:
        body["provision"] = provision
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


GET_MODELS = {"id": "vllm.models", "purpose": "p", "method": "GET", "host": "<model server>", "path": "/v1/models", "read_only": True, "since": "t"}


def test_the_repo_declaration_loads_is_hashed_and_names_every_call_the_harness_makes():
    d = load_declaration()
    assert d.source == str(DEFAULT_DECLARATION) and len(d.sha256) == 64 and d.version >= 2
    assert set(d.calls) >= {"aws.imds.token", "aws.imds.identity_document", "aws.imds.identity_signature", "aws.imds.role_credentials", "aws.sts.get_caller_identity", "vllm.models", "vllm.version", "vllm.metrics", "otlp.export"}
    assert all(c["read_only"] is True for c in d.calls.values())
    assert d.calls["otlp.export"]["routed"] is False and d.calls["otlp.export"]["routed_note"]
    assert d.ref()["sha256"] == d.sha256 and "vllm.metrics" in d.ref()["ids"]


def test_the_inventory_covers_the_phases_that_went_around_the_gate_until_attempt_4():
    """/1 said "every outbound call the harness makes" while the image build's four fetches and the model download went
    around it entirely: true of the Python runtime, false of the pod. Each provision fetch states its pin and what it is
    verified against -- including where that is the distribution's signatures with no version pin, or a hash a person
    recorded, because those are different facts from "verified by the publisher"."""
    d = load_declaration()
    fetches = {f["id"]: f for f in d.provision["fetches"]}
    assert set(fetches) >= {"image.base", "image.apt", "image.cosign", "image.node", "image.uv", "image.otelcol", "image.tempo"}
    assert all(f.get("pin") and f.get("verified") for f in fetches.values())
    assert fetches["image.apt"]["pin"] == "none" and "not pinned" in fetches["image.apt"]["verified"]
    assert "FLOOR OF THE CHAIN" in fetches["image.cosign"]["verified"]
    assert "pinned identity" in fetches["image.otelcol"]["verified"] and "no .sha256" in fetches["image.otelcol"]["verified"]
    assert d.provision["boot_time_fetches"].startswith("none")
    # the model download is the one thing a pod fetches at boot, and it is declared with its verification
    model = d.calls["hf.model_snapshot"]
    assert model["phase"] == "model_fetch" and model["routed"] is False and "model-pins" in model["verified"]
    # and none of these is something call() will issue
    assert set(d.routed()) == set(d.calls) - {"otlp.export", "hf.model_snapshot"}


def test_an_undeclared_call_is_refused_before_any_socket_opens(server, tmp_path):
    base, hits = server
    gate = CallGate(load_declaration(_decl(tmp_path, [GET_MODELS])), tmp_path / "harness-calls.jsonl")
    with pytest.raises(CallNotPermitted, match="not in the harness's permitted-calls declaration"):
        gate.call("vllm.metrics", "GET", base + "/metrics")
    # the declared id with the wrong method, or a path the declaration does not name, is the same refusal
    with pytest.raises(CallNotPermitted, match="declared GET, asked POST"):
        gate.call("vllm.models", "POST", base + "/v1/models")
    with pytest.raises(CallNotPermitted, match="declared for GET"):
        gate.call("vllm.models", "GET", base + "/v1/models/extra")
    assert hits == [] and not (tmp_path / "harness-calls.jsonl").exists()   # the listener saw nothing; nothing was logged
    assert gate.summary()["made"] == {}


def test_a_declared_call_is_made_logged_and_counted(server, tmp_path):
    base, hits = server
    gate = CallGate(load_declaration(_decl(tmp_path, [GET_MODELS, {**GET_MODELS, "id": "vllm.metrics", "path": "/metrics", "log": "count"}])), tmp_path / "harness-calls.jsonl")
    r = gate.call("vllm.models", "GET", base + "/v1/models")
    assert r.ok and r.status == 200 and json.loads(r.text()) == {"ok": True} and r.ended >= r.started
    for _ in range(3):
        assert gate.call("vllm.metrics", "GET", base + "/metrics").ok
    assert hits == [("GET", "/v1/models")] + [("GET", "/metrics")] * 3
    lines = [json.loads(l) for l in (tmp_path / "harness-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    # 'each' logs every call; 'count' logs the first and counts the rest
    assert [l["call"] for l in lines] == ["vllm.models", "vllm.metrics"] and lines[1]["logged"].startswith("first of a counted call")
    assert lines[0]["status"] == 200 and len(lines[0]["response_sha256"]) == 64 and lines[0]["error"] is None
    assert gate.summary()["made"] == {"vllm.metrics": 3, "vllm.models": 1}


def test_a_failed_call_is_returned_with_its_error_and_still_logged(tmp_path):
    gate = CallGate(load_declaration(_decl(tmp_path, [GET_MODELS])), tmp_path / "harness-calls.jsonl")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]   # bound, never listening: the connection is refused
    r = gate.call("vllm.models", "GET", f"http://127.0.0.1:{port}/v1/models", timeout_s=1.0)
    assert not r.ok and r.status is None and r.error and r.body == b""
    line = json.loads((tmp_path / "harness-calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["error"] == r.error and line["status"] is None


@pytest.mark.parametrize("edit,msg", [
    (lambda c: c.update(read_only=False), "only read-only calls can be declared"),
    (lambda c: c.pop("read_only"), "only read-only calls can be declared"),
    (lambda c: c.update(method="POST"), "states its basis"),
    (lambda c: c.update(credentials="api_key"), "credentials must be one of"),
    (lambda c: c.update(log="never"), "log must be one of"),
    (lambda c: c.update(routed=False), "says so"),
    (lambda c: c.pop("since"), "missing since"),
])
def test_a_declaration_that_could_hide_a_write_or_an_unrouted_call_is_refused(tmp_path, edit, msg):
    c = dict(GET_MODELS)
    edit(c)
    with pytest.raises(DeclarationError, match=msg):
        load_declaration(_decl(tmp_path, [c]))


def test_a_call_the_gate_cannot_route_is_declared_but_refused_through_the_door(tmp_path):
    gate = CallGate(load_declaration(_decl(tmp_path, [{**GET_MODELS, "id": "otlp.export", "method": "POST", "read_only_basis": "b", "routed": False, "routed_note": "the SDK makes it"}])))
    with pytest.raises(CallNotPermitted, match="cannot route"):
        gate.call("otlp.export", "POST", "http://127.0.0.1:1/v1/traces")


def test_a_call_from_another_phase_is_refused_through_the_door_too(tmp_path):
    """Declared for the inventory, not for the gate: a provision or boot-time fetch does not happen through call()."""
    gate = CallGate(load_declaration(_decl(tmp_path, [{**GET_MODELS, "id": "hf.model_snapshot", "phase": "model_fetch", "verified": "the pin list", "host": "huggingface.co"}])))
    with pytest.raises(CallNotPermitted, match="declared in the model_fetch phase"):
        gate.call("hf.model_snapshot", "GET", "https://huggingface.co/v1/models")


@pytest.mark.parametrize("prov,msg", [
    (None, "names the provision phase"),
    ({"where": "w", "fetches": [{"id": "a", "what": "b", "pin": "c", "verified": "d"}]}, "names the provision phase"),
    ({"where": "w", "frozen_by": "d", "fetches": []}, "names the provision phase"),
    ({"where": "w", "frozen_by": "d", "fetches": [{"id": "a", "what": "b", "pin": "c"}]}, "verified against, even when that is"),
    ({"where": "w", "frozen_by": "d", "fetches": [{"id": "a", "what": "b", "verified": "d"}]}, "an omitted pin reads as a pin"),
])
def test_a_provision_section_that_omits_a_pin_or_a_verification_is_refused(tmp_path, prov, msg):
    with pytest.raises(DeclarationError, match=msg):
        load_declaration(_decl(tmp_path, [GET_MODELS], provision=prov))


def test_an_unknown_phase_is_refused(tmp_path):
    with pytest.raises(DeclarationError, match="phase must be one of"):
        load_declaration(_decl(tmp_path, [{**GET_MODELS, "phase": "whenever"}]))
    with pytest.raises(DeclarationError, match="names what it is verified against"):
        load_declaration(_decl(tmp_path, [{**GET_MODELS, "phase": "model_fetch"}]))
