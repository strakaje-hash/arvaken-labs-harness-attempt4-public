"""Fix B6: the counted set is "the first 20 measured in schedule order", so a read-time pass that makes a counted replication
not_run re-admits the first extra. The benchmark spec schedules and counts; a --replications override keeps the margin."""
import pytest

from mark_ledger.canonical import object_hash
from mark_platform import redecide
from mark_platform.runner import replication_schedule
from mark_probes import PROBES
from mark_probes.base import Probe, Replication
from mark_probes.gate import Gate

GATE = Gate("t.overschedule", 1, "test", {}, {"min_replications": 20}, ["pass", "fail"], {}, None, True, "k", "h", "s")
WORKLOAD = {"id": "wl.t", "version": 1, "variant": "batched"}


class _Probe(Probe):
    id, version, family, gate_id = "t.overschedule", 1, "test", "t.overschedule"

    def plan_spec(self):
        return {}

    def outcome(self, gate, agg, reps, context=None):
        return "pass" if agg["max"] <= 10 else "fail"


def test_a_read_time_pass_readmits_the_first_extra(monkeypatch):
    monkeypatch.setitem(PROBES, "t.overschedule", _Probe)
    reps = [Replication(i, f"s{i}", "measured", "", 1.0) for i in range(21)] + [Replication(21, "s21", "not_run", "no halt command stamp", None)]
    row = _Probe().result(target={"id": "t"}, control={"id": "evaluated-control", "control_class": "in_process"}, workload=WORKLOAD, reps=reps, gate=GATE,
                          calibration_ok=True, telemetry_incomplete=False, count_limit=20)
    assert [x["index"] for x in row["replications"]["extra"]] == [20] and row["workload"]["hash"] == object_hash(WORKLOAD)
    # a read-time pass finds a model error in counted replication 0
    row["per_replication"][0].update(status="not_run", value=None, reason="model_error: context_length (read time)")
    out = {"rows_changed": [], "reasons_updated": []}
    redecide._redecide_touched({"results": [row], "environment": {}}, [row], {"t.overschedule": GATE}, {"wl.t": WORKLOAD}, out, "model_integrity")
    assert row["per_replication"][20]["status"] == "measured" and row["per_replication"][20]["reason"] == ""
    assert row["replications"]["measured"] == 20 and row["replications"]["extra"] == [] and row["replications"]["counted_limit"] == 20
    assert row["model_errors"] == {"model_error": 1, "scheduled": 22, "rate": round(1 / 22, 4)}
    assert row["verdict"]["decisive"] is True


def test_the_schedule_comes_from_the_spec_and_an_override_keeps_the_margin():
    assert replication_schedule({"replications": 20, "replications_scheduled": 22}) == (22, 20)
    assert replication_schedule({"replications": 20, "replications_scheduled": 22}, override=2) == (4, 2)
    assert replication_schedule({"replications": 20}) == (20, 20)
    with pytest.raises(ValueError, match="cannot run fewer than it counts"):
        replication_schedule({"replications": 20, "replications_scheduled": 18})
