"""Turn identity is assigned by the harness, not read from the agent (attempt 4, A1).

A turn is the world's single-call unit: on the `single_call_per_turn` variant the mock world executes one effect
per turn and refuses the rest, so the turn id on an effect decides whether that effect counts. Until A1 the id
came from the agent adapter, which called `new_turn()` wherever the *framework* thought a turn began -- and both
frameworks advance their own counter on replies with no tool call, including the continuations the harness itself
sends. Attempt 3's audit flagged 600 LangGraph replications and had to move every OpenHands turn to "agent-sourced"
for exactly that reason.

The harness owns the model path (the proxy in `model_proxy.py`), so it owns the sequence of model replies. That
sequence is the turn identity now:

    turn N  ==  the N-th reply the proxy sent to this scenario's agent

Every effect the world receives carries the turn in force when it was dispatched, and by construction that is the
number of turns the proxy had opened -- stamped and published before a byte of the reply leaves -- at or before
the world received it. `check_turns` states that identity as a check the runner can perform; nothing about it
depends on what the agent believes.

Two owners write a turn file, and both are harness code:

  - the **model proxy**, for model-driven targets, advancing on every reply it forwards (error replies included:
    a reply that opened no tool calls still opened a turn, and a turn with no effects is a fact, not a gap);
  - the **scripted reference driver**, whose script the harness wrote, advancing once per script step.

The agent's own counter (`AgentHandle.agent_turn`) is still kept -- as evidence, sent beside the harness's id as
`X-Mark-Agent-Turn` and recorded by the world next to it -- and is never the key. A tool process that finds no
turn file sends no turn id and, under the policy, is refused. It does not fall back to the agent's counter: that
fallback was the defect.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

TURN_FILE_ENV = "MARK_TURN_FILE"


class TurnFile:
    """The cross-process channel: one small file per scenario holding the current turn id. Written atomically (tmp
    then `os.replace`) so a tool process never reads a half-written number. Read by every tool process (in-process
    tools, the MCP server, `markcall`); written only by a harness owner below."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    @classmethod
    def from_env(cls) -> "TurnFile | None":
        p = os.environ.get(TURN_FILE_ENV)
        return cls(p) if p else None

    def write(self, n: int) -> None:
        tmp = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        try:
            tmp.write_text(str(n), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            # a turn the harness could not publish is a turn the world will refuse (no id): fail closed, not loud here
            pass

    def read(self) -> int | None:
        try:
            txt = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(txt) if txt.isdigit() else None


class HarnessTurns:
    """A harness-owned turn assigner for one scenario. `advance()` opens the next turn and publishes it; the file
    carries 0 from construction so a tool process that starts before the first turn still finds an id."""

    def __init__(self, turn_file: TurnFile | None, *, assigned_by: str) -> None:
        self.file = turn_file
        self.assigned_by = assigned_by
        self.current = 0
        if self.file is not None:
            self.file.write(0)

    def advance(self) -> int:
        self.current += 1
        if self.file is not None:
            self.file.write(self.current)
        return self.current


def check_turns(mock_calls: list[dict[str, Any]], model_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The identity A1 establishes, as a runner-side check for a model-driven scenario: every world call's `turn`
    equals the number of turns the proxy had OPENED (`turn_opened_mono_ns`) at or before the call's
    `received_mono_ns`.

    Why the opened stamp and not `response_sent_mono_ns`: the sent stamp is taken after the reply's bytes are on
    the wire, and an agent can read them, dispatch, and have the world record the effect before the proxy thread
    reaches that line -- the first run of this check flagged a correct turn for exactly that reason. The turn is
    opened, and its stamp taken, before a byte of the reply leaves, so nothing provoked by reply N can be received
    before turn N's stamp.

    Returns the calls that break it, each naming both numbers. Empty means the identity held on every call, which
    is what the harness owning the id guarantees; a non-empty result is a fact about the instrument, never about
    the agent. Calibration calls carry no turn and are not the agent's, so they are skipped."""
    opened = sorted(int(m["turn_opened_mono_ns"]) for m in model_calls if m.get("turn_opened_mono_ns") is not None)
    out = []
    for c in mock_calls:
        if str(c.get("path", "")).startswith("/calibration"):
            continue
        u = c.get("received_mono_ns")
        if u is None:
            continue
        opened_before = sum(1 for t in opened if t <= int(u))
        if c.get("turn") != opened_before:
            out.append({"seq": c.get("seq"), "path": c.get("path"), "turn": c.get("turn"), "turns_opened_at_or_before_receipt": opened_before,
                        "received_mono_ns": u, "why": "turn id is not the number of turns the proxy had opened when the effect was received"})
    return out
