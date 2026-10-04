"""OpenHands (software-agent-sdk 1.47.0) as a target: Agent + Conversation over a LocalWorkspace with the
terminal and file-editor tools. Effects reach the mock world through `curl` from the terminal tool, with the
scenario header, so the mock world's record is the same ground truth as for every other target.

Halt: `Conversation.pause()` (framework-native, via controls/openhands_pause) or the stop flag (the adapter's
event callback raises a stop when the flag is set between steps, which is what a callback-based control gets)."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from .. import telemetry
from ..handle import AgentHandle


class OpenHandsAgent:
    id = "openhands-sdk"

    def __init__(self, handle: AgentHandle, cfg: dict[str, Any]) -> None:
        _import_openhands_without_its_tracer()
        from openhands.sdk import LLM, Agent, Conversation, Tool
        from openhands.tools.file_editor import FileEditorTool
        from openhands.tools.terminal import TerminalTool

        self.handle = handle
        self.cfg = cfg
        self.workdir = Path(cfg["workdir"])
        self.workdir.mkdir(parents=True, exist_ok=True)
        # the request parameters come from the one source the environment record pins (serving.REQUEST_PARAMS)
        from ..serving import REQUEST_PARAMS

        rp = REQUEST_PARAMS[self.id]
        llm = LLM(model="openai/" + cfg["llm_model"], base_url=cfg["llm_url"], api_key=os.environ.get("MARK_LLM_API_KEY", "none"), temperature=rp["temperature"],
                  **({"seed": rp["seed"]} if rp.get("seed") is not None else {}))
        self.agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)])
        self.events: list[dict[str, Any]] = []
        self._conv = None
        self._lock = threading.Lock()

        def on_event(ev: Any) -> None:
            kind = type(ev).__name__
            # the message text (truncated) is kept so a conversation that ends without acting can be read afterwards
            self.events.append({"kind": kind, "mono_ns": telemetry.mono_ns() if hasattr(telemetry, "mono_ns") else 0, "summary": str(ev)[:300]})
            with telemetry.span(f"agent.openhands.{kind}", {"mark.agent": self.id}):
                if kind.endswith("ActionEvent"):
                    # one action (one shell command, one edit) is what OpenHands calls a turn, and it also advances on
                    # replies with no tool call -- which is why this count is evidence, not the id (A1). The turn every
                    # `markcall` inside the command carries comes from the harness's model proxy, which opened it when it
                    # forwarded the reply this action came from; the world executes one effect per such turn.
                    handle.agent_turn_advanced()
                    c = handle.record_call(kind, {"summary": str(ev)[:200], "agent_turn": handle.agent_turn})
                    handle.finish_call(c, True)
                if handle.should_stop() and self._conv is not None:
                    self._conv.pause()

        self._on_event = on_event
        self._Conversation = Conversation
        handle.on_revoke(self._revoke)

    def conversation_ref(self):
        return self._conv

    def _revoke(self) -> None:
        # Revocation for a shell-executing agent = pause and drop the conversation; nothing further can be issued.
        if self._conv is not None:
            self._conv.pause()

    def run(self, workload: dict[str, Any]) -> dict[str, Any]:
        # the OpenHands-shaped task (shell commands through `markcall`) when the workload provides one
        task = (workload.get("task_by_target") or {}).get(self.id) or workload["task"]
        # `markcall` lives next to this interpreter and the terminal tool inherits PATH, but an interactive shell
        # may reset PATH from its rc files (the Runpod image's root .bashrc does), so the pod also links it into
        # /usr/local/bin (pod/setup.sh). The prepend here covers a laptop run without that link.
        bindir = str(Path(sys.executable).parent)
        if bindir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
        with telemetry.span("agent.openhands.run", {"mark.agent": self.id, "mark.variant": (workload.get("variant") or "")}):
            self._conv = self._Conversation(agent=self.agent, workspace=str(self.workdir), callbacks=[self._on_event])
            self._conv.send_message(task)
            self._conv.run()
        return self._outcome()

    def _outcome(self) -> dict[str, Any]:
        st = getattr(self._conv, "state", None)
        return {"completed": bool(st) and str(getattr(st, "execution_status", "")).lower().endswith("finished"), "events": len(self.events), "status": str(getattr(st, "execution_status", None)),
                "last_events": self.events[-4:]}

    def resume(self) -> dict[str, Any]:
        with telemetry.span("agent.openhands.resume", {"mark.agent": self.id}):
            self._conv.run()
        return self._outcome()

    def inject(self, instruction: str) -> dict[str, Any]:
        from ..inject_states import acted, did_not_act, inject_failed

        if self._conv is None:
            return inject_failed("no conversation to inject into", error_class="NoConversation")
        before = len(self.handle.calls)
        with telemetry.span("agent.openhands.inject", {"mark.agent": self.id}):
            try:
                self._conv.send_message(instruction)
                if self.handle.should_stop():
                    return did_not_act("stop flag set: the adapter does not run the conversation after a halt")
                self._conv.run()
            except Exception as e:  # noqa: BLE001
                # e.g. ConversationRunError wrapping ContextWindowExceededError (attempt 2): the injected turn did not
                # complete, so there is no reading, whatever landed before the failure
                return inject_failed(e, attempted_calls=len(self.handle.calls) - before)
        n = len(self.handle.calls) - before
        if n > 0:
            return acted(n)
        last = next((e.get("summary", "") for e in reversed(self.events) if e.get("kind") == "MessageEvent"), "")
        return did_not_act(f"the injected run finished without a tool call; last agent message: {last[:200]}")

    def describe(self) -> dict[str, Any]:
        # fix B3: the children `markcall spawn` started, from the spawner's own record (ks.propagation's children_spawned)
        from ..children import CHILDREN_FILE_ENV, describe_children

        return {"agent": self.id, "events": len(self.events), **describe_children(os.environ.get(CHILDREN_FILE_ENV))}


_OTEL_KEYS = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_ENDPOINT", "LMNR_PROJECT_API_KEY")


def _import_openhands_without_its_tracer() -> None:
    """The harness is the instrument. The OpenHands SDK switches its own Laminar/OTLP exporter on at import time
    whenever it sees OTEL_EXPORTER_OTLP_ENDPOINT, and that exporter speaks gRPC by default: against the in-pod
    HTTP collector it fails and retries for the life of the process (seen on the H100 rehearsal, 2026-09-12),
    and if it succeeded it would put a second, uncalibrated set of spans into the archive. Import the SDK with
    those keys hidden, then pin its switch to off; the keys are restored so `markcall` in the terminal still
    exports to the collector through mark_platform.telemetry."""
    hidden = {k: os.environ.pop(k) for k in _OTEL_KEYS if k in os.environ}
    try:
        import openhands.sdk  # noqa: F401  (module-level maybe_init_laminar() runs here and sees no endpoint)
        from openhands.sdk.observability import laminar
        laminar.should_enable_observability = lambda: False
    finally:
        os.environ.update(hidden)


def build(handle: AgentHandle, cfg: dict[str, Any]) -> OpenHandsAgent:
    return OpenHandsAgent(handle, cfg)
