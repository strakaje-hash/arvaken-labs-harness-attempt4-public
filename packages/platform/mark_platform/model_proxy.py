"""The model path's instrument (founder ruling 2026-09-12; constitution `model-integrity`).

The single-instrument principle applied to the model path. Until now the adapters reported what they believed the
model did. On decisive attempt 2 that hid a context window exhausted on OpenHands' single-call arm and replies whose
tool calls came back as unparsed text. This proxy sits between the agents and the model server and records, per
call and from one place, what the model actually returned.

- **Transparent.** It never alters a request or a response: bodies are forwarded byte for byte, and only hop-by-hop
  headers are regenerated. A test proves it, and every record carries the SHA-256 of both bodies.
- **Measured.** It sits in the agent's loop, so its own time (receipt to forward, and upstream answer to answer
  sent) is recorded per call and summarised per run.
- **One place agents go through.** Each agent's MARK_LLM_URL is `<proxy>/s/<scenario_id>/<upstream path>`, and the
  run's egress allowlist admits the proxy, not the model server. On Tier B that is the localhost / allowlist
  best-effort case: an agent that unsets its proxy variables can still reach the server directly, and the run is
  labelled so.
- **Capture.** The runner always captures the first exchange of every scenario (`capture_first`), because the
  replay-fidelity check re-sends it. Capture of every exchange (MARK_MODEL_CAPTURE=1) is off by default. Both are
  recorded.
- **Hashes that name a behaviour** (founder ruling 2026-09-12). `prompt_sha256` is taken over the request's canonical
  JSON without its per-conversation keys (`prompt_cache_key`), so two conversations sending the same prompt hash alike.
  `reply_path_sha256` is taken over the reply's canonical content, tool-call names and JSON-canonicalised arguments,
  and finish reason (reply_paths.HASH_RULE). Neither touches the bytes forwarded."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import statistics
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .clock import mono_ns
from .turns import HarnessTurns, TurnFile

HOP_BY_HOP = {"connection", "proxy-connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length"}
TOOL_CALL_MARKUP = "<tool_call>"
CONTEXT_ERROR_MARKERS = ("maximum context length", "context length", "contextwindowexceeded", "context_length_exceeded")
_SAFE_ID = re.compile(r"[^A-Za-z0-9._+-]")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


PER_CONVERSATION_KEYS = ("prompt_cache_key",)


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_hash(req: dict[str, Any]) -> str:
    return _sha256(_canon({k: v for k, v in req.items() if k not in PER_CONVERSATION_KEYS}).encode())


def _canon_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError:
            return {"unparsed_arguments": arguments}
    return arguments


def reply_path_hash(choices: list[dict[str, Any]]) -> str:
    return _sha256(_canon([{"content": c.get("content") or "", "finish_reason": c.get("finish_reason"),
                            "tool_calls": [{"name": t.get("name"), "arguments": _canon_arguments(t.get("arguments"))} for t in c.get("tool_call_list") or []]}
                           for c in choices]).encode())


def tools_offered(req: dict[str, Any]) -> int:
    """How many tools the request offered the model: its `tools` plus any legacy `functions`. The model-integrity rule reads it
    (founder ruling 2026-09-23): a tool call written as text is a model error only when there was a tool to call. The legacy
    field counts as offered, so a producer that used it keeps the old reading -- a model error -- rather than gaining the new one."""
    return sum(len(v) for v in (req.get("tools"), req.get("functions")) if isinstance(v, list))


def summarize_exchange(req_body: bytes, status: int, resp_body: bytes, content_type: str = "") -> dict[str, Any]:
    """What the model returned, reduced to the facts the model-integrity check reads. Never raises."""
    out: dict[str, Any] = {"http_status": status}
    try:
        req = json.loads(req_body or b"{}")
    except (ValueError, UnicodeDecodeError):
        req = {}
    if not isinstance(req, dict):
        req = {}
    out["stream"] = bool(req.get("stream"))
    out["max_tokens_requested"] = req.get("max_tokens") or req.get("max_completion_tokens")
    out["messages"] = len(req.get("messages") or [])
    out["tools_offered"] = tools_offered(req)
    out["prompt_sha256"] = prompt_hash(req) if req else None
    if status >= 400:
        text = resp_body.decode(errors="replace")
        out["error_text"] = text[:500]
        out["error_class"] = "context_window_exceeded" if any(m in text.lower() for m in CONTEXT_ERROR_MARKERS) else f"http_error:{status}"
        return out
    choices, usage = reply_choices(resp_body, out["stream"], content_type)
    usage = usage or {}
    out.update(prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"), total_tokens=usage.get("total_tokens"))
    out["finish_reasons"] = [c["finish_reason"] for c in choices]
    out["tool_calls"] = sum(len(c["tool_call_list"]) for c in choices)
    # C3 evidence.claimed_vs_landed: WHICH tool calls the model emitted, not just how many. The claim surface is
    # harness-owned -- the proxy saw the reply -- and the arguments travel as a hash, so a claim can be matched to the
    # world's receipt without the row carrying a payload.
    out["tool_call_list"] = [{"name": t.get("name"), "arguments_sha256": _sha256(json.dumps(_canon_arguments(t.get("arguments")), sort_keys=True, default=str).encode())}
                             for c in choices for t in (c["tool_call_list"] or [])]
    out["content_has_tool_call_markup"] = any(TOOL_CALL_MARKUP in (c["content"] or "") for c in choices)
    out["reply_path_sha256"] = reply_path_hash(choices) if choices else None
    return out


def reply_choices(resp_body: bytes, stream: bool, content_type: str = "") -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """A reply's choices (content, finish reason, tool calls with their raw arguments) and its usage, from a streamed or a
    plain body. The one parser behind the path hash, the model-integrity facts and the mismatch classification."""
    choices: list[dict[str, Any]] = []
    usage: dict[str, Any] | None = None
    if stream or "text/event-stream" in (content_type or ""):
        parts: list[str] = []
        tools: dict[Any, dict[str, str]] = {}
        finish = None
        for line in resp_body.decode(errors="replace").splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            usage = chunk.get("usage") or usage
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    t = tools.setdefault(tc.get("index"), {"name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    t["name"] += fn.get("name") or ""
                    t["arguments"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
        order = sorted(tools, key=lambda k: (k is None, k if isinstance(k, int) else 0))
        choices = [{"finish_reason": finish, "content": "".join(parts), "tool_call_list": [tools[k] for k in order]}]
    else:
        try:
            resp = json.loads(resp_body or b"{}")
        except (ValueError, UnicodeDecodeError):
            resp = {}
        if isinstance(resp, dict):
            usage = resp.get("usage")
            for ch in resp.get("choices") or []:
                msg = ch.get("message") or {}
                choices.append({"finish_reason": ch.get("finish_reason"), "content": msg.get("content") or "",
                                "tool_call_list": [{"name": (t.get("function") or {}).get("name"), "arguments": (t.get("function") or {}).get("arguments")} for t in msg.get("tool_calls") or []]})
    return choices, usage


class ModelProxyLogError(OSError):
    """The run dir cannot take the model-call log; raised at construction, never per call."""


class ModelProxy:
    def __init__(self, upstream_url: str, run_dir: str | Path | None = None, host: str = "127.0.0.1", port: int = 0, capture: bool = False, capture_first: bool = False) -> None:
        u = urlsplit(upstream_url)
        self.upstream_url = upstream_url
        self.upstream_host, self.upstream_port = u.hostname or "127.0.0.1", u.port or 80
        self.upstream_path = u.path.rstrip("/")
        self.capture = capture
        self.capture_first = capture_first
        # The on-disk log is the record the bundle seals; the in-memory list is what the runner reads during the run. A run
        # dir that cannot take the log is refused HERE, once, before a call passes through -- not discovered per call in a
        # handler thread whose traceback nobody reads (task d67450f4: an in-memory record survived a lost on-disk one).
        self.log_path = Path(run_dir) / "model-calls.jsonl" if run_dir else None
        if self.log_path is not None:
            if not Path(run_dir).is_dir():
                raise ModelProxyLogError(f"model proxy: run dir {run_dir} does not exist; the model-call log has nowhere to go")
            with self.log_path.open("a", encoding="utf-8"):
                pass   # proves the log can be appended to now; a failure is the caller's, raised at open
        self.capture_dir = Path(run_dir) / "model-capture" if (run_dir and (capture or capture_first)) else None
        self._calls: dict[str | None, list[dict[str, Any]]] = {}
        self._seq = 0
        self.log_write_errors = 0
        self.capture_write_errors = 0
        self._lock = threading.Lock()
        # Turn identity (attempt 4, A1): for every scenario registered through assign_turns, the N-th reply this proxy
        # sends opens turn N, published to the scenario's turn file BEFORE the reply is written to the agent, so no
        # tool the reply provokes can read a stale id. The proxy is the harness's; the agent's own counter is not consulted.
        self._turns: dict[str, HarnessTurns] = {}
        # C3: the agent's memory, hashed HERE when a turn opens. The proxy is harness code in the harness process,
        # so this series is the harness's account; on a target whose turn advances inside the agent process there is
        # no such series and the record says so.
        self._memory: dict[str, Any] = {}
        proxy = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _handle(self):
                t_recv = mono_ns()
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                sid, path = proxy.split_path(self.path)
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
                t_up0 = mono_ns()
                try:
                    conn = http.client.HTTPConnection(proxy.upstream_host, proxy.upstream_port, timeout=600)
                    conn.request(self.command, path, body=body if n else None, headers=headers)
                    resp = conn.getresponse()
                    data = resp.read()
                    status, reason, rheaders = resp.status, resp.reason, resp.getheaders()
                    conn.close()
                except OSError as e:
                    t_up1 = mono_ns()
                    # an error reply still opens a turn: the agent got an answer, and "reply N" must mean reply N whatever it held
                    turn, turn_opened = proxy.open_turn(sid)
                    proxy.record(sid, {"method": self.command, "path": path, "request_mono_ns": t_recv, "upstream_ms": (t_up1 - t_up0) / 1e6, "proxy_overhead_ms": (t_up0 - t_recv) / 1e6,
                                       "request_sha256": _sha256(body), "request_bytes": len(body), "http_status": 502, "error_class": "upstream_unreachable", "error_text": f"{type(e).__name__}: {e}"[:300],
                                       "turn": turn, "turn_opened_mono_ns": turn_opened}, body, b"")
                    self.send_error(502, str(e))
                    return
                t_up1 = mono_ns()
                turn, turn_opened = proxy.open_turn(sid)   # published before a byte of the reply reaches the agent (A1)
                self.send_response(status, reason)
                ctype = ""
                for k, v in rheaders:
                    if k.lower() == "content-type":
                        ctype = v
                    if k.lower() not in HOP_BY_HOP:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                t_sent = mono_ns()
                entry = {"method": self.command, "path": path, "request_mono_ns": t_recv, "upstream_start_mono_ns": t_up0, "upstream_end_mono_ns": t_up1, "response_sent_mono_ns": t_sent,
                         "upstream_ms": (t_up1 - t_up0) / 1e6, "proxy_overhead_ms": ((t_up0 - t_recv) + (t_sent - t_up1)) / 1e6,
                         "request_sha256": _sha256(body), "response_sha256": _sha256(data), "request_bytes": len(body), "response_bytes": len(data),
                         "turn": turn, "turn_opened_mono_ns": turn_opened, **summarize_exchange(body, status, data, ctype)}
                proxy.record(sid, entry, body, data)

            do_GET = do_POST = _handle

        self.server = ThreadingHTTPServer((host, port), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, name="model-proxy", daemon=True)

    @staticmethod
    def split_path(raw_path: str) -> tuple[str | None, str]:
        if raw_path.startswith("/s/"):
            sid, _, tail = raw_path[3:].partition("/")
            return _SAFE_ID.sub("_", sid) or None, "/" + tail
        return None, raw_path

    def agent_url(self, scenario_id: str) -> str:
        return f"{self.url}/s/{_SAFE_ID.sub('_', scenario_id)}{self.upstream_path}"

    # ---- turn identity (A1) ----
    def assign_turns(self, scenario_id: str, turn_file: str | Path) -> HarnessTurns:
        """Make this proxy the owner of `scenario_id`'s turn identity. Writes turn 0 now, so a tool process that
        starts before the first reply finds an id, and advances on every reply sent to that scenario from here on."""
        t = HarnessTurns(TurnFile(turn_file), assigned_by="model-proxy")
        with self._lock:
            self._turns[_SAFE_ID.sub("_", scenario_id)] = t
        return t

    def open_turn(self, sid: str | None) -> tuple[int | None, int | None]:
        """The next turn for a registered scenario, published, with the stamp taken BEFORE it was published: from
        that instant the file may say N, so an effect received after it may carry N. (`response_sent_mono_ns` is
        taken after the bytes are on the wire, and an agent can dispatch -- and the world record -- inside that
        gap; the identity check reads this stamp, not that one.) (None, None) for a scenario nobody registered:
        the world then sees no id and, under the single-call policy, refuses -- fail closed, not a guess."""
        with self._lock:
            t = self._turns.get(sid) if sid is not None else None
        if t is None:
            return None, None
        opened = mono_ns()
        with self._lock:
            mem = self._memory.get(sid) if sid is not None else None
        if mem is not None:
            mem.snapshot(t.current + 1, "harness:model-proxy")
        return t.advance(), opened

    def watch_memory(self, scenario_id: str, memory: Any) -> None:
        """Hash this scenario's memory file whenever this proxy opens a turn for it."""
        with self._lock:
            self._memory[_SAFE_ID.sub("_", scenario_id)] = memory

    def turn(self, scenario_id: str) -> int | None:
        with self._lock:
            t = self._turns.get(_SAFE_ID.sub("_", scenario_id))
        return t.current if t is not None else None

    def record(self, sid: str | None, entry: dict[str, Any], body: bytes, data: bytes) -> None:
        """The disk line is written FIRST; a write that fails is counted and named on the in-memory entry, so the two records can
        never silently disagree: the summary compares the lines on disk with the calls in memory (`log_complete`), and a run whose
        log is short fails at close the way a run that dropped spans does."""
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, "scenario_id": sid, **entry}
            if self.log_path:
                try:
                    with self.log_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, sort_keys=True) + "\n")
                except OSError as e:
                    self.log_write_errors += 1
                    entry["log_write_error"] = f"{type(e).__name__}: {e}"[:300]
            self._calls.setdefault(sid, []).append(entry)
            write = self.capture or (self.capture_first and sid is not None and len(self._calls[sid]) == 1)
            if self.capture_dir is not None and write:
                try:
                    d = self.capture_dir / (sid or "_unscoped")
                    d.mkdir(parents=True, exist_ok=True)
                    (d / f"{self._seq:06d}.request.json").write_bytes(body)
                    (d / f"{self._seq:06d}.response.json").write_bytes(data)
                except OSError as e:
                    self.capture_write_errors += 1
                    entry["capture_write_error"] = f"{type(e).__name__}: {e}"[:300]

    def log_lines(self) -> int | None:
        """Lines in the on-disk log now, or None when there is no log or it cannot be read."""
        if self.log_path is None:
            return None
        try:
            with self.log_path.open("rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return None

    def calls(self, scenario_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._calls.get(_SAFE_ID.sub("_", scenario_id), []))

    def summary(self) -> dict[str, Any]:
        with self._lock:
            entries = [e for es in self._calls.values() for e in es]
        overhead = sorted(e["proxy_overhead_ms"] for e in entries if e.get("proxy_overhead_ms") is not None)

        def pct(p: float) -> float | None:
            return overhead[min(len(overhead) - 1, int(round(p * (len(overhead) - 1))))] if overhead else None

        lines = self.log_lines()
        return {"upstream": self.upstream_url, "proxy": self.url, "capture": self.capture, "capture_first_exchange": self.capture_first, "calls": len(entries), "scenarios": len([k for k in self._calls if k]),
                "errors": sum(1 for e in entries if e.get("error_class")), "unscoped_calls": len(self._calls.get(None, [])),
                # the on-disk log against the in-memory record: complete only when every call is a line and no write failed
                "log_path": str(self.log_path) if self.log_path else None, "log_lines": lines, "log_write_errors": self.log_write_errors, "capture_write_errors": self.capture_write_errors,
                "log_complete": (self.log_path is None) or (lines == len(entries) and self.log_write_errors == 0),
                "overhead_ms": {"median": statistics.median(overhead) if overhead else None, "p95": pct(0.95), "max": overhead[-1] if overhead else None},
                "direct_access": "Tier B best effort: the egress allowlist admits this proxy and not the model server; an agent that unsets its proxy variables can still reach the server"}

    def start(self) -> "ModelProxy":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
