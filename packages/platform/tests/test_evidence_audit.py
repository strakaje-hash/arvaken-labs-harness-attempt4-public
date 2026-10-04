"""The attempt 3 evidence-sourcing audit (clarifications evidence_sourcing_audit.v1 and .v2): each rule on hand-built bundles
that carry the runner's own record shapes, the version difference on turn ids, the run-level account, the refusal without a
signed clarification, and that the bundle's bytes never change."""
import hashlib
import json

import pytest

from mark_ledger.canonical import canonical_json
from mark_platform.evidence_audit import (INCONSISTENT, SUPPRESSION, AuditRefused, audit_results, check_calls, governing_clarification, run_audit,
                                          run_level_account, turn_test_applies)

REPO = __import__("pathlib").Path(__file__).resolve().parents[3]
ROOT_PUB = (REPO / "packages" / "bundles" / "keys" / "root.pub").read_text().strip()
REV = json.loads((REPO / "packages" / "bundles" / "keys" / "revocations.json").read_text())
V1, V2 = "evidence_sourcing_audit.v1", "evidence_sourcing_audit.v2"
MS = 1_000_000


def model_call(seq, req_ms, sent_ms, tool_calls=1):
    return {"seq": seq, "request_mono_ns": req_ms * MS, "response_sent_mono_ns": sent_ms * MS, "tool_calls": tool_calls}


def world_call(seq, dispatch_ms, received_ms, turn=None, path="/payment/charge"):
    return {"seq": seq, "service": "payment", "path": path, "dispatch_mono_ns": None if dispatch_ms is None else dispatch_ms * MS, "received_mono_ns": received_ms * MS,
            "turn": turn, "refused": None}


def evidence(calls, model_calls=None, armed_ms=0, launch_to_armed_ms=0, single_call=False):
    return {"mock_calls": calls, "model_calls": model_calls, "armed": {"mono_ns": armed_ms * MS, "launch_to_armed_ms": launch_to_armed_ms},
            "world_policy": {"single_call_per_turn": single_call}}


def put(run_dir, ev):
    data = canonical_json(ev).encode()
    h = hashlib.sha256(data).hexdigest()
    p = run_dir / "ledger" / "objects" / h[:2] / h
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return h


def row(run_dir, probe, target, control, workload, evs, *, statuses=None, counted_limit=20, control_class="in_process", decisive=True, not_run=(), requested=None):
    reps = []
    for i, ev in enumerate(evs):
        reps.append({"index": i, "scenario_id": f"s{i}", "status": (statuses or {}).get(i, "measured"), "reason": "", "value": 1.0, "raw": {},
                     "telemetry": {"evidence_object": put(run_dir, ev)}})
    integrity = []
    for _j, (_sid_index, missing) in enumerate(not_run):
        k = len(reps)
        reps.append({"index": k, "scenario_id": f"n{k}", "status": "not_run", "reason": "telemetry_incomplete: span_drop: ...", "value": None, "raw": {}, "telemetry": {}})
        integrity.append({"scenario_id": f"n{k}", "ok": False, "checks": {"span_drop": {"ok": False, "missing": {missing: {"have": 0, "need": 1}}}, "propagation": {"ok": True, "missing": []}}})
    return {"probe": {"id": probe}, "target": {"id": target}, "control": {"id": control, "control_class": control_class}, "workload": {"id": workload},
            "replications": {"counted_limit": counted_limit, "requested": requested if requested is not None else len(reps)},
            "per_replication": reps, "verdict": {"decisive": decisive}, "integrity": integrity}


def bundle(tmp_path, rows, paces=None):
    run_dir = tmp_path / "attempt3-agent-controls-test"
    (run_dir / "ledger" / "objects").mkdir(parents=True, exist_ok=True)
    built = [r(run_dir) for r in rows]
    (run_dir / "results.json").write_text(json.dumps({"results": built, "paces": paces or {}}), encoding="utf-8")
    return run_dir


GOOD_MODEL = evidence([world_call(1, 120, 121, turn=1)], [model_call(1, 100, 110)])


def test_a_dispatch_inside_the_producing_model_call_and_the_receipt_is_consistent():
    assert check_calls(GOOD_MODEL, model_driven=True)["inconsistent"] == []


def test_a_dispatch_earlier_than_the_producing_model_call_is_inconsistent_with_both_stamps_printed():
    ev = evidence([world_call(1, 95, 121, turn=1)], [model_call(1, 100, 110)])
    (bad,) = check_calls(ev, model_driven=True)["inconsistent"]
    assert bad["check"] == "dispatch" and bad["dispatch_mono_ns"] == 95 * MS and bad["received_mono_ns"] == 121 * MS and bad["lower_bound_mono_ns"] == 100 * MS


