"""Detouring stepwise reference target, for tests only (fix A4, 2026-09-14): the stepwise reference that answers its first
MARK_TEST_DETOURS continuations without acting, the way OpenHands spent continuations on 2b checking its work or looking
for the mail command before it acted again. No model. Never registered in targets/registry.yaml; tests add it to a registry
of their own."""
from __future__ import annotations

import os
from typing import Any

from ..handle import AgentHandle
from .stepwise import StepwiseAgent


class DetourStepwiseAgent(StepwiseAgent):
    id = "detour-stepwise"

    def __init__(self, handle: AgentHandle, cfg: dict[str, Any]) -> None:
        super().__init__(handle, cfg)
        self._detours = int(os.environ.get("MARK_TEST_DETOURS", "0"))

    def inject(self, instruction: str) -> dict[str, Any]:
        from ..inject_states import did_not_act

        if not self.handle.should_stop() and self._detours > 0:
            self._detours -= 1
            return did_not_act("a detour: the turn ended without a tool call (test stand-in)")
        return super().inject(instruction)


def build(handle: AgentHandle, cfg: dict[str, Any]) -> DetourStepwiseAgent:
    return DetourStepwiseAgent(handle, cfg)
