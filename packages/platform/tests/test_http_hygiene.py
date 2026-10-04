"""The pipeline suite flaked once with an httpx read error under load (2026-09-12). Two structural causes, both
closed here and pinned by these tests, which exercise the property itself (a socket, a header), not the config:

1. uvicorn closes an idle keep-alive connection after 5 s by default; the harness's pooled client and the gateway
   go quiet for longer than that between effects, and a server-side close racing a reuse is a read error. The
   world must never initiate an idle close during a run.
2. Control-plane calls (halt, inject, resume, status) go to servers that live for one scenario on ports that come
   back into use; a pooled keep-alive connection to a server that is gone is a read error on the next scenario.
   Those calls must never reuse a pooled connection.
"""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from mark_platform.control_channel import ControlClient
from mark_platform.scenario import MockWorld


def _http(sock: socket.socket, path: str) -> bytes:
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: mock\r\nConnection: keep-alive\r\n\r\n".encode())
    sock.settimeout(5.0)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return data
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    n = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            n = int(line.split(b":")[1])
    while len(rest) < n:
        rest += sock.recv(4096)
    return head + b"\r\n\r\n" + rest


def test_the_world_keeps_an_idle_keep_alive_connection_open_past_uvicorns_default(tmp_path):
    mock = MockWorld.start(tmp_path)
    try:
        host, port = mock.url.replace("http://", "").split(":")
        with socket.create_connection((host, int(port))) as s:
            first = _http(s, "/_health")
            assert first.startswith(b"HTTP/1.1 200")
            time.sleep(6.5)   # past uvicorn's default timeout_keep_alive of 5 s
            second = _http(s, "/_health")
            assert second.startswith(b"HTTP/1.1 200"), "the world closed an idle connection: a pooled client would read an error on reuse"
    finally:
        mock.stop()


def test_control_plane_calls_never_reuse_a_pooled_connection():
    seen: list[dict[str, str]] = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append({k.lower(): v for k, v in self.headers.items()})
            # read the request body before answering: a socket closed with unread data is reset (WinError 10053 /
            # ECONNRESET) and the client's read of the response fails. This fixture did not, and reproduced the
            # very error it was written to pin (third suite run, 2026-09-12).
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    srv = HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        c = ControlClient(f"http://127.0.0.1:{srv.server_address[1]}")
        c.status()
        c.halt("test")
        assert len(seen) == 2 and all(h.get("connection", "").lower() == "close" for h in seen), seen
    finally:
        srv.shutdown()
        srv.server_close()
