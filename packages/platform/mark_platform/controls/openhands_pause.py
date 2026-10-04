"""OpenHands native pause: `Conversation.pause()`. Applicable to openhands-sdk only. The agent adapter hands
the conversation to this control through cfg["conversation_ref"] (a zero-arg callable) after it is built."""
from __future__ import annotations

from typing import Any, Callable

from .. import telemetry
from ..handle import AgentHandle


class OpenHandsPauseControl:
    id = "openhands-pause"

    def __init__(self, handle: AgentHandle, conversation_ref: Callable[[], Any] | None) -> None:
        self.handle = handle
        self.conversation_ref = conversation_ref
        self.halts = 0

    def halt(self, body: dict[str, Any]) -> dict[str, Any]:
        conv = self.conversation_ref() if self.conversation_ref else None
        with telemetry.span("control.openhands.pause", {"mark.control": self.id}):
            self.handle.stop()
            acted = False
            if conv is not None:
                conv.pause()
                acted = True
            self.halts += 1
        return {"control": self.id, "mechanism": "framework-pause", "primitive": "stop", "acted": acted, "note": "Conversation.pause() called" if acted else "no conversation yet; only the stop flag was set"}

    def resume(self, body: dict[str, Any]) -> dict[str, Any]:
        self.handle.resume()
        return {"control": self.id, "acted": True, "note": "stop flag cleared; the adapter calls Conversation.run() again"}

    def describe(self) -> dict[str, Any]:
        return {"control": self.id, "mechanism": "framework-pause", "primitive": "stop", "halts": self.halts}


def build(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> OpenHandsPauseControl:
    return OpenHandsPauseControl(handle, (cfg or {}).get("conversation_ref"))
