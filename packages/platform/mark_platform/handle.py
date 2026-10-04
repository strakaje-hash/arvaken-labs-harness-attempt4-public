"""In-process seam between an agent adapter and a control adapter.

An `AgentHandle` is what a control can act on. It exposes the three primitives a halt can use, so the probe
can say WHICH one the control used (ks.mechanism):
  stop_flag        a threading.Event the agent's loop checks between steps ("control message")
  cancel_inflight  cancel tool work already dispatched but not finished (best effort, agent-specific)
  revoke           remove the agent's ability to act: tool bindings / credentials / network are dropped
The handle also records what the agent did (tool calls with stamps) so the adapter can report it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .clock import mono_ns, wall_ns


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    dispatched_mono_ns: int
    finished_mono_ns: int | None = None
    ok: bool | None = None
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "args": self.args, "dispatched_mono_ns": self.dispatched_mono_ns, "finished_mono_ns": self.finished_mono_ns, "ok": self.ok, "detail": self.detail}


@dataclass
class AgentHandle:
    agent_id: str
    session_id: str
    stop_flag: threading.Event = field(default_factory=threading.Event)
    revoked: threading.Event = field(default_factory=threading.Event)
    calls: list[ToolCall] = field(default_factory=list)
    _cancel_hooks: list[Callable[[], int]] = field(default_factory=list)
    _revoke_hooks: list[Callable[[], None]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    halted_at_mono_ns: int | None = None
    resumed_at_mono_ns: int | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    outcome: dict[str, Any] = field(default_factory=dict)
    # The agent's OWN turn counter: evidence, never the key (attempt 4, A1). Until A1 this was `turn`, the adapter
    # advanced it wherever the framework thought a turn began, and it was written to the turn file every tool
    # process read -- so the world's single-call unit was whatever the agent said it was, and both frameworks
    # advance on replies with no tool call. The id the world enforces is assigned by the harness now (turns.py:
    # the model proxy's reply sequence, or the scripted driver's step). This counter is sent beside it as
    # X-Mark-Agent-Turn and recorded next to it, so a reader can see where the two disagree. It writes nothing.
    agent_turn: int = 0

    def agent_turn_advanced(self) -> int:
        """The adapter reports that the framework began what it calls a turn. Recorded; not enforced."""
        with self._lock:
            self.agent_turn += 1
            return self.agent_turn

    # ---- primitives a control may use ----
    def stop(self) -> None:
        """Control message: ask the agent to stop at its next check."""
        with self._lock:
            if self.halted_at_mono_ns is None:
                self.halted_at_mono_ns = mono_ns()
        self.stop_flag.set()

    def cancel_inflight(self) -> int:
        """Cancel dispatched-but-unfinished tool work. Returns how many were cancelled, or -1 when NO agent has
        registered a cancel hook (the primitive does not reach this agent's tool boundary: an out-of-process tool
        server, for instance). A control that reports -1 measured nothing; the probe records not_run."""
        if not self._cancel_hooks:
            return -1
        n = 0
        for h in list(self._cancel_hooks):
            try:
                n += int(h() or 0)
            except Exception:  # noqa: BLE001
                pass
        return n

    def revoke(self) -> bool:
        """Revocation: after this the agent CANNOT act even if it wants to (tools unbound). Returns False when no
        revoke hook is registered, i.e. the primitive does not reach this agent's tools."""
        self.revoked.set()
        if not self._revoke_hooks:
            return False
        for h in list(self._revoke_hooks):
            try:
                h()
            except Exception:  # noqa: BLE001
                pass
        return True

    def primitives_reachable(self) -> dict[str, bool]:
        return {"stop": True, "cancel_inflight": bool(self._cancel_hooks), "revoke": bool(self._revoke_hooks)}

    def resume(self) -> None:
        self.stop_flag.clear()
        self.resumed_at_mono_ns = mono_ns()

    # ---- agent side ----
    def on_cancel(self, hook: Callable[[], int]) -> None:
        self._cancel_hooks.append(hook)

    def on_revoke(self, hook: Callable[[], None]) -> None:
        self._revoke_hooks.append(hook)

    def should_stop(self) -> bool:
        return self.stop_flag.is_set() or self.revoked.is_set()

    def record_call(self, name: str, args: dict[str, Any]) -> ToolCall:
        c = ToolCall(name=name, args=args, dispatched_mono_ns=mono_ns())
        with self._lock:
            self.calls.append(c)
        return c

    def finish_call(self, c: ToolCall, ok: bool, detail: str = "") -> None:
        c.finished_mono_ns = mono_ns()
        c.ok = ok
        c.detail = detail[:500]

    def status(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "session_id": self.session_id, "halted": self.stop_flag.is_set(), "revoked": self.revoked.is_set(),
                "finished": self.finished.is_set(), "calls": len(self.calls), "halted_at_mono_ns": self.halted_at_mono_ns, "now_mono_ns": mono_ns(), "now_wall_ns": wall_ns(),
                "agent_turn": self.agent_turn}
