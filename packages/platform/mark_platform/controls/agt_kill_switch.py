"""Microsoft Agent Governance Toolkit: `hypervisor.security.KillSwitch` (agent-governance-toolkit-core 5.0.0).

The toolkit's kill switch is a registry of termination CALLBACKS: `register_agent(did, callback)`, then
`kill(did, session, reason)` invokes the callback in a daemon thread with a 5 s timeout and records a
KillResult. The toolkit itself revokes nothing; what termination means is whatever the integrator registered.
This adapter registers the agent handle's `stop()` (a control message) by default, or `revoke` when
cfg.primitive == "revoke", and reports which one it used, so the benchmark row says exactly what was measured:
"the toolkit's kill switch wired to primitive P".
"""
from __future__ import annotations

from typing import Any

from .. import telemetry
from ..handle import AgentHandle


class AgtKillSwitchControl:
    id = "agt-kill-switch"

    def __init__(self, handle: AgentHandle, primitive: str = "stop") -> None:
        from hypervisor.security.kill_switch import KillReason, KillSwitch  # agent-governance-toolkit-core

        self.handle = handle
        self.primitive = primitive
        self.KillReason = KillReason
        self.ks = KillSwitch()
        self.ks.register_agent(handle.agent_id, self._terminate)
        self.results: list[dict[str, Any]] = []

    def _terminate(self) -> None:
        # The callback the toolkit invokes. Chosen primitive, nothing more: the row measures the toolkit + this wiring.
        if self.primitive == "revoke":
            self.handle.stop()
            self.handle.cancel_inflight()
            self.handle.revoke()
        elif self.primitive == "stop+cancel":
            self.handle.stop()
            self.handle.cancel_inflight()
        else:
            self.handle.stop()

    def halt(self, body: dict[str, Any]) -> dict[str, Any]:
        with telemetry.span("control.agt.kill", {"mark.control": self.id, "mark.primitive": self.primitive}) as s:
            r = self.ks.kill(self.handle.agent_id, self.handle.session_id, self.KillReason.MANUAL, details=str(body.get("reason", "harness")))
            out = {"kill_id": r.kill_id, "terminated": r.terminated, "reason": r.reason.value, "timestamp": r.timestamp.isoformat(), "handoffs": len(r.handoffs), "details": r.details}
            s.set_attribute("mark.kill.terminated", r.terminated)
        self.results.append(out)
        reach = self.handle.primitives_reachable()
        unreachable = (self.primitive == "revoke" and not reach["revoke"]) or (self.primitive == "stop+cancel" and not reach["cancel_inflight"])
        return {"control": self.id, "mechanism": "callback", "primitive": self.primitive, "acted": True, "kill_result": out, "primitive_unreachable": unreachable, "reachable": reach}

    def resume(self, body: dict[str, Any]) -> dict[str, Any]:
        # The toolkit unregisters the agent on kill; resuming means re-registering (its documented contract).
        self.ks.register_agent(self.handle.agent_id, self._terminate)
        self.handle.resume()
        return {"control": self.id, "acted": True, "note": "agent re-registered with the kill switch; stop flag cleared"}

    def describe(self) -> dict[str, Any]:
        return {"control": self.id, "mechanism": "callback", "primitive": self.primitive, "kills": len(self.results), "toolkit_kill_history": self.ks.total_kills}


def build(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> AgtKillSwitchControl:
    return AgtKillSwitchControl(handle, primitive=(cfg or {}).get("primitive", "stop"))