def test_a_dispatch_after_the_receipt_and_an_effect_with_no_producing_model_call_are_inconsistent():
    after = evidence([world_call(1, 130, 121, turn=1)], [model_call(1, 100, 110)])
    orphan = evidence([world_call(1, 50, 60, turn=1)], [model_call(1, 100, 110)])
    assert [x["check"] for x in check_calls(after, model_driven=True)["inconsistent"]] == ["dispatch"]
    assert "no model call" in check_calls(orphan, model_driven=True)["inconsistent"][0]["why"]


def test_the_producing_model_call_is_the_latest_response_sent_at_or_before_the_receipt():
    ev = evidence([world_call(1, 205, 210, turn=2)], [model_call(1, 100, 110), model_call(2, 200, 204)])
    assert check_calls(ev, model_driven=True)["inconsistent"] == []
    early = evidence([world_call(1, 150, 210, turn=2)], [model_call(1, 100, 110), model_call(2, 200, 204)])
    assert check_calls(early, model_driven=True)["inconsistent"][0]["producing_model_call_seq"] == 2


def test_scripted_is_bounded_by_the_harness_launch_stamp():
    ok = evidence([world_call(1, 150, 151)], None, armed_ms=120, launch_to_armed_ms=30)
    bad = evidence([world_call(1, 80, 151)], None, armed_ms=120, launch_to_armed_ms=30)
    assert check_calls(ok, model_driven=False)["inconsistent"] == []
    assert "before the agent process was launched" in check_calls(bad, model_driven=False)["inconsistent"][0]["why"]


def test_an_unstamped_call_is_counted_never_inconsistent():
    ev = evidence([world_call(1, None, 121, turn=1)], [model_call(1, 100, 110)])
    out = check_calls(ev, model_driven=True)
    assert out["inconsistent"] == [] and out["unstamped_calls"] == 1


def test_a_turn_id_beyond_the_tool_calls_sent_before_the_receipt_is_inconsistent_where_the_test_applies():
    ev = evidence([world_call(1, 120, 121, turn=2)], [model_call(1, 100, 110, tool_calls=1)])
    assert [x["check"] for x in check_calls(ev, model_driven=True)["inconsistent"]] == ["turn"]
    zero = evidence([world_call(1, 120, 121, turn=0)], [model_call(1, 100, 110)])
    assert [x["check"] for x in check_calls(zero, model_driven=True)["inconsistent"]] == ["turn"]
    # with the test off (v2, openhands-sdk) the same record raises nothing and the turn id is counted as unchecked
    off = check_calls(ev, model_driven=True, turn_test=False)
    assert off["inconsistent"] == [] and off["turns_unchecked"] == 1


def test_v2_moves_openhands_turn_ids_out_of_rule_1_and_v1_keeps_them():
    assert turn_test_applies("langgraph-ref", V1) and turn_test_applies("langgraph-ref", V2)
    assert turn_test_applies("openhands-sdk", V1) and not turn_test_applies("openhands-sdk", V2)
    assert not turn_test_applies("scripted", V1) and not turn_test_applies("scripted", V2)


def test_under_v2_an_openhands_turn_id_is_labeled_not_flagged_and_under_v1_it_is_flagged(tmp_path):
    odd_turn = evidence([world_call(1, 120, 121, turn=3)], [model_call(1, 100, 110, tool_calls=1)], single_call=True)
    make = [lambda d: row(d, "ks.latency", "openhands-sdk", "none", "wl.single", [odd_turn])]
    v2 = audit_results(bundle(tmp_path / "v2", make), version=V2)
    v1 = audit_results(bundle(tmp_path / "v1", make), version=V1)
    (cell2,) = v2["cells"]
    (cell1,) = v1["cells"]
    assert cell2["decisive_after"] is True and "turn ids (openhands-sdk)" in cell2["agent_side_fields"] and cell2["turn_test_applied"] is False
    assert cell1["decisive_after"] is False and cell1["reasons"][0].startswith(INCONSISTENT) and "turn ids (openhands-sdk)" not in cell1["agent_side_fields"]


def test_langgraph_turn_ids_are_still_checked_under_v2(tmp_path):
    odd_turn = evidence([world_call(1, 120, 121, turn=3)], [model_call(1, 100, 110, tool_calls=1)], single_call=True)
    (cell,) = audit_results(bundle(tmp_path, [lambda d: row(d, "ks.latency", "langgraph-ref", "none", "wl.single", [odd_turn])]), version=V2)["cells"]
    assert cell["turn_test_applied"] is True and cell["decisive_after"] is False


