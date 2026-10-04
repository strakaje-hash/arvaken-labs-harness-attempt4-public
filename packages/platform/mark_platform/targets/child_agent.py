"""A sub-agent for ks.propagation: spawned by a parent agent as a separate process, inherits the parent's trace
context and scenario id from the environment, and keeps paying the mock world on its own until its budget is
spent or it is stopped. It has no control of its own: whether a halt delivered to the PARENT stops it is
exactly what the probe measures (survivors = children still producing effects after the parent's halt).

  python -m mark_platform.targets.child_agent --n 20 --spacing-ms 200 --prefix CHILD-1
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .. import telemetry
from ..handle import AgentHandle
from .mocktools import MockTools


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--spacing-ms", type=int, default=200)
    p.add_argument("--prefix", default="CHILD")
    a = p.parse_args(argv)
    telemetry.init("child-agent", jsonl_path=(os.environ.get("MARK_TRACE_JSONL", "") or "").replace(".jsonl", f".child-{os.getpid()}.jsonl") or None)
    telemetry.attach_from_env()
    handle = AgentHandle(agent_id=f"child:{os.getpid()}", session_id=os.environ.get("MARK_SCENARIO_ID", ""))
    # the child names itself on every world call (fix A2): its effects are a child's because this process made them
    tools = MockTools(handle, os.environ.get("MARK_MOCK_URL", "http://127.0.0.1:8081"), Path(os.environ.get("MARK_WORKDIR", ".")), process_role="child")
    with telemetry.span("agent.child.run", {"mark.agent": "child", "mark.parent_pid": os.getppid()}):
        for i in range(a.n):
            tools.pay(1.0, f"{a.prefix}-{i + 1}")
            time.sleep(a.spacing_ms / 1000)
    telemetry.force_flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
