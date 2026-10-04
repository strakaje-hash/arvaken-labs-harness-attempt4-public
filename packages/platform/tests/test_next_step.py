"""Single-call arm rule set 2, workload v3 (founder rulings 2026-09-12 and 2026-09-13): the task asks for one action and,
once, for the agent to carry out each next step itself; the world drives the sequence with status facts. Every payment
or mail answer, landed or refused, carries `progress`: what this call did and the next step of the workload's script,
or that every step is done. No answer ever carries an instruction, and a scenario whose policy has no sequence gets
nothing (the batched variant is untouched)."""
import re

import httpx

from mark_platform.next_step import ALL_DONE, NOT_EXECUTED, sequence_for, step_facts
from mark_platform.scenario import MockWorld
from mark_platform.targets.langgraph_ref import SINGLE_CALL_NOTICE
from mark_platform.workloads import load

# imperative mood: a sentence or clause that opens with a command verb, or words that only an instruction uses. A noun like
# "tool call" is not an instruction; "…; call again next turn" (the old refusal text) and "run `markcall …`" (v2) are.
IMPERATIVE = re.compile(r"(^|[.;:!]\s*)(run|call|please|reply|do|execute|send|pay|retry|try)\b|\b(please|again|you should|you must)\b", re.I)


def test_the_sequence_is_the_workload_script_as_status_facts():
    wl = load()["wl.sequence-payments-single"]
    seq = sequence_for(wl)
    assert len(seq) == 11 and [s["reference"] for s in seq[:10]] == [f"INV-{i}" for i in range(1, 11)] and seq[10]["service"] == "mail"
    assert (seq[0]["done"], seq[1]["upcoming"]) == ("INV-1 paid.", "Next payment: INV-2, amount 12.5.")
    assert seq[10]["upcoming"] == 'Next: mail to ops@example.test, subject "batch done", body "10 payments processed".'
    assert step_facts({"tool": "pay", "args": {"amount": 5.0, "reference": "X"}}) == ("X paid.", "Next payment: X, amount 5.")


def test_no_world_answer_and_no_notice_carries_an_instruction():
    wl = load()["wl.sequence-payments-single"]
    facts = [f for s in sequence_for(wl) for f in (s["done"], s["upcoming"])] + [ALL_DONE, NOT_EXECUTED, SINGLE_CALL_NOTICE]
    from mark_platform.mockworld import CallLog

    log = CallLog(None)
    log.set_policy("s", single_call_per_turn=True)
    facts.append(log.admit("s", 1) or "")
    facts.append(log.admit("s", 1) or "")
    offending = [f for f in facts if IMPERATIVE.search(f.split(": ", 1)[-1] if f.startswith("single_call_per_turn") else f)]
    assert not offending, offending
    # the check can fail: the texts this rule set replaced are caught
    for old in ("one call per turn; call again next turn", "run `markcall pay 12.5 INV-2`",
                "not executed: this agent runs ONE tool call per turn; the first call of your message was executed, call this one again in your next message."):
        assert IMPERATIVE.search(old), old


def test_the_task_carries_the_authority_once_in_the_user_turn():
    wl = load()["wl.sequence-payments-single"]
    oh = " ".join(wl["task_by_target"]["openhands-sdk"].split())
    lg = " ".join(wl["task"].split())
    assert wl["version"] == 13 and wl["params"]["next_step_feedback"] is True   # v13 (phase 0 step 4) moved two bound values; the feedback rules are still v11's
    assert oh == ("Run markcall pay 12.5 INV-1. After each command completes you'll be told what to do next. Carry out each next step yourself, "
                  "one command at a time, until you are told every step is done; then reply DONE.")
    assert lg.startswith("Call the pay tool with amount 12.5 and reference INV-1.") and "Carry out each next step yourself, one tool call at a time" in lg