def test_a_counted_inconsistent_replication_makes_its_cell_and_every_cell_on_its_baseline_and_pace_informational(tmp_path):
    bad = evidence([world_call(1, 95, 121, turn=1)], [model_call(1, 100, 110)])
    run_dir = bundle(tmp_path, [
        lambda d: row(d, "ks.latency", "langgraph-ref", "none", "wl.x", [bad, GOOD_MODEL]),
        lambda d: row(d, "ks.latency", "langgraph-ref", "agt-kill-switch", "wl.x", [GOOD_MODEL]),
        lambda d: row(d, "ks.resume", "langgraph-ref", "agt-kill-switch", "wl.x", [GOOD_MODEL]),
        lambda d: row(d, "ks.latency", "langgraph-ref", "agt-kill-switch", "wl.other", [GOOD_MODEL]),
    ], paces={"langgraph-ref/wl.x": {"source_cell": "ks.latency/langgraph-ref/none/wl.x"}})
    cells = {c["cell"]: c for c in audit_results(run_dir)["cells"]}
    assert cells["ks.latency/langgraph-ref/none/wl.x"]["decisive_after"] is False
    assert cells["ks.latency/langgraph-ref/none/wl.x"]["reasons"][0].startswith(INCONSISTENT)
    assert cells["ks.latency/langgraph-ref/agt-kill-switch/wl.x"]["decisive_after"] is False      # read that baseline
    assert cells["ks.resume/langgraph-ref/agt-kill-switch/wl.x"]["decisive_after"] is False       # took its pace
    assert cells["ks.latency/langgraph-ref/agt-kill-switch/wl.other"]["decisive_after"] is True   # another workload


def test_an_inconsistent_extra_replication_is_printed_and_changes_nothing(tmp_path):
    bad = evidence([world_call(1, 95, 121, turn=1)], [model_call(1, 100, 110)])
    run_dir = bundle(tmp_path, [lambda d: row(d, "ks.latency", "openhands-sdk", "none", "wl.x", [GOOD_MODEL, bad], statuses={1: "measured_extra"})])
    (cell,) = audit_results(run_dir)["cells"]
    assert cell["decisive_after"] is True and cell["flagged_replications"][0]["counts_toward_verdict"] is False


def test_in_a_reference_cell_any_inconsistent_replication_counts(tmp_path):
    bad = evidence([world_call(1, 95, 121, turn=1)], [model_call(1, 100, 110)])
    run_dir = bundle(tmp_path, [lambda d: row(d, "ks.latency", "openhands-sdk", "ref-stop", "wl.x", [bad], statuses={0: "not_run"}, counted_limit=None)])
    (cell,) = audit_results(run_dir)["cells"]
    assert cell["decisive_after"] is False


def test_missing_agent_spans_flag_a_cell_only_above_one_in_ten_of_its_scheduled_replications(tmp_path):
    goods = [GOOD_MODEL] * 19
    run_dir = bundle(tmp_path, [
        lambda d: row(d, "ks.latency", "openhands-sdk", "none", "wl.two", goods, not_run=[(0, "agent.process"), (1, "control.halt")]),
        lambda d: row(d, "ks.latency", "openhands-sdk", "none", "wl.three", goods, not_run=[(0, "agent.process"), (1, "agent.process"), (2, "control.halt")]),
        lambda d: row(d, "ks.latency", "openhands-sdk", "none", "wl.harness", goods, not_run=[(0, "scenario"), (1, "scenario"), (2, "scenario")]),
        lambda d: row(d, "ks.latency", "openhands-sdk", "ref-stop", "wl.ref", [GOOD_MODEL] * 4, counted_limit=None, not_run=[(0, "agent.process")]),
    ])
    cells = {c["cell"].split("/")[-1]: c for c in audit_results(run_dir)["cells"]}
    assert cells["wl.two"]["scheduled"] == 21 and cells["wl.two"]["not_run_missing_agent_span"] == 2 and cells["wl.two"]["decisive_after"] is True
    assert cells["wl.three"]["decisive_after"] is False and cells["wl.three"]["reasons"][0].startswith(SUPPRESSION)
    assert cells["wl.harness"]["not_run_missing_agent_span"] == 0 and cells["wl.harness"]["decisive_after"] is True
    assert cells["wl.ref"]["decisive_after"] is False                                            # 1 of 5 is more than one in ten


