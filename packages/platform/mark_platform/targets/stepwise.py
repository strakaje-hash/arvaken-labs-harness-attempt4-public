"""Stepwise reference target, for tests only: executes ONE effect step of the workload script per run and per
continuation, then stops, which is what the smoke runs showed the model-driven agents do on the single-call arm. No
model. It lets the harness continuation path (founder ruling 2026-09-13) be exercised end to end on a laptop: a none
control lets every continuation act, a stop-flag control makes every post-halt continuation a did_not_act.
Never registered in targets/registry.yaml; tests add it to a registry of their own."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..handle import AgentHandle
from .mocktools import MockTools

EFFECT_TOOLS = ("pay", "send_mail")


class StepwiseAgent:
    id = "stepwise"

    def __init__(self, handle: AgentHandle, cfg: dict[str, Any]) -> None:
        self.handle = handle
        self.fns = MockTools(handle, cfg["mock_url"], Path(cfg["workdir"])).as_callables()
        self._steps: list[dict[str, Any]] = []
        self._pos = 0

    def _one(self) -> int:
        while self._pos < len(self._steps) and self._steps[self._pos].get("tool") not in EFFECT_TOOLS:
            self._pos += 1
        if self._pos >= len(self._steps):
            return 0
        step = self._steps[self._pos]
        # a harness-authored step is a harness-assigned turn (turns.py, A1); the agent's counter is never the id
        from ..turns import HarnessTurns, TurnFile

        t = getattr(self, "_harness_turns", None)
        if t is None:
            t = self._harness_turns = HarnessTurns(TurnFile.from_env(), assigned_by="scripted-driver")
        t.advance()
        self.fns[step["tool"]](**dict(step.get("args") or {}))
        self._pos += 1
        return 1

    def run(self, workload: dict[str, Any]) -> dict[str, Any]:
        self._steps, self._pos = list(workload.get("script") or []), 0
        return {"completed": False, "steps_done": self._one()}

    def resume(self) -> dict[str, Any]:
        return {"completed": self._pos >= len(self._steps)}

    def inject(self, instruction: str) -> dict[str, Any]:
        from ..inject_states import acted, did_not_act

        if self.handle.should_stop():
            return did_not_act("stop flag set: the stepwise reference does not act after a halt")
        n = self._one()
        return acted(n) if n else did_not_act("no step left")

    def describe(self) -> dict[str, Any]:
        return {"agent": self.id, "position": self._pos, "steps": len(self._steps)}


def build(handle: AgentHandle, cfg: dict[str, Any]) -> StepwiseAgent:
    return StepwiseAgent(handle, cfg)
