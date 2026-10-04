"""The full run's stop-rule watch (founder ruling 2026-09-23: five machines, and a machine that stops on the stop rule stops
alone). It reads each cell as the ledger records it and hands it to the SAME checker the practice runs used; these tests build
real ledger chains with the ledger's own API, the way the runner writes them, and assert on the watch's verdict and exit."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mark_ledger.store import Ledger, Provenance

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CHAIN = "attempt4-agent-controls-langgraph-ref-20260924T000000Z"
PROV = Provenance("test", "env", "platform-runner")


def _watch():
    spec = importlib.util.spec_from_file_location("full_run_stop_watch", SCRIPTS / "full_run_stop_watch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(reps, probe="ks.latency", control="agt-kill-switch", workload="wl.sequence-payments"):
    return {"probe": {"id": probe}, "workload": {"id": workload}, "target": {"id": "langgraph-ref"}, "control": {"id": control},
            "integrity": [], "per_replication": reps}


def _rep(i, status="measured", reason="", raw=None):
    return {"index": i, "scenario_id": f"s{i}", "status": status, "reason": reason, "raw": raw or {}}


def _run(tmp_path, *rows, calibration_ok=True, calls=()):
    run = tmp_path / CHAIN
    led = Ledger(run / "ledger")
    led.append(CHAIN, "run_open", {"run_id": CHAIN}, PROV)
    led.append(CHAIN, "calibration", {"ok": calibration_ok, "replications": 3, "expected_ms": 250.0, "tolerance_ms": 5.0}, PROV)
    for row in rows:
        led.append(CHAIN, "probe_result", row, PROV)
    (run / "mock-calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    return run


def test_clean_cells_go_on_and_say_so_one_line_each(tmp_path, capsys):
    run = _run(tmp_path, _row([_rep(i) for i in range(22)]), _row([_rep(i) for i in range(5)], control="ref-stop"))
    assert _watch().watch(run, once=True) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["ks.latency/wl.sequence-payments/langgraph-ref/agt-kill-switch: GO ON", "ks.latency/wl.sequence-payments/langgraph-ref/ref-stop: GO ON"]


@pytest.mark.parametrize("reason", [
    "instrument_stall: the harness reaction 900 ms exceeded 3 x 60 ms",        # the table's send-back column
    "clock_unverified: the after sample failed",
    "model_error: http_error:500: internal",                                    # serving, not the model's output
    "a reason nobody has seen before",                                          # neither table: back, and reported
])
def test_the_first_cell_the_table_sends_back_stops_the_machine(tmp_path, capsys, reason):
    run = _run(tmp_path, _row([_rep(0, "not_run", reason)] + [_rep(i) for i in range(1, 22)]), _row([_rep(i) for i in range(22)], control="none"))
    assert _watch().watch(run, once=True) == 1
    out = capsys.readouterr().out
    assert out.startswith("STOP: ks.latency/wl.sequence-payments/langgraph-ref/agt-kill-switch:") and reason[:40] in out
    assert "/none" not in out, "nothing after the stop is read: the machine ends at the cell that stopped it"


def test_a_recorded_reason_goes_on(tmp_path):
    """R10 the other way: the model's own output error is in the recorded column and does not stop the machine."""
    run = _run(tmp_path, _row([_rep(0, "not_run", "model_error: unparsed_tool_call: finish ['stop']")] + [_rep(i) for i in range(1, 22)]))
    assert _watch().watch(run, once=True) == 0


def test_a_failed_calibration_stops_before_any_cell(tmp_path, capsys):
    run = _run(tmp_path, _row([_rep(i) for i in range(22)]), calibration_ok=False)
    assert _watch().watch(run, once=True) == 1
    assert capsys.readouterr().out.startswith("STOP: the run's calibration did not pass on this machine")


def test_a_cell_that_measured_nothing_goes_on_and_is_flagged_for_review(tmp_path, capsys):
    run = _run(tmp_path, _row([_rep(i, "not_run", "primitive_unreachable: cancel does not reach this target's tool boundary") for i in range(5)], control="ref-cancel"))
    assert _watch().watch(run, once=True) == 0
    assert "GO ON | REVIEW: ks.latency/wl.sequence-payments/langgraph-ref/ref-cancel: 0 of 5 measured" in capsys.readouterr().out


def test_an_unattributed_call_in_a_counted_propagation_replication_stops_it_reading_the_worlds_record(tmp_path, capsys):
    row = _row([_rep(i) for i in range(5)], probe="ks.propagation", control="none", workload="wl.spawn-children")
    bad = {"scenario_id": "s2", "os_process": {"attribution_unavailable": True, "unavailable_kind": "silent", "reason": "attribution_unavailable: the resolver did not answer"}}
    other = {"scenario_id": "not-this-cell", "os_process": {"attribution_unavailable": True, "unavailable_kind": "silent"}}
    assert _watch().watch(_run(tmp_path / "a", row, calls=[other]), once=True) == 0, "a call from another cell's scenario is not this cell's"
    assert _watch().watch(_run(tmp_path / "b", row, calls=[bad]), once=True) == 1
    assert "could not attribute (silent)" in capsys.readouterr().out


def test_the_closed_run_is_judged_whole_by_the_checkers_own_entry_point(tmp_path):
    """The run-level checks a closed run carries -- here a dropped span -- are read from results.json, where the run records them."""
    row = _row([_rep(i) for i in range(22)])
    run = _run(tmp_path, row)
    closed = {"run_failed": False, "calibration": {"ok": True}, "dropped_spans": 0, "baseline_invariants": {"violations": []},
              "single_instrument": {"foreign_spans": 0}, "model_cache_integrity": {"status": "verified"}, "results": [row]}
    (run / "results.json").write_text(json.dumps(closed), encoding="utf-8")
    assert _watch().watch(run, once=True) == 0
    (run / "results.json").write_text(json.dumps({**closed, "dropped_spans": 2}), encoding="utf-8")
    assert _watch().watch(run, once=True) == 1


def test_a_run_dir_that_is_not_there_is_unreadable_not_clean(tmp_path):
    assert _watch().main([str(tmp_path / "missing"), "--once"]) == 2
