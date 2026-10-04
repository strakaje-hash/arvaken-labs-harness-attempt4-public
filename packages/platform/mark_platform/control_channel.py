"""The control channel: how the harness reaches the control that guards an agent.

The control runs INSIDE the agent process (that is where every open-source control in this pass lives: a
KillSwitch object, a LangGraph interrupt, a Conversation.pause()). The harness talks to it over a loopback
HTTP listener the adapter starts before the agent begins:

  POST /halt      {"reason": ...}   -> the control's declared halt mechanism is invoked; returns what it did
  POST /resume                       -> the control's resume mechanism (ks.resume)
  POST /inject    {"instruction":..} -> the harness tries to make the agent act after a halt (ks.mechanism)
  GET  /status                       -> {"halted": bool, "acting": bool, "mechanism": str, "control": str}

The harness stamps `halt_command_at` (monotonic) IMMEDIATELY BEFORE sending the request; the adapter stamps
`halt_received_at` on receipt. Both go into the span and the result. The channel is loopback-only.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import httpc, telemetry
from .clock import mono_ns, wall_ns

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class ControlListener:
    def __init__(self, handlers: dict[str, Handler], host: str = "127.0.0.1", port: int = 0) -> None:
        self.handlers = handlers
        listener = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, code: int, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/status" and "status" in listener.handlers:
                    return self._send(200, listener.handlers["status"]({}))
                return self._send(404, {"error": "unknown"})

            def do_POST(self):
                received = {"received_mono_ns": mono_ns(), "received_wall_ns": wall_ns()}
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                name = self.path.strip("/")
                fn = listener.handlers.get(name)
                if not fn:
                    return self._send(404, {"error": f"no handler {name}"})
                with telemetry.span_from_headers(dict(self.headers), f"control.{name}", {"mark.control.received_mono_ns": received["received_mono_ns"]}):
                    try:
                        out = fn({**body, **received})
                    except Exception as e:  # noqa: BLE001
                        out = {"error": f"{type(e).__name__}: {e}"}
                return self._send(200, {**received, **out})

        self.server = ThreadingHTTPServer((host, port), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name="control-listener", daemon=True)

    def start(self) -> "ControlListener":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ControlClient:
    """Harness side. `halt()` returns the command stamp taken before the request left this process."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    # Control-plane calls never reuse a pooled connection: the listener (agent process) and the gateway live for one
    # scenario, their ports come back into use, and a keep-alive connection held in the process-wide pool to a
    # server that has since gone away is a read error on the next scenario (the flake in the pipeline suite,
    # 2026-09-12). A fresh connection per command costs a loopback connect, which is not on any measured path:
    # the command stamp is taken before the request and the receipt stamp inside the listener.
    NO_REUSE = {"Connection": "close"}

    def _post(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {**telemetry.current_headers(), **self.NO_REUSE}
        r = httpc.client().post(f"{self.base_url}/{name}", json=body, headers=headers, timeout=30.0)
        r.raise_for_status()
        return r.json()

    def halt(self, reason: str = "harness") -> dict[str, Any]:
        with telemetry.span("harness.halt_command") as s:
            command = telemetry.event("halt_command_at")
            s.set_attribute("mark.halt_command_mono_ns", command["mono_ns"])
            out = self._post("halt", {"reason": reason, "command_mono_ns": command["mono_ns"]})
            s.set_attribute("mark.halt_received_mono_ns", out.get("received_mono_ns", -1))
        # when the halt call returned to the harness: a control whose halt takes longer than a hold makes the hold
        # meaningless for it, and the replication records that (ks.resume v3)
        return {"halt_command_at": command, "response": out, "returned_mono_ns": mono_ns()}

    def resume(self) -> dict[str, Any]:
        with telemetry.span("harness.resume_command"):
            command = telemetry.event("resume_command_at")
            return {"resume_command_at": command, "response": self._post("resume", {"command_mono_ns": command["mono_ns"]})}

    def inject(self, instruction: str, timeout_s: float | None = None) -> dict[str, Any]:
        """Never raises (founder ruling 2026-09-12): the command stamp is always kept, and a channel that times out or
        fails yields a named state (turn_timeout, inject_failed) instead of an exception that drops the stamp."""
        import httpx

        from .inject_states import CHANNEL_MARGIN_S, TURN_BOUND_S, inject_failed, turn_timeout

        timeout = timeout_s if timeout_s is not None else TURN_BOUND_S + CHANNEL_MARGIN_S
        with telemetry.span("harness.inject"):
            command = telemetry.event("inject_at")
            try:
                headers = {**telemetry.current_headers(), **self.NO_REUSE}
                r = httpc.client().post(f"{self.base_url}/inject", json={"instruction": instruction, "command_mono_ns": command["mono_ns"]}, headers=headers, timeout=timeout)
                r.raise_for_status()
                resp = r.json()
            except httpx.TimeoutException:
                resp = turn_timeout(timeout, "no answer from the agent process within the channel timeout")
            except Exception as e:  # noqa: BLE001
                resp = inject_failed(e)
            return {"inject_at": command, "response": resp, "response_received_mono_ns": mono_ns(), "channel_timeout_s": timeout}

    def continue_(self, text: str, timeout_s: float | None = None) -> dict[str, Any]:
        """A harness continuation (founder ruling 2026-09-13). Never raises, like inject: the send stamp is always kept."""
        import httpx

        from .inject_states import CHANNEL_MARGIN_S, TURN_BOUND_S, inject_failed, turn_timeout

        timeout = timeout_s if timeout_s is not None else TURN_BOUND_S + CHANNEL_MARGIN_S
        with telemetry.span("harness.continuation"):
            command = telemetry.event("continuation_at")
            try:
                headers = {**telemetry.current_headers(), **self.NO_REUSE}
                r = httpc.client().post(f"{self.base_url}/continue", json={"text": text, "command_mono_ns": command["mono_ns"]}, headers=headers, timeout=timeout)
                r.raise_for_status()
                resp = r.json()
            except httpx.TimeoutException:
                resp = turn_timeout(timeout, "no answer from the agent process within the channel timeout")
            except Exception as e:  # noqa: BLE001
                resp = inject_failed(e)
            return {"continuation_at": command, "response": resp, "response_received_mono_ns": mono_ns(), "channel_timeout_s": timeout}

    def status(self) -> dict[str, Any]:
        r = httpc.client().get(f"{self.base_url}/status", headers=self.NO_REUSE, timeout=10.0)
        r.raise_for_status()
        return r.json()
