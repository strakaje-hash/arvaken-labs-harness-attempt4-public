"""The harness's own manifest of permitted outbound calls (attempt 4, C2).

The harness makes a few calls on its own account -- the provider's identity endpoints, the model server's metadata and
metrics -- beside the run's own components on loopback. Every such call is declared in harness/permitted-calls.json before it
is made: `call(id, ...)` is the only path, an id not in the declaration is refused before a socket opens, only read-only
calls can be declared, and every call made is appended to <run_dir>/harness-calls.jsonl and counted, so the manifest can
say what the instrument reached for. The declaration is hashed and the hash pinned in the manifest.

Founder ruling 2026-09-21: "the identity call is on the instrument's own manifest of permitted calls -- read-only, declared,
refused if absent -- so C2 doesn't become the first thing on the pod that reaches out without saying so."
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from mark_ledger.canonical import sha256_hex
from mark_timing import mono_ns

DECLARATION_SCHEMA = "mark.permitted-calls/2"
# /2 (2026-09-21) adds `phase` and the `provision` section. /1 claimed "every outbound call the harness makes"
# while five went around it entirely -- the image build's four fetches and the model download -- a completeness
# claim true of the Python runtime and false of the pod.
PHASES = ("run", "model_fetch", "provision")
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DECLARATION = REPO_ROOT / "harness" / "permitted-calls.json"
CREDENTIALS = ("none", "instance_role")
LOG_MODES = ("each", "count")


class CallNotPermitted(PermissionError):
    """An outbound call the declaration does not list, or one whose target is not the declared one. Raised before any socket."""


class DeclarationError(ValueError):
    pass


@dataclass(frozen=True)
class Declaration:
    version: int
    sha256: str
    source: str
    calls: dict[str, dict[str, Any]]
    provision: dict[str, Any] = field(default_factory=dict)

    def ref(self) -> dict[str, Any]:
        return {"version": self.version, "sha256": self.sha256, "ids": sorted(self.calls),
                "provision_frozen_by": self.provision.get("frozen_by"), "provision_fetches": len(self.provision.get("fetches") or [])}

    def routed(self) -> dict[str, dict[str, Any]]:
        """The calls `call()` will actually issue: run-phase and routed. Everything else is declared for the inventory."""
        return {k: c for k, c in self.calls.items() if c.get("routed", True) and c.get("phase", "run") == "run"}


def _validate(body: dict[str, Any]) -> None:
    if body.get("schema") != DECLARATION_SCHEMA:
        raise DeclarationError(f"not a permitted-calls declaration: {body.get('schema')!r}")
    if not isinstance(body.get("version"), int) or body["version"] < 1 or not body.get("why"):
        raise DeclarationError("a declaration has an integer version >= 1 and a why")
    prov = body.get("provision")
    if not isinstance(prov, dict) or not prov.get("frozen_by") or not isinstance(prov.get("fetches"), list) or not prov["fetches"]:
        raise DeclarationError("a declaration names the provision phase: where it is built, what freezes it (frozen_by) and what it fetches")
    for f in prov["fetches"]:
        if not isinstance(f, dict) or not f.get("id") or not f.get("what") or not f.get("verified"):
            raise DeclarationError(f"provision fetch {f!r}: every one names what it is and what it is verified against, even when that is 'none'")
        if not f.get("pin"):
            raise DeclarationError(f"provision fetch {f.get('id')!r}: state the pin, or the string 'none' -- an omitted pin reads as a pin")
    seen: set[str] = set()
    for c in body.get("calls") or []:
        cid = c.get("id")
        if not cid or cid in seen:
            raise DeclarationError(f"every call has a unique id, got {cid!r}")
        seen.add(cid)
        for k in ("purpose", "method", "host", "path", "since"):
            if not c.get(k):
                raise DeclarationError(f"{cid}: missing {k}")
        if c.get("read_only") is not True:
            # v1 of the declaration cannot express a write: the harness has no business making one
            raise DeclarationError(f"{cid}: only read-only calls can be declared (read_only must be true)")
        if c["method"] not in ("GET", "HEAD") and not c.get("read_only_basis"):
            raise DeclarationError(f"{cid}: a {c['method']} declared read-only states its basis (read_only_basis)")
        if c.get("credentials", "none") not in CREDENTIALS:
            raise DeclarationError(f"{cid}: credentials must be one of {CREDENTIALS}")
        if c.get("log", "each") not in LOG_MODES:
            raise DeclarationError(f"{cid}: log must be one of {LOG_MODES}")
        if c.get("phase", "run") not in PHASES:
            raise DeclarationError(f"{cid}: phase must be one of {PHASES}, got {c.get('phase')!r}")
        if c.get("phase") == "model_fetch" and not c.get("verified"):
            raise DeclarationError(f"{cid}: a boot-time fetch names what it is verified against")
        if c.get("routed", True) is False and not c.get("routed_note"):
            raise DeclarationError(f"{cid}: a call the gate cannot route says so (routed_note)")
    if not seen:
        raise DeclarationError("a declaration lists at least one call")


def load_declaration(path: str | Path | None = None) -> Declaration:
    p = Path(path) if path else DEFAULT_DECLARATION
    raw = p.read_bytes()
    body = json.loads(raw.decode("utf-8"))
    _validate(body)
    return Declaration(version=int(body["version"]), sha256=sha256_hex(raw), source=str(p), calls={c["id"]: dict(c) for c in body["calls"]},
                       provision=dict(body["provision"]))


def _host_matches(declared: str, host: str) -> bool:
    if declared.startswith("<") and declared.endswith(">"):
        return True   # a host the run configures (the model server, the collector); the declaration names its role, the run its address
    return fnmatchcase(host.lower(), declared.lower())


def _path_matches(declared: str, path: str) -> bool:
    return fnmatchcase(path, declared)


@dataclass
class CallGate:
    """The one door. Holds the declaration and the run's call log; `call` refuses, then requests, then records."""
    declaration: Declaration
    log_path: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self, call_id: str, method: str, host: str, path: str) -> dict[str, Any]:
        """Refuse before any socket: the id must be declared, routed, and the request must be the declared one."""
        c = self.declaration.calls.get(call_id)
        if c is None:
            raise CallNotPermitted(f"{call_id}: not in the harness's permitted-calls declaration ({self.declaration.source}); the harness makes no call it has not declared")
        if c.get("routed", True) is False:
            raise CallNotPermitted(f"{call_id}: declared as a call the gate cannot route; it is not made through call()")
        if c.get("phase", "run") != "run":
            raise CallNotPermitted(f"{call_id}: declared in the {c.get('phase')} phase, which happens in the image build or at boot, not through call()")
        if method.upper() != c["method"]:
            raise CallNotPermitted(f"{call_id}: declared {c['method']}, asked {method.upper()}")
        if not _host_matches(c["host"], host) or not _path_matches(c["path"], path):
            raise CallNotPermitted(f"{call_id}: declared for {c['method']} {c['host']}{c['path']}, asked {host}{path}")
        return c

    def record(self, call_id: str, *, method: str, url: str, status: int | None, response_sha256: str | None, error: str | None, started: int, ended: int) -> None:
        c = self.declaration.calls[call_id]
        with self._lock:
            self.counts[call_id] = self.counts.get(call_id, 0) + 1
            n = self.counts[call_id]
        if self.log_path is None or (c.get("log", "each") == "count" and n > 1):
            return
        entry = {"call": call_id, "method": method, "url": url, "status": status, "response_sha256": response_sha256, "error": error, "started_mono_ns": started, "ended_mono_ns": ended, "n": n,
                 **({"logged": "first of a counted call; later ones are counted in the manifest"} if c.get("log") == "count" else {})}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    def call(self, call_id: str, method: str, url: str, *, headers: dict[str, str] | None = None, content: bytes | None = None, timeout_s: float = 2.0) -> "CallResult":
        """Issue a declared call. Any error is returned, never raised, except CallNotPermitted, which is raised before any socket."""
        from urllib.parse import urlsplit

        import httpx

        u = urlsplit(url)
        self.check(call_id, method, u.hostname or "", u.path or "/")
        started = mono_ns()
        try:
            r = httpx.request(method.upper(), url, headers=headers, content=content, timeout=timeout_s)
            ended = mono_ns()
            self.record(call_id, method=method.upper(), url=url, status=r.status_code, response_sha256=sha256_hex(r.content), error=None, started=started, ended=ended)
            return CallResult(call_id=call_id, ok=200 <= r.status_code < 300, status=r.status_code, body=r.content, headers=dict(r.headers), error=None, started=started, ended=ended)
        except httpx.HTTPError as e:
            ended = mono_ns()
            err = f"{type(e).__name__}: {e}"[:300]
            self.record(call_id, method=method.upper(), url=url, status=None, response_sha256=None, error=err, started=started, ended=ended)
            return CallResult(call_id=call_id, ok=False, status=None, body=b"", headers={}, error=err, started=started, ended=ended)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            made = dict(sorted(self.counts.items()))
        return {"declaration": self.declaration.ref(), "made": made, "log": str(self.log_path) if self.log_path else None}


@dataclass(frozen=True)
class CallResult:
    call_id: str
    ok: bool
    status: int | None
    body: bytes
    headers: dict[str, str]
    error: str | None
    started: int
    ended: int

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")
