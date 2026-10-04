"""The agent process. One per scenario replication. Launched by the harness (scenario.py), inside the sandbox
on the pod, as:

  python -m mark_platform.agentproc --target <id> --control <id> --workload <path.json> --port-file <path> --result <path>

Environment from the harness: MARK_SCENARIO_ID, OTEL_TRACEPARENT/OTEL_BAGGAGE (trace continuity), MARK_MOCK_URL,
MARK_LLM_URL, MARK_LLM_MODEL, MARK_WORKDIR, MARK_TRACE_JSONL (this process's span archive), MARK_CONTROL_CFG (json).
The listener port is written to --port-file once the control is armed and BEFORE the workload starts, so the
harness can never halt an agent that is not yet guarded (which would measure the harness, not the control).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from . import telemetry
from .clock import mono_ns
from .control_channel import ControlListener
from .handle import AgentHandle


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True)
    p.add_argument("--control", required=True)
    p.add_argument("--workload", required=True)
    p.add_argument("--port-file", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--registry", default=None)
    a = p.parse_args(argv)

    from .registry import load

    reg = load(a.registry)
    target_t, control_t = reg[a.target], reg[a.control]
    workload = json.loads(Path(a.workload).read_text(encoding="utf-8"))
    scenario_id = telemetry.attach_from_env() or workload.get("scenario_id") or "unknown"
    telemetry.init(f"agent:{a.target}", jsonl_path=os.environ.get("MARK_TRACE_JSONL"))
    if scenario_id:
        telemetry.scenario_context(scenario_id)

    cfg = {
        "mock_url": os.environ.get("MARK_MOCK_URL", "http://127.0.0.1:8081"),
        "llm_url": os.environ.get("MARK_LLM_URL", "http://127.0.0.1:8000/v1"),
        "llm_model": os.environ.get("MARK_LLM_MODEL", ""),
        "workdir": os.environ.get("MARK_WORKDIR", str(Path.cwd() / "work")),
        "scenario_id": scenario_id,
        "tools_mode": os.environ.get("MARK_TOOLS", "mcp"),
    }
    control_cfg = json.loads(os.environ.get("MARK_CONTROL_CFG") or "{}")
    # The turn file (MARK_TURN_FILE) is written by the harness -- the model proxy for a model-driven target, the
    # scripted driver for the reference -- never by this handle (attempt 4, A1). The handle keeps the agent's own count.
    handle = AgentHandle(agent_id=f"did:mark:{a.target}:{scenario_id}", session_id=scenario_id)
    result: dict[str, Any] = {"target": a.target, "control": a.control, "scenario_id": scenario_id, "pid": os.getpid()}

    with telemetry.span("agent.process", {"mark.target": a.target, "mark.control": a.control, "mark.scenario_id": scenario_id}) as root_span:
        try:
            target_mod = importlib.import_module(target_t.launch_module)
            target = target_mod.build(handle, cfg)
            # Framework-native controls may need a reference into the agent (e.g. the OpenHands conversation).
            control_cfg.setdefault("conversation_ref", getattr(target, "conversation_ref", None))
            control_mod = importlib.import_module(control_t.launch_module)
            control = control_mod.build(handle, control_cfg)
        except Exception as e:  # noqa: BLE001
            result.update({"status": "not_run", "reason": f"launch failed: {type(e).__name__}: {e}", "traceback": traceback.format_exc()[-4000:]})
            Path(a.result).write_text(json.dumps(result, indent=1), encoding="utf-8")
            return 3

        state: dict[str, Any] = {"run_outcome": None, "resume_outcomes": [], "inject_outcomes": [], "halts": [], "resumes": [], "continuations": [],
                                 "main_run_finished": False, "last_activity": time.monotonic()}
        run_lock = threading.Lock()

        def h_halt(body: dict[str, Any]) -> dict[str, Any]:
            out = control.halt(body)
            state["halts"].append({**out, "received_mono_ns": body.get("received_mono_ns"), "command_mono_ns": body.get("command_mono_ns")})
            state.setdefault("first_halt_monotonic", time.monotonic())
            return out

        def h_resume(body: dict[str, Any]) -> dict[str, Any]:
            out = control.resume(body)
            state["resumes"].append(out)
            # continue the agent in the background so the channel answers immediately. A running resume is an open control
            # call until target.resume() returns (fix A1, 2026-09-14): the linger loop below cannot expire while it is in
            # flight, and its end is activity. On attempt 2b the resume thread was neither, so the process left 3 s after the
            # last continuation and killed LangGraph's resumed graph mid-run (40 of 40 single-call replications). Counted
            # before the thread starts, so there is no gap in which the loop could see nothing in flight.
            with inflight_lock:
                inflight["n"] += 1

            def go():
                try:
                    telemetry.attach_from_env()   # a new thread starts without the scenario's trace context
                    with run_lock:
                        state["resume_outcomes"].append(target.resume())
                finally:
                    state["last_activity"] = time.monotonic()
                    with inflight_lock:
                        inflight["n"] -= 1

            threading.Thread(target=go, daemon=True).start()
            return out

        inflight = {"n": 0}
        inflight_lock = threading.Lock()

        def h_inject(body: dict[str, Any]) -> dict[str, Any]:
            from .inject_states import TURN_BOUND_S, inject_failed, normalize, turn_timeout

            telemetry.attach_from_env()
            with inflight_lock:
                inflight["n"] += 1
            try:
                # The injected turn starts when the agent's current run has yielded (founder ruling 2026-09-12): wait for
                # it, bounded. A run that does not yield within the bound is turn_timeout, never not_attempted. Before
                # this, OpenHands' inject ran the conversation while its main run was still going.
                asked = mono_ns()
                if not run_lock.acquire(timeout=TURN_BOUND_S):
                    out = turn_timeout(TURN_BOUND_S, "the agent's current run did not yield")
                else:
                    try:
                        prior = mono_ns()
                        with telemetry.span("agent.inject", {"mark.instruction": str(body.get("instruction", ""))[:200]}):
                            try:
                                out = normalize(target.inject(str(body.get("instruction", ""))))
                            except Exception as e:  # noqa: BLE001
                                out = inject_failed(e)
                        out = {**out, "prior_turn_completed_mono_ns": prior, "waited_for_prior_turn_ms": (prior - asked) / 1e6}
                    finally:
                        run_lock.release()
                state["inject_outcomes"].append(out)
                state["last_activity"] = time.monotonic()
                return out
            finally:
                with inflight_lock:
                    inflight["n"] -= 1

        def h_continue(body: dict[str, Any]) -> dict[str, Any]:
            """A harness continuation (founder ruling 2026-09-13): a fixed user prompt the harness sends when the agent's
            conversation has finished and steps remain. It goes through the agent's own input path, exactly as a new user
            message would, under the same turn lock and bound as an inject; it opens a new turn id and is recorded as
            harness-initiated, never as the agent's own."""
            from .inject_states import TURN_BOUND_S, inject_failed, normalize, turn_timeout

            telemetry.attach_from_env()
            with inflight_lock:
                inflight["n"] += 1
            try:
                asked = mono_ns()
                if not run_lock.acquire(timeout=TURN_BOUND_S):
                    out = turn_timeout(TURN_BOUND_S, "the agent's current run did not yield")
                else:
                    try:
                        # A1: the continuation opens no turn itself. The model call it provokes does, through the harness's
                        # proxy; what is recorded here is the turn in force when it was sent and when its run yielded, read
                        # from the file the harness writes, plus the agent's own count as evidence.
                        from .turns import TurnFile

                        tf = TurnFile.from_env()
                        turn_at_send = tf.read() if tf is not None else None
                        started = mono_ns()
                        with telemetry.span("agent.harness_continuation", {"mark.turn_at_send": turn_at_send if turn_at_send is not None else -1, "mark.initiated_by": "harness_continuation"}):
                            try:
                                out = normalize(target.inject(str(body.get("text", ""))))
                            except Exception as e:  # noqa: BLE001
                                out = inject_failed(e)
                        turn = tf.read() if tf is not None else None
                        out = {**out, "turn": turn, "turn_at_send": turn_at_send, "agent_turn": handle.agent_turn, "initiated_by": "harness_continuation",
                               "started_mono_ns": started, "waited_for_prior_turn_ms": (started - asked) / 1e6, "completed_mono_ns": mono_ns()}
                    finally:
                        run_lock.release()
                state["continuations"].append(out)
                state["last_activity"] = time.monotonic()
                return out
            finally:
                with inflight_lock:
                    inflight["n"] -= 1

        def h_status(body: dict[str, Any]) -> dict[str, Any]:
            return {**handle.status(), "control": control.describe(), "target": target.describe(), "run_outcome": state["run_outcome"],
                    "main_run_finished": state["main_run_finished"], "run_in_progress": run_lock.locked(), "continuations_answered": len(state["continuations"])}

        listener = ControlListener({"halt": h_halt, "resume": h_resume, "inject": h_inject, "continue": h_continue, "status": h_status}).start()
        # A3: this process's pid, for the harness to corroborate against the pid it launched (a venv launcher on Windows makes
        # them differ) and then register as the agent every call's OS identity is classified against. Written before the port
        # file, which is what the harness waits on, so it is there when the harness looks.
        Path(a.port_file).with_name("agent.pid").write_text(str(os.getpid()), encoding="utf-8")
        Path(a.port_file).write_text(str(listener.port), encoding="utf-8")
        root_span.set_attribute("mark.control_port", listener.port)
        root_span.set_attribute("mark.armed_mono_ns", mono_ns())

        try:
            with run_lock:
                state["run_outcome"] = target.run(workload)
            result["status"] = "ok"
        except Exception as e:  # noqa: BLE001
            result.update({"status": "error", "reason": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-4000:]})
        state["main_run_finished"] = True
        state["last_activity"] = time.monotonic()
        handle.finished.set()
        # Stay alive so a post-run halt/inject/resume/continuation can still be delivered and answered (ks.mechanism,
        # ks.resume, the single-call arm's harness continuations).
        linger_s = float(os.environ.get("MARK_LINGER_S", "3"))

        from .inject_states import CHANNEL_MARGIN_S, TURN_BOUND_S

        # Stay while a control call is in flight (an inject, a continuation, a resume until target.resume() returns), or while
        # there was activity (the run, an inject, a continuation, a finished resume) within the last linger_s: each one restarts
        # that clock, so a harness-driven sequence is not cut off after the first run. The hard bound keeps a stuck call from
        # holding the process alive.
        hard_end = time.monotonic() + max(TURN_BOUND_S + CHANNEL_MARGIN_S, float(os.environ.get("MARK_AGENT_MAX_LINGER_S", "900")))
        # A resume the harness will send after a hold (fix A1): set by the harness only when the scenario's plan resumes. After
        # a halt the process waits for it, bounded by the hold plus a margin, so a hold longer than linger_s cannot outlast it.
        await_resume_s = float(os.environ.get("MARK_AWAIT_RESUME_S") or 0)

        def awaiting_resume() -> bool:
            halted_at = state.get("first_halt_monotonic")
            return bool(await_resume_s and halted_at is not None and not state["resumes"] and time.monotonic() < halted_at + await_resume_s)

        while (time.monotonic() < state["last_activity"] + linger_s or inflight["n"] > 0 or awaiting_resume()) and time.monotonic() < hard_end:
            time.sleep(0.05)
        listener.stop()
        result.update({"run_outcome": state["run_outcome"], "halts": state["halts"], "resumes": state["resumes"], "resume_outcomes": state["resume_outcomes"],
                       "inject_outcomes": state["inject_outcomes"], "continuations": state["continuations"], "calls": [c.to_json() for c in handle.calls], "handle": handle.status(), "control": control.describe(), "target": target.describe(),
                       # single-instrument precondition: which tracers were live in this process (the harness's must be the only one)
                       "instrument_check": telemetry.instrument_check(),
                       # the harness-assigned turn in force at exit, and the agent's own count beside it (A1)
                       "turns": _harness_turn(), "agent_turns": handle.agent_turn})
    telemetry.force_flush()
    Path(a.result).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return 0 if result.get("status") == "ok" else 1


def _harness_turn() -> int | None:
    from .turns import TurnFile

    tf = TurnFile.from_env()
    return tf.read() if tf is not None else None


if __name__ == "__main__":
    sys.exit(main())
