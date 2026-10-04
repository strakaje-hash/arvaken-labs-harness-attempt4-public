"""MCP tool server (stdio) exposing the mock-world tools to the LangGraph reference agent. Runs as a
subprocess of the agent with the scenario's trace context in its environment, so its spans and its calls to
the mock world join the scenario trace across the process boundary (the propagation test in tests/env).

Revocation across this boundary: the agent adapter's revoke hook terminates this process, which is the honest
version of "credentials/tool access revoked" for an out-of-process tool server."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import telemetry
from ..handle import AgentHandle
from .mocktools import MockTools


def main() -> None:
    telemetry.init("mcp-tools", jsonl_path=os.environ.get("MARK_TRACE_JSONL_MCP"))
    telemetry.attach_from_env()
    handle = AgentHandle(agent_id="mcp-tools", session_id=os.environ.get("MARK_SCENARIO_ID", ""))
    tools = MockTools(handle, os.environ.get("MARK_MOCK_URL", "http://127.0.0.1:8081"), Path(os.environ.get("MARK_WORKDIR", "work")))
    mcp = FastMCP("mark-mock-world")

    @mcp.tool()
    def pay(amount: float, reference: str, delay_ms: int = 0) -> dict[str, Any]:
        """Charge a payment of `amount` with `reference` (mock payment service)."""
        return tools.pay(amount, reference, delay_ms)

    @mcp.tool()
    def pay_batch(n: int, amount: float, reference_prefix: str, spacing_ms: int = 200, delay_ms: int = 0) -> dict[str, Any]:
        """Charge n payments in one batch, references reference_prefix-1..n (mock payment service)."""
        return tools.pay_batch(n, amount, reference_prefix, spacing_ms, delay_ms)

    @mcp.tool()
    def send_mail(to: str, subject: str, body: str) -> dict[str, Any]:
        """Send an email (mock mail service)."""
        return tools.send_mail(to, subject, body)

    @mcp.tool()
    def http_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to the mock API at /api/<path>."""
        return tools.http_post(path, payload)

    @mcp.tool()
    def db(op: str, key: str = "", value: Any = None) -> dict[str, Any]:
        """Mock database: op is put, get or list."""
        return tools.db(op, key, value)

    @mcp.tool()
    def write_file(name: str, content: str) -> dict[str, Any]:
        """Write a text file in the working directory."""
        return tools.write_file(name, content)

    @mcp.tool()
    def read_file(name: str) -> dict[str, Any]:
        """Read a text file from the working directory."""
        return tools.read_file(name)

    @mcp.tool()
    def calibration_sleep(ms: int = 250) -> dict[str, Any]:
        """Known-latency tool: the service sleeps `ms` milliseconds."""
        return tools.calibration_sleep(ms)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
