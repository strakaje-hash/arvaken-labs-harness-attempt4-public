"""One HTTP client per process. `httpx.post(...)` builds a new Client (and a new TLS context) on every call,
which cost 200-800 ms per request on the authoring machine and would have been counted as control latency.
Loopback services only; no TLS is ever used here, so verification is irrelevant but left on."""
from __future__ import annotations

import threading

import httpx

_lock = threading.Lock()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=30.0, http2=False, limits=httpx.Limits(max_keepalive_connections=20, max_connections=50))
        return _client
