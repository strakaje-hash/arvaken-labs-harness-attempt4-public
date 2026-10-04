"""The credential-holding gateway: the first out-of-process control (Task 3.1 / 5.3 control classes).

Runs in the HARNESS process (not the agent's), one per scenario, between every agent and the mock world. The
mock world only accepts calls that carry the run token; only the gateway holds it; the agent is given the
gateway's URL as its "mock world". Every tool call the agent makes (in-process tools, the MCP tool server, an
OpenHands `curl`) passes through here and is forwarded with the token and the agent's own headers (traceparent,
X-Scenario-Id, X-Mark-Dispatch-Ns) intact, so the mock world's record is unchanged.

Halt = revocation: the harness posts /halt to the gateway's control port. From that instant the gateway stops
forwarding; a call that arrives afterwards is refused (403) and logged with its dispatch and arrival stamps. An
agent with a shell cannot unset this: the credential was never in its process. What the gateway cannot do is
recall a request it had already forwarded before the halt; those land and are counted honestly (they were
dispatched before the halt, so they are pre_halt_delayed, not post_halt_landed).

Control class: reference_instrument (founder decision A10: the harness's own instrument, never an evaluated
control; rows informational by rule, rendered beside the control rows; source ships with the bundle).
Primitive: revoke (+ cancel of anything not yet forwarded).
"""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import httpc, telemetry
from .clock import mono_ns, wall_ns

HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "proxy-connection", "accept-encoding"}


class GatewayState:
    def __init__(self, upstream: str, token: str, log_path: Path | None) -> None:
        self.upstream = upstream.rstrip("/")
        self.token = token
        self.revoked = threading.Event()
        self.revoked_at_mono_ns: int | None = None
        self.lock = threading.Lock()
        self.decisions: list[dict[str, Any]] = []
        self.log_path = log_path
        self.halts = 0

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.decisions.append(entry)
            if self.log_path:
                with self.log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, sort_keys=True) + "\n")

    def denied(self) -> list[dict[str, Any]]:
        with self.lock:
            return [d for d in self.decisions if d.get("decision") == "deny"]


def _make_handler(state: GatewayState):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, headers: dict[str, str] | None = None) -> None:
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _control(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
            received = {"received_mono_ns": mono_ns(), "received_wall_ns": wall_ns()}
            if name == "halt":
                with telemetry.span("control.gateway.revoke", {"mark.control": "credential-gateway", "mark.primitive": "revoke"}):
                    state.revoked.set()
                    state.revoked_at_mono_ns = received["received_mono_ns"]
                    state.halts += 1
                return {**received, "control": "credential-gateway", "mechanism": "gateway", "primitive": "revoke", "acted": True, "control_class": "reference_instrument",
                        "reachable": {"stop": False, "cancel_inflight": True, "revoke": True}, "primitive_unreachable": False, "note": "credential revoked at the gateway; calls arriving after this instant are refused"}
            if name == "resume":
                state.revoked.clear()
                return {**received, "control": "credential-gateway", "acted": True, "note": "forwarding restored"}
            if name == "status":
                return {**received, "revoked": state.revoked.is_set(), "halts": state.halts, "denied": len(state.denied()), "forwarded": sum(1 for d in state.decisions if d.get("decision") == "allow")}
            return {**received, "error": f"unknown control {name}"}

        def _forward(self) -> None:
            path = self.path
            arrived = {"arrived_mono_ns": mono_ns(), "arrived_wall_ns": wall_ns()}
            if path.startswith("/_gw/"):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                with telemetry.span_from_headers(dict(self.headers), f"gateway.control.{path[5:]}", {"mark.service": "gateway"}):
                    out = self._control(path[5:], body)
                return self._send(200, json.dumps(out).encode(), {"Content-Type": "application/json"})
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if path.startswith("/_"):
                # the mock world's harness endpoints (/_reset, /_policy, /_calls) are never reachable through the
                # gateway: an agent behind it cannot change the world's policy or wipe its record
                return self._send(403, json.dumps({"error": "harness endpoint; not forwarded", "mock": True}).encode(), {"Content-Type": "application/json"})
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            d = headers.get("X-Mark-Dispatch-Ns") or headers.get("x-mark-dispatch-ns")
            # The hop rule (hop.py; attempt 4 A2 and A3): the call's first receipt by the harness and the process behind it,
            # forwarded from the egress proxy when this socket's peer is the harness process, resolved from this socket
            # otherwise; anything the agent supplied under those names is dropped and noted. The gateway's own arrival is
            # recorded beside it either way.
            from .hop import receive as _hop_receive

            headers, hop_facts = _hop_receive(headers, client_port=self.client_address[1], server_port=self.server.server_address[1], hop="gateway")
            entry = {"path": path, "method": self.command, "scenario_id": headers.get("X-Scenario-Id") or headers.get("x-scenario-id"), "dispatch_mono_ns": int(d) if d and d.isdigit() else None, **arrived,
                     "hop": {k: hop_facts[k] for k in ("peer_pid", "peer_resolution", "peer_is_harness", "trusted_upstream", "dropped_supplied", "pid", "first_hop", "arrived_mono_ns", "attribution")}}
            with telemetry.span_from_headers(dict(self.headers), "gateway.forward", {"mark.service": "gateway", "mark.gateway.path": path}) as s:
                if state.revoked.is_set():
                    entry.update({"decision": "deny", "reason": "revoked", "revoked_at_mono_ns": state.revoked_at_mono_ns})
                    state.record(entry)
                    s.set_attribute("mark.gateway.decision", "deny")
                    return self._send(403, json.dumps({"error": "credential revoked by the gateway", "mock": True}).encode(), {"Content-Type": "application/json"})
                entry["decision"] = "allow"
                state.record(entry)
                s.set_attribute("mark.gateway.decision", "allow")
                try:
                    r = httpc.client().request(self.command, state.upstream + path, content=raw, headers={**headers, "X-Mock-Token": state.token}, timeout=60.0)
                except Exception as e:  # noqa: BLE001
                    return self._send(502, json.dumps({"error": f"upstream: {type(e).__name__}: {e}"}).encode(), {"Content-Type": "application/json"})
                out_headers = {k: v for k, v in r.headers.items() if k.lower() not in HOP_HEADERS and k.lower() != "content-encoding"}
                return self._send(r.status_code, r.content, out_headers)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _forward

    return H


class Gateway:
    def __init__(self, upstream: str, token: str, log_path: Path | None = None, host: str = "127.0.0.1", port: int = 0) -> None:
        self.state = GatewayState(upstream, token, log_path)
        self.server = ThreadingHTTPServer((host, port), _make_handler(self.state))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self.control_url = f"{self.url}/_gw"
        self.thread = threading.Thread(target=self.server.serve_forever, name="credential-gateway", daemon=True)

    def start(self) -> "Gateway":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def decisions(self) -> list[dict[str, Any]]:
        with self.state.lock:
            return list(self.state.decisions)


def new_token() -> str:
    return secrets.token_hex(16)
