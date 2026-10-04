"""Control adapters. Each module exposes `build(handle, cfg) -> Control`.

A Control answers the control channel:
  halt(body)   -> dict describing what the control DID (mechanism, primitive used, control's own return value)
  resume(body) -> dict
  inject(body) -> dict: the harness's attempt to make the agent act after a halt is routed through the control
                 layer so a control that intercepts instructions can show it
  describe()   -> {"control": id, "mechanism": ..., "primitive": ...}
"""
from __future__ import annotations

from typing import Any, Protocol


class Control(Protocol):
    id: str

    def halt(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def resume(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def describe(self) -> dict[str, Any]: ...
