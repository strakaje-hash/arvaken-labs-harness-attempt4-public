"""The attempt 3 benchmark spec (freeze-6, 2026-09-15): 22 scheduled and 20 counted per evaluated cell (fix B6);
ks.propagation asked of scripted and openhands-sdk and not of langgraph-ref (fixes B3 and C1); a bench run on either
arm's model finds the bound rule's values for every model-driven single-call cell, and any other model is refused (fix A9)."""
import hashlib
from pathlib import Path

import yaml

from mark_platform.next_step import check_window_bounds
from mark_platform.runner import replication_schedule
from mark_platform.workloads import load

SPEC = yaml.safe_load((Path(__file__).resolve().parents[3] / "benchmarks" / "attempt3-agent-controls.yaml").read_text(encoding="utf-8"))


def _targets_for(probe_id):
    return sorted({t for cell in SPEC["matrix"] for t in cell["targets"] if any(p["id"] == probe_id for p in cell["probes"])})


def test_the_over_schedule_is_twenty_two_counting_twenty():
    assert SPEC["schema"] == "mark.benchmark/1" and SPEC["id"] == "attempt3-agent-controls"
    assert replication_schedule(SPEC) == (22, 20) and SPEC["reference_replications"] == 5


def test_propagation_is_asked_of_scripted_and_openhands_and_not_of_langgraph():
    assert _targets_for("ks.propagation") == ["openhands-sdk", "scripted"]
    for probe in ("ks.latency", "ks.completeness", "ks.mechanism", "ks.false_halt", "ks.resume"):
        assert _targets_for(probe) == ["langgraph-ref", "openhands-sdk", "scripted"], probe
    workloads = load()
    assert all(w in workloads for cell in SPEC["matrix"] for p in cell["probes"] for w in p["workloads"])


def test_a_bench_run_on_either_arms_model_has_every_bound_and_any_other_model_is_refused():
    for model in ("Qwen/Qwen2.5-7B-Instruct-AWQ", "Qwen/Qwen3-32B-FP8"):
        assert check_window_bounds(SPEC["matrix"], load(), model) == [], model
    refusals = check_window_bounds(SPEC["matrix"], load(), "gpt-oss-120b")
    assert any("'openhands-sdk'" in r for r in refusals) and any("'langgraph-ref'" in r for r in refusals)
    assert check_window_bounds(SPEC["matrix"], load(), "gpt-oss-120b", targets=["scripted"]) == []


def test_the_spec_names_the_pre_registration_by_its_current_sha256():
    """Founder ruling 2026-09-15: the pre-registration is markdown beside the spec, signed with the gate key. The spec's
    pre_registration field carries the file's sha256, so every run's results.json, which the manifest commits to by hash,
    cites it. Editing the record without updating the spec fails here."""
    prereg = Path(__file__).resolve().parents[3] / "benchmarks" / "attempt3-preregistration.md"
    digest = hashlib.sha256(prereg.read_bytes()).hexdigest()
    assert "benchmarks/attempt3-preregistration.md" in SPEC["pre_registration"] and f"sha256 {digest}" in SPEC["pre_registration"], (digest, SPEC["pre_registration"])
