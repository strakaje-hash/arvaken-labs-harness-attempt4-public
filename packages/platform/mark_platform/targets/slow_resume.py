"""Slow-resume reference target, for tests only (fix A1, 2026-09-14): the scripted reference whose resume waits one second
longer than the agent process's linger time before continuing its script. That is what a model-driven agent's resumed run
does: on attempt 2b LangGraph's resumed graph was still calling the model when the agent process left, 3 s after its last
recorded activity, and 40 of 40 single-call ks.resume replications read inconsistent. No model. Never registered in
targets/registry.yaml; tests add it to a registry of their own."""
from __future__ import annotations

import os
import time
from typing import Any

from ..handle import AgentHandle
from .scripted import ScriptedAgent


class SlowResumeAgent(ScriptedAgent):
    id = "slow-resume"

    def resume(self) -> dict[str, Any]:
        time.sleep(float(os.environ.get("MARK_LINGER_S", "3")) + 1.0)
        return super().resume()


def build(handle: AgentHandle, cfg: dict[str, Any]) -> SlowResumeAgent:
    return SlowResumeAgent(handle, cfg)
