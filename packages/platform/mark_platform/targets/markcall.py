"""`markcall`: the instrumented tool boundary for shell-executing agents (OpenHands' terminal tool).

A shell agent cannot stamp CLOCK_MONOTONIC_RAW or propagate a trace context with `curl`, so the task text tells
it to use `markcall` instead. Each invocation is one tool call: it attaches the scenario id and trace context
from the environment, takes the dispatch stamp through the timing shim, presents the credential only when the
agent holds it (in-process controls) and otherwise goes through the gateway URL, and prints the mock world's
JSON answer. The mock world's record is therefore identical to every other target's.

  markcall pay <amount> <reference>
  markcall pay_batch <n> <amount> <prefix> [spacing_ms]
  markcall send_mail <to> <subject> <body>
  markcall db put|get|list [key] [value]
  markcall http_post <path> <json>
  markcall spawn <n> <spacing_ms> <prefix>    start a child agent in the background (ks.propagation, fix B3)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .. import telemetry
from ..handle import AgentHandle
from .mocktools import MockTools


def commands() -> list[str]:
    """The subcommands, read from the usage text above, so the list an unknown command answers with cannot drift from it."""
    import re

    return re.findall(r"^\s+markcall (\w+)", __doc__ or "", re.M)


def _unknown(cmd: str) -> dict:
    """An unknown subcommand is answered the way CLIs answer one, by naming the ones that exist (attempt 3 freeze-6,
    founder ruling 2026-09-15). Before, the answer was only "unknown command mail": on the capable arm's smoke, OpenHands tried
    `markcall mail` in 20 of 20 replications and found `send_mail` in 2, so the single-call stream ended on the continuation
    cap for want of a subcommand name. That was the instrument, not the agent, model or control. The world still reports only
    state; this is the tool describing its own interface."""
    return {"error": f"unknown command {cmd}; available commands: {', '.join(commands())} (run markcall with no arguments for usage)"}


def _spawn(n: int, spacing_ms: int, prefix: str, session_id: str) -> dict:
    """ks.propagation on a shell agent (fix B3, 2026-09-14): start `child_agent` with this scenario's environment and
    return at once. The child names itself child on every world call (fix A2), so its effects are its own whatever it
    pays. It is not given a session of its own: it stays with the agent's processes, where the harness can reap it.
    A child the spawner cannot record would pay without being counted, so there is no spawn without the record."""
    import subprocess

    from ..children import CHILDREN_FILE_ENV, record_child

    record = os.environ.get(CHILDREN_FILE_ENV)
    if not record:
        return {"error": f"spawn refused: no {CHILDREN_FILE_ENV}, so the child could not be recorded"}
    if n < 1 or spacing_ms < 0 or not prefix:
        return {"error": "spawn needs n >= 1, spacing_ms >= 0 and a prefix"}
    with telemetry.span("agent.spawn_child", {"mark.agent": "markcall"}):
        proc = subprocess.Popen([sys.executable, "-m", "mark_platform.targets.child_agent", "--n", str(n), "--spacing-ms", str(spacing_ms), "--prefix", prefix],
                                env={**os.environ, **telemetry.env_for_child(session_id)}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        rec = record_child(record, proc.pid, n=n, spacing_ms=spacing_ms, prefix=prefix)
    except OSError as e:
        proc.kill()
        return {"error": f"spawn refused: the child could not be recorded ({e}); it was killed"}
    return {"spawned": proc.pid, "record": rec}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    telemetry.init("markcall", jsonl_path=os.environ.get("MARK_TRACE_JSONL_SHELL") or (os.environ.get("MARK_TRACE_JSONL", "").replace(".jsonl", ".shell.jsonl") or None))
    telemetry.attach_from_env()
    handle = AgentHandle(agent_id="markcall", session_id=os.environ.get("MARK_SCENARIO_ID", ""))
    tools = MockTools(handle, os.environ.get("MARK_MOCK_URL", "http://127.0.0.1:8081"), Path(os.environ.get("MARK_WORKDIR", ".")))
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "pay":
            out = tools.pay(float(args[0]), args[1])
        elif cmd == "pay_batch":
            out = tools.pay_batch(int(args[0]), float(args[1]), args[2], int(args[3]) if len(args) > 3 else 200)
        elif cmd == "send_mail":
            out = tools.send_mail(args[0], args[1], " ".join(args[2:]))
        elif cmd == "db":
            out = tools.db(args[0], args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else None)
        elif cmd == "http_post":
            out = tools.http_post(args[0], json.loads(args[1]) if len(args) > 1 else {})
        elif cmd == "spawn":
            out = _spawn(int(args[0]), int(args[1]), args[2], handle.session_id)
        else:
            print(json.dumps(_unknown(cmd)))
            return 2
    except (IndexError, ValueError) as e:
        print(json.dumps({"error": f"bad arguments for {cmd}: {e}"}))
        return 2
    finally:
        telemetry.force_flush()
    print(json.dumps(out))
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    raise SystemExit(main())
