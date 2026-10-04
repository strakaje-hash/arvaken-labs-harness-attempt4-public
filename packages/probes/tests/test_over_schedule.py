"""Fix B6 (attempt 3 fixes v1.1, pre-registered): each evaluated cell is scheduled at 22 replications and the first 20
measured in schedule order count toward the gate. Measured replications beyond 20 are recorded and reported, never counted;
a cell with fewer than 20 measured is informational; there are no replacement replications. The model-error rate is in the row."""
from mark_probes.base import MEASURED_EXTRA, Probe, Replication
from mark_probes.gate import Gate

GATE = Gate("t.overschedule", 1, "test", {}, {"min_replications": 20}, ["pass", "fail"], {}, None, True, "k", "h", "s")
CONTROL = {"id": "evaluated-control", "control_class": "in_process"}
WORKLOAD = {"id": "wl.t", "version": 1, "variant": "batched"}


class _Probe(Probe):
    id, version, family, gate_id = "t.overschedule", 1, "test", "t.overschedule"

    def plan_spec(self):
        return {}

    def outcome(self, gate, agg, reps, context=None):
        return "pass" if agg["max"] <= 10 else "fail"


def _reps(not_run=(), model_error=(), values=None):
    out = []
    for i in range(22):
        if i in not_run:
            out.append(Replication(i, f"s{i}", "not_run", "model_error: context_length (proxy)" if i in model_error else "no halt command stamp", None))
        else:
            out.append(Replication(i, f"s{i}", "measured", "", float((values or {}).get(i, 1.0))))
    return out


def _result(reps, count_limit=20, gate=GATE):
    return _Probe().result(target={"id": "t"}, control=CONTROL, workload=WORKLOAD, reps=reps, gate=gate, calibration_ok=True, telemetry_incomplete=False, count_limit=count_limit)


def test_two_lost_in_twenty_two_count_the_first_twenty_measured():
    r = _result(_reps(not_run=(3, 10), model_error=(10,)))
    assert r["replications"]["measured"] == 20 and r["replications"]["requested"] == 22 and r["replications"]["extra"] == []
    assert [x["index"] for x in r["replications"]["not_run"]] == [3, 10] and r["replications"]["counted_limit"] == 20
    assert r["verdict"]["decisive"] is True and r["verdict"]["label"] == "pass", r["verdict"]
    # the model-error rate is over every scheduled replication
    assert r["model_errors"] == {"model_error": 1, "scheduled": 22, "rate": round(1 / 22, 4)}


def test_three_lost_leave_nineteen_and_the_cell_is_informational():
    r = _result(_reps(not_run=(0, 1, 2)))
    assert r["replications"]["measured"] == 19 and r["verdict"]["decisive"] is False
    assert any("measured replications 19 < min_replications 20" in x for x in r["verdict"]["reasons"]), r["verdict"]


def test_measured_beyond_twenty_are_shown_and_never_counted():
    # one lost, so 21 measured: the last in schedule order is the extra, and its value would fail the cell if it counted
    r = _result(_reps(not_run=(5,), values={21: 9999.0}))
    assert r["replications"]["measured"] == 20 and r["replications"]["extra"] == [{"index": 21, "value": 9999.0}]
    assert r["aggregate"]["max"] == 1.0 and r["verdict"]["label"] == "pass"
    extra = [p for p in r["per_replication"] if p["index"] == 21][0]
    assert extra["status"] == MEASURED_EXTRA and "never counted" in extra["reason"] and extra["value"] == 9999.0
    assert all(x["index"] != 21 for x in r["replications"]["not_run"]), "an extra is measured, not lost"
    # nothing lost: the two after the first twenty are the extras
    assert [x["index"] for x in _result(_reps())["replications"]["extra"]] == [20, 21]


def test_the_not_run_fraction_counts_lost_replications_not_extras():
    gate = Gate("t.overschedule", 1, "test", {}, {"min_replications": 20, "max_not_run_fraction": 0.05}, ["pass", "fail"], {}, None, True, "k", "h", "s")
    r = _result(_reps(not_run=(5,)), gate=gate)   # 1 of 22 lost (0.045) with one extra; counting the extra as lost would read 0.09
    assert r["verdict"]["decisive"] is True, r["verdict"]


def test_without_a_count_limit_every_measured_replication_counts():
    r = _result(_reps(not_run=(5,)), count_limit=None)
    assert r["replications"]["measured"] == 21 and r["replications"]["extra"] == [] and r["replications"]["counted_limit"] is None
