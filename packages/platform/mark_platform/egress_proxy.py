"""Tier B userspace egress allowlist proxy (Task 3.2). The agent's HTTP_PROXY / HTTPS_PROXY point here; the
proxy forwards only to allowed host:port pairs (vLLM, the mock world, the collector, the MCP tool server) and
answers 403 for everything else, recording the attempt. Plain HTTP requests are re-issued to the origin;
CONNECT tunnels are bridged. Best effort by construction: an agent with shell access can unset the proxy
environment, which is why every Tier B run is labelled egress_control=best_effort and the attempts log is
part of the evidence, not a guarantee.

  python -m mark_platform.egress_proxy --port 3128 --allow 127.0.0.1:8000 --allow 127.0.0.1:8081 --log /root/runs/<run>/egress.jsonl
"""
from __future__ import annotations

import argparse
import http.client
import json
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .clock import mono_ns, wall_ns


class EgressLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, entry: dict[str, Any]) -> None:
        entry = {**entry, "mono_ns": mono_ns(), "wall_ns": wall_ns()}
        with self._lock:
            self.entries.append(entry)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, sort_keys=True) + "\n")


def make_handler(allow: set[tuple[str, int]], log: EgressLog):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _allowed(self, host: str, port: int) -> bool:
            h = "127.0.0.1" if host in ("localhost", "::1") else host
            return (h, port) in allow

        def _deny(self, host: str, port: int, method: str) -> None:
            log.record({"decision": "deny", "host": host, "port": port, "method": method, "scenario_id": self.headers.get("X-Scenario-Id")})
            body = json.dumps({"error": "egress denied by allowlist", "host": host, "port": port}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_CONNECT(self):
            host, _, port_s = self.path.rpartition(":")
            port = int(port_s or 443)
            if not self._allowed(host, port):
                return self._deny(host, port, "CONNECT")
            log.record({"decision": "allow", "host": host, "port": port, "method": "CONNECT"})
            try:
                upstream = socket.create_connection((host, port), timeout=30)
            except OSError as e:
                self.send_error(502, str(e))
                return
            self.send_response(200, "Connection established")
            self.end_headers()
            conns = [self.connection, upstream]
            try:
                while True:
                    r, _, x = select.select(conns, [], conns, 60)
                    if x or not r:
                        break
                    for s in r:
                        data = s.recv(65536)
                        if not data:
                            return
                        (upstream if s is self.connection else self.connection).sendall(data)
            finally:
                upstream.close()

        def _forward(self):
            u = urlsplit(self.path)
            host, port = u.hostname or "", u.port or (443 if u.scheme == "https" else 80)
            # the request body is read before any answer, a denial included: closing a socket with unread data
            # resets it and the agent would see a connection error instead of the 403 that names the decision
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            if not self._allowed(host, port):
                return self._deny(host, port, self.command)
            # The hop rule (hop.py; attempt 4 A2 and A3). This proxy is the first harness-owned process every agent call reaches
            # on Tier B, so its arrival is the receipt of record and its socket peer is the process behind the call; both are
            # forwarded under names a downstream hop trusts only from the harness itself. CONNECT tunnels (do_CONNECT) carry
            # no headers and stay unattributed by construction.
            from .hop import receive as _hop_receive

            headers, hop_facts = _hop_receive({k: v for k, v in self.headers.items() if k.lower() not in ("proxy-connection", "connection")},
                                              client_port=self.client_address[1], server_port=self.server.server_address[1], hop="egress")
            log.record({"decision": "allow", "host": host, "port": port, "method": self.command, "path": u.path,
                        "hop": {k: hop_facts[k] for k in ("peer_pid", "peer_resolution", "peer_is_harness", "dropped_supplied", "pid", "arrived_mono_ns", "attribution")}})
            try:
                conn = http.client.HTTPConnection(host, port, timeout=120)
                conn.request(self.command, (u.path or "/") + (("?" + u.query) if u.query else ""), body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except OSError as e:
                self.send_error(502, str(e))
                return
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = do_OPTIONS = _forward

    return H


class EgressProxy:
    def __init__(self, allow: list[str], log_path: Path | None = None, host: str = "127.0.0.1", port: int = 0) -> None:
        pairs: set[tuple[str, int]] = set()
        for a in allow:
            h, _, p = a.rpartition(":")
            pairs.add(("127.0.0.1" if h in ("localhost", "::1") else h, int(p)))
        self.allow = pairs   # live: the handler reads this set, so a per-scenario gateway port can be admitted and withdrawn
        self.log = EgressLog(log_path)
        self.server = ThreadingHTTPServer((host, port), make_handler(pairs, self.log))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, name="egress-proxy", daemon=True)

    def start(self) -> "EgressProxy":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def allow_add(self, host: str, port: int) -> None:
        self.allow.add(("127.0.0.1" if host in ("localhost", "::1") else host, int(port)))

    def allow_remove(self, host: str, port: int) -> None:
        self.allow.discard(("127.0.0.1" if host in ("localhost", "::1") else host, int(port)))

    def denied(self) -> list[dict[str, Any]]:
        return [e for e in self.log.entries if e.get("decision") == "deny"]

    def env(self) -> dict[str, str]:
        return {"HTTP_PROXY": self.url, "HTTPS_PROXY": self.url, "http_proxy": self.url, "https_proxy": self.url, "NO_PROXY": "", "no_proxy": ""}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=3128)
    p.add_argument("--allow", action="append", default=[])
    p.add_argument("--log")
    a = p.parse_args(argv)
    px = EgressProxy(a.allow, Path(a.log) if a.log else None, port=a.port).start()
    print(px.url, flush=True)
    px.thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
