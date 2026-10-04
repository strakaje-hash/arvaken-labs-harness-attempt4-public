"""Reference controls: the three halt primitives applied directly, with no third-party code in between.
They bound what any control can achieve on a given agent (ref-revoke is the best case; ref-stop is what a
control that only sends a message can do) and are the positive controls for the halt pipeline in CI.
Category `reference`: their numbers are printed next to a control's, never in place of one."""
from __future__ import annotations

from typing import Any

from .. import telemetry
from ..handle import AgentHandle


class ReferenceControl:
    def __init__(self, handle: AgentHandle, primitive: str) -> None:
        self.handle = handle
        self.primitive = primitive
        self.id = f"ref-{primitive}"
        self.halts = 0

    def halt(self, body: dict[str, Any]) -> dict[str, Any]:
        with telemetry.span(f"control.reference.{self.primitive}", {"mark.control": self.id}):
            self.handle.stop()
            cancelled, revoked = 0, None
            if self.primitive in ("cancel", "revoke"):
                cancelled = self.handle.cancel_inflight()
            if self.primitive == "revoke":
                revoked = self.handle.revoke()
            self.halts += 1
        # Honesty: a primitive the agent never wired (no hook) measured nothing. The probe turns this into not_run.
        unreachable = (self.primitive == "cancel" and cancelled < 0) or (self.primitive == "revoke" and revoked is False)
        return {"control": self.id, "mechanism": "direct", "primitive": self.primitive, "acted": True, "cancelled_inflight": max(cancelled, 0),
                "primitive_unreachable": unreachable, "reachable": self.handle.primitives_reachable()}

    def resume(self, body: dict[str, Any]) -> dict[str, Any]:
        self.handle.resume()
        return {"control": self.id, "acted": True}

    def describe(self) -> dict[str, Any]:
        return {"control": self.id, "mechanism": "direct", "primitive": self.primitive, "halts": self.halts}


def build_stop(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> ReferenceControl:
    return ReferenceControl(handle, "stop")


def build_cancel(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> ReferenceControl:
    return ReferenceControl(handle, "cancel")


def build_revoke(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> ReferenceControl:
    return ReferenceControl(handle, "revoke")


build = build_stop
