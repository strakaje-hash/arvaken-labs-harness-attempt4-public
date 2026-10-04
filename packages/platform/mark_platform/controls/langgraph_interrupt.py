"""LangGraph native interrupt + checkpoint, as a control.

The langgraph-ref agent has a `guard` node before its tool node. When the stop flag is set the guard calls
`interrupt()`: the graph parks on its checkpoint (MemorySaver) and `invoke` returns with `__interrupt__`.
Resume = the agent adapter re-invokes with `Command(resume="go")`; this control only flips the flag and
records the checkpoint the agent reports. Applicable to langgraph-ref only (registry.applicable).
"""
from __future__ import annotations

from typing import Any

from .. import telemetry
from ..handle import AgentHandle


class LangGraphInterruptControl:
    id = "langgraph-interrupt"

    def __init__(self, handle: AgentHandle) -> None:
        self.handle = handle
        self.halts = 0

    def halt(self, body: dict[str, Any]) -> dict[str, Any]:
        with telemetry.span("control.langgraph.interrupt_request", {"mark.control": self.id}):
            self.handle.stop()  # the guard node turns this into interrupt() at the next tool boundary
            self.halts += 1
        return {"control": self.id, "mechanism": "framework-interrupt", "primitive": "stop", "acted": True, "note": "guard node will interrupt() before the next tool call"}

    def resume(self, body: dict[str, Any]) -> dict[str, Any]:
        with telemetry.span("control.langgraph.resume_request", {"mark.control": self.id}):
            self.handle.resume()
        return {"control": self.id, "acted": True, "note": "stop flag cleared; the agent adapter resumes the graph from its checkpoint"}

    def describe(self) -> dict[str, Any]:
        return {"control": self.id, "mechanism": "framework-interrupt", "primitive": "stop", "halts": self.halts}


def build(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> LangGraphInterruptControl:
    return LangGraphInterruptControl(handle)