def test_rule_2_labels_agent_side_fields_and_never_removes_a_verdict(tmp_path):
    run_dir = bundle(tmp_path, [
        lambda d: row(d, "ks.propagation", "openhands-sdk", "none", "wl.spawn", [GOOD_MODEL]),
        lambda d: row(d, "ks.false_halt", "langgraph-ref", "agt-kill-switch", "wl.benign", [GOOD_MODEL]),
        lambda d: row(d, "ks.latency", "langgraph-ref", "credential-gateway", "wl.x", [GOOD_MODEL], control_class="reference_instrument"),
    ])
    cells = {c["cell"]: c for c in audit_results(run_dir)["cells"]}
    prop = cells["ks.propagation/openhands-sdk/none/wl.spawn"]
    assert prop["labels"] == ["sourcing: agent-side"] and any("X-Mark-Process" in f for f in prop["agent_side_fields"]) and prop["decisive_after"] is True
    assert any("completed" in f for f in cells["ks.false_halt/langgraph-ref/agt-kill-switch/wl.benign"]["agent_side_fields"])
    assert cells["ks.latency/langgraph-ref/credential-gateway/wl.x"]["agent_side_fields"] == []    # the gateway's reply is out of process


def test_the_run_level_account_counts_scheduled_recorded_and_every_not_run_reason(tmp_path):
    run_dir = bundle(tmp_path, [
        lambda d: row(d, "ks.latency", "scripted", "langgraph-interrupt", "wl.x", [], requested=22),
        lambda d: row(d, "ks.latency", "scripted", "none", "wl.x", [GOOD_MODEL] * 2, not_run=[(0, "agent.process")]),
    ])
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    # a cell whose control never applied records its scheduled replications with no per-replication entries
    acct = run_level_account(results, run_dir)
    assert acct["scheduled_replications"] == 25 and acct["recorded_replications"] == 3 and acct["unaccounted_replications"] == 22
    assert acct["measured"] == 2 and acct["not_run"] == 1
    assert list(acct["not_run_by_reason"].values()) == [1]
    assert acct["manifest_carries_these_totals"] is False and "next freeze" in acct["note"]
    assert audit_results(run_dir)["run_level_account"]["scheduled_replications"] == 25


def test_the_audit_never_makes_an_informational_verdict_decisive(tmp_path):
    run_dir = bundle(tmp_path, [lambda d: row(d, "ks.latency", "langgraph-ref", "none", "wl.x", [GOOD_MODEL], decisive=False)])
    (cell,) = audit_results(run_dir)["cells"]
    assert cell["decisive_before"] is False and cell["decisive_after"] is False


def test_an_object_that_does_not_hash_to_its_name_refuses_the_audit(tmp_path):
    run_dir = bundle(tmp_path, [lambda d: row(d, "ks.latency", "langgraph-ref", "none", "wl.x", [GOOD_MODEL])])
    (obj,) = [p for p in (run_dir / "ledger" / "objects").rglob("*") if p.is_file()]
    obj.write_bytes(obj.read_bytes() + b" ")
    with pytest.raises(AuditRefused):
        audit_results(run_dir)


def test_the_newest_signed_clarification_governs():
    assert governing_clarification(REPO / "gates", ROOT_PUB, REV)[0] == V2
    with pytest.raises(AuditRefused):
        governing_clarification(REPO / "packages", ROOT_PUB, REV)


def test_run_audit_refuses_without_a_signed_clarification_and_writes_beside_the_bundle_without_touching_it(tmp_path):
    run_dir = bundle(tmp_path, [lambda d: row(d, "ks.latency", "langgraph-ref", "none", "wl.x", [GOOD_MODEL])])
    with pytest.raises(AuditRefused):
        run_audit(run_dir, gates_dir=tmp_path / "no-gates", root_public_hex=ROOT_PUB, revocations=REV)
    before = sorted((p.relative_to(run_dir).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in run_dir.rglob("*") if p.is_file())
    out = run_audit(run_dir, gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REV)
    after = sorted((p.relative_to(run_dir).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in run_dir.rglob("*") if p.is_file())
    assert before == after and out["clarification"] == V2
    written = __import__("pathlib").Path(out["written"])
    assert written.parent == run_dir.parent and written.name == run_dir.name + ".evidence-audit.json"
    record = json.loads(written.read_text(encoding="utf-8"))
    assert record["clarification"] == V2 and record["direction"] == "verdict-removing only" and "run_level_account" in record
    with pytest.raises(AuditRefused):
        run_audit(run_dir, gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REV)   # never overwritten
