"""Fix A5 (attempt 3 fixes v1.1, 2026-09-14): a declared scope cites the replications it rests on. On attempt 2b the
OpenHands single-call line "not measurable on this model" came from a smoke of 5 replications, and an N=20 reading narrowed
it. The workload loader never sees the gates, so the rule is enforced when a run opens: resting on fewer replications than a
gate's min_replications, the declaration must say it is provisional."""
from pathlib import Path

import pytest
import yaml

from mark_platform.next_step import check_declared_scopes
from mark_platform.runner import gate_min_replications
from mark_platform.workloads import load

REPO = Path(__file__).resolve().parents[3]
SPEC = yaml.safe_load((REPO / "benchmarks" / "oss-agent-controls-v1.yaml").read_text(encoding="utf-8"))
SINGLE = "wl.sequence-payments-single"


def _with_scope(workloads, target, **entry):
    wl = workloads[SINGLE]
    base = {"status": "not_measurable_on_this_model", "model": "m", "mechanism": "a stand-in mechanism", "evidence": "a stand-in run"}
    return {**workloads, SINGLE: {**wl, "scope_by_target": {**(wl.get("scope_by_target") or {}), target: {**base, **entry}}}}


def test_the_floor_comes_from_the_signed_gates():
    floors = gate_min_replications(["ks.latency", "ks.completeness", "ks.mechanism", "ks.resume"])
    assert floors == {"ks.latency": 20, "ks.completeness": 20, "ks.mechanism": 20, "ks.resume": 20}


def test_an_unqualified_declaration_below_the_floor_is_refused_and_the_qualified_form_accepted():
    floors = gate_min_replications(sorted({p["id"] for c in SPEC["matrix"] for p in c["probes"]}))
    w = load()
    # the repo's own declaration rests on 5 replications and says it is provisional: the run may open
    assert check_declared_scopes(SPEC["matrix"], w, floors) == []
    unqualified = _with_scope(w, "openhands-sdk", replications=5)
    refusals = check_declared_scopes(SPEC["matrix"], unqualified, floors)
    # one refusal per probe the run asks of openhands-sdk on the single-call workload, each naming its floor
    assert len(refusals) == 4 and all("openhands-sdk" in r and "on 5 replications" in r and "min_replications 20" in r and "without saying provisional" in r for r in refusals), refusals
    assert check_declared_scopes(SPEC["matrix"], _with_scope(w, "openhands-sdk", replications=5, provisional=True), floors) == []
    assert check_declared_scopes(SPEC["matrix"], _with_scope(w, "openhands-sdk", replications=20), floors) == []
    # only the targets and probes the run asks
    assert check_declared_scopes(SPEC["matrix"], unqualified, floors, targets=["scripted"]) == []
    assert len(check_declared_scopes(SPEC["matrix"], unqualified, floors, probes=["ks.latency"])) == 1


def test_the_rule_applies_to_a_measured_reading_too():
    """Fix B4: A5's rule covers any quantitative claim in a scope line; a reading checks its smallest count."""
    floors = gate_min_replications(sorted({p["id"] for c in SPEC["matrix"] for p in c["probes"]}))
    w = load()
    wl = w[SINGLE]
    unqualified = {**w, SINGLE: {**wl, "scope_by_target": {"openhands-sdk": {**wl["scope_by_target"]["openhands-sdk"], "provisional": False}}}}
    refusals = check_declared_scopes(SPEC["matrix"], unqualified, floors)
    assert len(refusals) == 4 and all("measured_reading_on_this_model on 16 replications" in r for r in refusals), refusals


def test_a_bench_run_refuses_before_it_opens(tmp_path, monkeypatch):
    from mark_platform import workloads as workloads_module
    from mark_platform.cli import main

    real = workloads_module.load
    monkeypatch.setattr(workloads_module, "load", lambda path=None: _with_scope(real(path), "scripted", replications=3))
    run_dir = tmp_path / "refused"
    with pytest.raises(SystemExit, match=r"bench run refused \(fix A5\).*declares scripted .* on 3 replications"):
        main(["--tools", "inproc", "bench", "run", str(REPO / "benchmarks" / "oss-agent-controls-v1.yaml"), "--run-dir", str(run_dir), "--targets", "scripted", "--replications", "1"])
    assert not run_dir.exists(), "the refusal comes before the run opens anything"
