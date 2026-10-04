"""Failing-resume reference target, for tests only (fixes A1 and A10, 2026-09-14): the scripted reference whose resume
raises, so the agent records no resume outcome, which is the record attempt 2b's killed resumes left. No model. Never
registered in targets/registry.yaml; tests add it to a registry of their own."""
from __future__ import annotations

from typing import Any

from ..handle import AgentHandle
from .scripted import ScriptedAgent


class FailingResumeAgent(ScriptedAgent):
    id = "failing-resume"

    def resume(self) -> dict[str, Any]:
        raise RuntimeError("the resume did not return (a test stand-in for an agent process that ends mid-resume)")


def build(handle: AgentHandle, cfg: dict[str, Any]) -> FailingResumeAgent:
    return FailingResumeAgent(handle, cfg)
