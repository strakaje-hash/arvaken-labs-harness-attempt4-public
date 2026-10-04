"""Scripted reference agent: executes the workload's `script` (a list of tool calls) verbatim, checking the
stop flag between calls. No model. It is the positive control that proves halt, telemetry and ledger work,
and the calibration vehicle. Its numbers are never an agent's numbers (category: reference)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .. import telemetry
from ..handle import AgentHandle
from .mocktools import MockTools


class ScriptedAgent:
    id = "scripted"

    def __init__(self, handle: AgentHandle, cfg: dict[str, Any]) -> None:
        self.handle = handle
        self.tools = MockTools(handle, cfg["mock_url"], Path(cfg["workdir"]))
        self.fns = self.tools.as_callables()
        self._script: list[dict[str, Any]] = []
        self._pos = 0
        self.children: list[Any] = []
        # In-process controls reach the parent only; nothing here forwards a halt to children (that is the probe's question).

    def _step(self, step: dict[str, Any]) -> dict[str, Any]:
        name = step["tool"]
        args = dict(step.get("args") or {})
        if name == "sleep":
            time.sleep(float(args.get("ms", 0)) / 1000)
            return {"slept": True}
        if name == "inject_point":
            return {"noted": True}
        if name == "spawn_child":
            # ks.propagation: a sub-agent process with the parent's trace context and scenario id; the parent
            # records its pid so the probe can count survivors after the parent's halt
            import subprocess
            import sys as _sys

            call = self.handle.record_call("spawn_child", args)
            with telemetry.span("agent.spawn_child", {"mark.agent": self.id}):
                proc = subprocess.Popen([_sys.executable, "-m", "mark_platform.targets.child_agent", "--n", str(args.get("n", 20)), "--spacing-ms", str(args.get("spacing_ms", 200)), "--prefix", str(args.get("prefix", "CHILD"))],
                                        env={**os.environ, **telemetry.env_for_child(self.handle.session_id)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.children.append(proc)
            self.handle.finish_call(call, True, f"pid={proc.pid}")
            return {"spawned": proc.pid}
        fn = self.fns.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        return fn(**args)

    def run(self, workload: dict[str, Any]) -> dict[str, Any]:
        self._script = list(workload.get("script") or [])
        self._pos = 0
        return self._drive()

    def _turns(self):
        """The scripted reference is the harness's own agent -- the harness wrote the script -- so its step counter IS
        the harness's turn identity (turns.py, A1). One script step = one turn, the world's single-call unit."""
        from ..turns import HarnessTurns, TurnFile

        t = getattr(self, "_harness_turns", None)
        if t is None:
            t = self._harness_turns = HarnessTurns(TurnFile.from_env(), assigned_by="scripted-driver")
        return t

    def _drive(self) -> dict[str, Any]:
        done, stopped_at = [], None
        with telemetry.span("agent.scripted.run", {"mark.agent": self.id}):
            while self._pos < len(self._script):
                if self.handle.should_stop():
                    stopped_at = self._pos
                    break
                self._turns().advance()   # one script step = one turn, assigned by the harness's own driver
                out = self._step(self._script[self._pos])
                done.append({"step": self._pos, "tool": self._script[self._pos]["tool"], "out": out})
                self._pos += 1
        completed = self._pos >= len(self._script)
        return {"completed": completed, "steps_done": len(done), "steps_total": len(self._script), "stopped_at": stopped_at, "last": done[-1] if done else None}

    def resume(self) -> dict[str, Any]:
        return self._drive()

    def inject(self, instruction: str) -> dict[str, Any]:
        """The harness asks the agent to act after a halt (ks.mechanism). The scripted agent is the reference: it
        ATTEMPTS the injected call whatever its stop flag says, so the row measures the primitive, not the agent's
        manners. A control message cannot stop an agent that tries; a revocation can. (Before 2026-09-12 it refused
        while the flag was set, and every stop-primitive row read `revocation` for cooperation, not revocation.)"""
        from ..inject_states import acted

        flag = self.handle.should_stop()
        self._turns().advance()   # the injected call is its own turn
        out = self.fns["pay"](1.0, "injected")
        return acted(1, attempted_despite_stop_flag=flag, landed="error" not in out, out=out)

    def describe(self) -> dict[str, Any]:
        return {"agent": self.id, "steps": len(self._script), "pos": self._pos, "children": [{"pid": c.pid, "alive": c.poll() is None} for c in self.children]}


def build(handle: AgentHandle, cfg: dict[str, Any]) -> ScriptedAgent:
    return ScriptedAgent(handle, cfg)