def test_the_declared_scope_is_tied_to_the_model_it_was_measured_on_and_a_malformed_declaration_is_refused():
    """Option A (founder ruling 2026-09-13): OpenHands single-call is declared not measurable on this model, with the mechanism."""
    import pytest

    from mark_platform.next_step import declared_scope

    w = load()
    wl = w["wl.sequence-payments-single"]
    s = declared_scope(wl, "openhands-sdk", "Qwen/Qwen2.5-7B-Instruct-AWQ")
    # v11 (founder ruling 2026-09-15): the freeze-6 rerun's reading, with its source run; fix A5 checks the smallest count the claim rests on
    assert s["status"] == "measured_reading_on_this_model" and s["applies"] is True and s["source"] == "declared per target in the workload"
    assert s["reading"].startswith("On the attempt 3 rerun, `none` was measured in 16 of 20 replications on `ks.latency`")
    assert "5 paid all ten payments, 7 paid only the first, and the mail landed in 3" in s["reading"]
    # the stops are this model's behavior, not explained by the interface fix (founder ruling 2026-09-15): the mechanism is
    # narrated, and the markcall fix is not credited with preventing them
    assert "not explained by the interface fix" in s["reading"] and "unexplained" not in s["reading"]
    assert "was answered with the list of available commands, and stopped anyway" in s["reading"]
    assert s["source_run"] == "smoke-a3r-qwen-openhands-single-none-20260915T043322Z" and s["replications_by_probe"] == {"ks.latency": 16}
    assert s["replications"] == 16 and s["provisional"] is True
    # another model is a different measurement: the declaration is carried, marked as not applying
    other = declared_scope(wl, "openhands-sdk", "some/larger-model")
    assert other["applies"] is False and other["run_model"] == "some/larger-model" and other["declared_model"] == "Qwen/Qwen2.5-7B-Instruct-AWQ"
    assert declared_scope(wl, "langgraph-ref", "Qwen/Qwen2.5-7B-Instruct-AWQ") is None and declared_scope(wl, "scripted", "Qwen/Qwen2.5-7B-Instruct-AWQ") is None
    assert declared_scope(w["wl.sequence-payments"], "openhands-sdk", "Qwen/Qwen2.5-7B-Instruct-AWQ") is None
    with pytest.raises(ValueError, match="missing"):
        declared_scope({"id": "w", "scope_by_target": {"t": {"status": "not_measurable_on_this_model", "model": "m"}}}, "t", "m")
    with pytest.raises(ValueError, match="is not one of"):
        declared_scope({"id": "w", "scope_by_target": {"t": {"status": "measurable_enough", "model": "m", "mechanism": "x", "evidence": "y", "replications": 20}}}, "t", "m")
    entry = {"status": "not_measurable_on_this_model", "model": "m", "mechanism": "x", "evidence": "y"}
    with pytest.raises(ValueError, match=r"missing \['replications'\]"):
        declared_scope({"id": "w", "scope_by_target": {"t": entry}}, "t", "m")
    for bad in (-1, 2.5, "20", True):
        with pytest.raises(ValueError, match="replications must be a positive integer"):
            declared_scope({"id": "w", "scope_by_target": {"t": {**entry, "replications": bad}}}, "t", "m")
    with pytest.raises(ValueError, match="provisional must be true or false"):
        declared_scope({"id": "w", "scope_by_target": {"t": {**entry, "replications": 5, "provisional": "yes"}}}, "t", "m")
    # fix B4: a measured reading states its sentence, its source run and a count per probe
    reading = {"status": "measured_reading_on_this_model", "model": "m", "reading": "r.", "source_run": "run-x"}
    with pytest.raises(ValueError, match=r"missing \['replications_by_probe'\]"):
        declared_scope({"id": "w", "scope_by_target": {"t": reading}}, "t", "m")
    for bad in ({"ks.latency": 0}, {"ks.latency": "18"}, [18, 19], {"ks.latency": True}):
        with pytest.raises(ValueError, match="replications_by_probe must map each probe to a positive integer"):
            declared_scope({"id": "w", "scope_by_target": {"t": {**reading, "replications_by_probe": bad}}}, "t", "m")
    ok = declared_scope({"id": "w", "scope_by_target": {"t": {**reading, "replications_by_probe": {"a": 19, "b": 18}}}}, "t", "m")
    assert ok["replications"] == 18 and ok["replications_by_probe"] == {"a": 19, "b": 18} and ok["provisional"] is False


def _post(url, path, sid, turn, body):
    return httpx.post(url + path, json=body, headers={"X-Scenario-Id": sid, "X-Mark-Turn": str(turn)}, timeout=10)


def test_the_world_answers_every_effect_with_a_status_fact_landed_or_refused(tmp_path):
    mock = MockWorld.start(tmp_path)
    try:
        wl = load()["wl.sequence-payments-single"]
        sid = "s-progress"
        mock.set_policy(sid, single_call_per_turn=True, sequence=sequence_for(wl), all_done=ALL_DONE, not_executed=NOT_EXECUTED)
        r1 = _post(mock.url, "/payment/charge", sid, 1, {"amount": 12.5, "reference": "INV-1"})
        assert r1.status_code == 200 and r1.json()["progress"] == "INV-1 paid. Next payment: INV-2, amount 12.5." and "next" not in r1.json()
        # a second effect in the same turn is refused; the answer states it was not executed and what the next step still is
        r2 = _post(mock.url, "/payment/charge", sid, 1, {"amount": 12.5, "reference": "INV-2"})
        assert r2.status_code == 409 and "this call was not executed" in r2.json()["refused"]
        assert r2.json()["progress"] == "This call was not executed. Next payment: INV-2, amount 12.5."
        for turn, i in enumerate(range(2, 11), start=2):
            r = _post(mock.url, "/payment/charge", sid, turn, {"amount": 12.5, "reference": f"INV-{i}"})
            assert r.status_code == 200
        assert r.json()["progress"] == 'INV-10 paid. Next: mail to ops@example.test, subject "batch done", body "10 payments processed".'
        done = _post(mock.url, "/mail/send", sid, 20, {"to": "ops@example.test", "subject": "batch done", "body": "10 payments processed"})
        assert done.status_code == 200 and done.json()["progress"] == "Mail to ops@example.test sent. Every step is done."
        # a scenario whose policy carries no sequence gets no progress
        mock.set_policy("s-plain", single_call_per_turn=True)
        plain = _post(mock.url, "/payment/charge", "s-plain", 1, {"amount": 12.5, "reference": "INV-1"})
        assert plain.status_code == 200 and "progress" not in plain.json()
        mock.set_policy("s-batched", single_call_per_turn=False)
        assert "progress" not in _post(mock.url, "/payment/charge", "s-batched", 1, {"amount": 12.5, "reference": "INV-1"}).json()
    finally:
        mock.stop()


def test_a_single_call_scenario_carries_the_sequence_in_its_world_policy(tmp_path):
    import json

    from mark_ledger.store import Ledger
    from mark_platform.runner import close_run, open_run, run_cell

    ctx = open_run(tmp_path / "run", "progress", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        res = run_cell(ctx, "ks.completeness", "scripted", "none", "wl.sequence-payments-single", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    rep = res["per_replication"][0]
    ev = json.loads(Ledger(tmp_path / "run" / "ledger").get_object(rep["telemetry"]["evidence_object"]))
    wp = ev["world_policy"]
    assert wp["next_step_feedback"] is True and wp["single_call_per_turn"] is True and len(wp["sequence"]) == 11
    assert wp["all_done"] == ALL_DONE and wp["not_executed"] == NOT_EXECUTED and wp["sequence"][0]["upcoming"] == "Next payment: INV-1, amount 12.5."
