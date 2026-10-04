"""The `none` control: the baseline. The halt command arrives and is recorded; nobody acts on it."""
from __future__ import annotations

from typing import Any

from ..handle import AgentHandle


class NoneControl:
    id = "none"

    def __init__(self, handle: AgentHandle) -> None:
        self.handle = handle
        self.received: list[dict[str, Any]] = []

    def halt(self, body: dict[str, Any]) -> dict[str, Any]:
        self.received.append(body)
        return {"control": self.id, "mechanism": "none", "primitive": None, "acted": False, "note": "halt recorded; no control is installed"}

    def resume(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"control": self.id, "acted": False}

    def describe(self) -> dict[str, Any]:
        return {"control": self.id, "mechanism": "none", "primitive": None}


def build(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> NoneControl:
    return NoneControl(handle)
