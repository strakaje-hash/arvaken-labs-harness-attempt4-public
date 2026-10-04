"""In-agent side of the credential gateway (control_class out_of_process). Nothing here can act: the control
lives in the harness process (mark_platform.gateway) and the harness delivers the halt THERE, never to this
adapter. This stub exists so the agent process launches with a control entry that says so; if a halt ever
reaches it, it records the fact and refuses to act, so a mis-routed halt cannot masquerade as the gateway."""
from __future__ import annotations

from typing import Any

from ..handle import AgentHandle


class CredentialGatewayStub:
    id = "credential-gateway"

    def __init__(self, handle: AgentHandle) -> None:
        self.handle = handle
        self.misrouted = 0

    def halt(self, body: dict[str, Any]) -> dict[str, Any]:
        self.misrouted += 1
        return {"control": self.id, "mechanism": "gateway", "primitive": None, "acted": False, "control_class": "out_of_process",
                "error": "halt delivered to the agent process; the credential gateway is out of process and receives halts from the harness directly"}

    def resume(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"control": self.id, "acted": False}

    def describe(self) -> dict[str, Any]:
        return {"control": self.id, "mechanism": "gateway", "primitive": "revoke", "control_class": "out_of_process", "misrouted_halts": self.misrouted}


def build(handle: AgentHandle, cfg: dict[str, Any] | None = None) -> CredentialGatewayStub:
    return CredentialGatewayStub(handle)
