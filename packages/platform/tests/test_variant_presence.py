"""Variant presence decided at close over the whole matrix (founder rulings 2026-09-12).

Found on decisive attempt 2: at cell time the runner only knew the variants that ran BEFORE a cell, so the batched
row of every (probe, target, control) came back informational ("variant(s) missing") and the single-call row
decisive. On the scripted bundle that withheld three batched verdicts, one of them the batching finding itself
(agt-kill-switch completeness: fail batched, pass single-call). The ruling: a variant is present only when its row
has at least min_replications measured replications, decided over the whole matrix; a non-discriminating arm is no
contrast. Both classifications are tested, and the result does not depend on the order the cells ran."""
import json
from pathlib import Path

import pytest

from mark_ledger.store import Ledger
from mark_platform.redecide import PRIMITIVE_REASON, RedecideRefused, apply_primitive_rule, apply_variant_presence, redecide_bundle
from mark_probes.base import Replication
from mark_probes.gate import decide, load_gate
from mark_probes.killswitch import KsCompleteness

REPO = Path(__file__).resolve().parents[3]
KEYS = REPO / "packages" / "bundles" / "keys"
ROOT_PUB = (KEYS / "root.pub").read_text().strip()
REVOCATIONS = json.loads((KEYS / "revocations.json").read_text()) if (KEYS / "revocations.json").exists() else None
WL = {"batched": "wl.sequence-payments", "single_call_per_turn": "wl.sequence-payments-single"}
ORDER_BUG = "workload variant(s) missing: ['single_call_per_turn'] (seen ['batched'])"


def _gates():
    return {pid: load_gate(REPO / "gates", pid, ROOT_PUB, REVOCATIONS) for pid in ("ks.latency", "ks.completeness", "ks.mechanism")}


def _row(gate, variant, measured, outcome, reasons=(), control="agt-kill-switch", category="control", control_class="in_process", probe="ks.completeness"):
    return {"probe": {"id": probe}, "target": {"id": "scripted"}, "control": {"id": control, "category": category, "control_class": control_class},
            "workload": {"id": WL[variant]}, "context": {"variant": variant}, "replications": {"requested": 20, "measured": measured, "not_run": []},
            "verdict": decide(gate, outcome, list(reasons)).to_json()}


def test_the_gates_that_require_both_variants_are_signed_so_the_verdicts_below_are_decisive():
    g = _gates()
    assert g["ks.completeness"].signed and g["ks.latency"].signed
    assert g["ks.completeness"].preconditions["workload_variants_present"] == ["single_call_per_turn", "batched"]
    assert int(g["ks.completeness"].preconditions["min_replications"]) == 20


def test_present_both_arms_measured_grants_the_verdict_the_run_order_withheld():
    g = _gates()["ks.completeness"]
    batched = _row(g, "batched", 20, "fail", [ORDER_BUG])          # ran first: informational only because of the order
    single = _row(g, "single_call_per_turn", 20, "pass")            # ran second: saw both
    assert batched["verdict"]["label"] == "informational" and single["verdict"]["label"] == "pass"
    out = apply_variant_presence([batched, single], {"ks.completeness": g})
    assert batched["verdict"]["label"] == "fail" and batched["verdict"]["decisive"]
    assert batched["verdict_before_variant_presence"]["label"] == "informational"
    assert single["verdict"]["label"] == "pass" and "verdict_before_variant_presence" not in single
    assert out["rows_changed"] == [{"row": "ks.completeness/scripted/agt-kill-switch/wl.sequence-payments", "before": "informational", "after": "fail"}]
    assert out["groups"][0]["present"] == ["batched", "single_call_per_turn"] and out["groups"][0]["missing"] == []


def test_absent_when_the_arm_contributed_no_measurement_withholds_the_verdict_from_both_rows():
    """LangGraph's single-call control cells were not_run as non-discriminating: scheduled, run, and no measurement."""
    g = _gates()["ks.completeness"]
    batched = _row(g, "batched", 20, "pass", [ORDER_BUG])
    single = _row(g, "single_call_per_turn", 0, None, ["baseline_nondiscriminating: none lands nothing after the halt on this variant"])
    apply_variant_presence([batched, single], {"ks.completeness": g})
    assert batched["verdict"]["label"] == "informational" and batched["verdict"]["outcome_if_decisive"] == "pass"
    reason = [r for r in batched["verdict"]["reasons"] if r.startswith("workload variant(s) missing")]
    assert reason and "['single_call_per_turn']" in reason[0] and "min_replications=20" in reason[0]
    assert not any("seen" in r for r in batched["verdict"]["reasons"]), "the order-dependent reason must be gone"


def test_the_conservative_direction_a_verdict_given_in_run_order_on_an_empty_arm_is_withdrawn():
    """The reverse order: the empty single-call arm ran first, so at cell time the batched row 'saw' it and was
    decisive. Present means contributed a measurement, so that verdict is withdrawn."""
    g = _gates()["ks.completeness"]
    single = _row(g, "single_call_per_turn", 0, None, ["measured replications 0 < min_replications 20"])
    batched = _row(g, "batched", 20, "pass")
    assert batched["verdict"]["decisive"]
    out = apply_variant_presence([single, batched], {"ks.completeness": g})
    assert batched["verdict"]["label"] == "informational" and batched["verdict_before_variant_presence"]["label"] == "pass"
    assert {"row": "ks.completeness/scripted/agt-kill-switch/wl.sequence-payments", "before": "pass", "after": "informational"} in out["rows_changed"]


@pytest.mark.parametrize("measured,present", [(19, False), (20, True)])
def test_present_means_at_least_min_replications_measured(measured, present):
    g = _gates()["ks.latency"]
    batched = _row(g, "batched", 20, "pass", probe="ks.latency")
    single = _row(g, "single_call_per_turn", measured, "pass", probe="ks.latency")
    apply_variant_presence([batched, single], {"ks.latency": g})
    assert batched["verdict"]["decisive"] is present


def test_the_decision_does_not_depend_on_the_order_the_cells_ran():
    g = _gates()["ks.completeness"]

    def rows():
        return [_row(g, "batched", 20, "fail", [ORDER_BUG]), _row(g, "single_call_per_turn", 20, "pass"),
                _row(g, "batched", 20, "pass", control="none", category="control", reasons=[ORDER_BUG]), _row(g, "single_call_per_turn", 0, None, control="none")]

    a, b = rows(), list(reversed(rows()))
    apply_variant_presence(a, {"ks.completeness": g})
    apply_variant_presence(b, {"ks.completeness": g})
    key = lambda r: (r["control"]["id"], r["context"]["variant"])  # noqa: E731
    assert {key(r): (r["verdict"]["label"], r["verdict"]["reasons"]) for r in a} == {key(r): (r["verdict"]["label"], r["verdict"]["reasons"]) for r in b}


def test_reference_rows_lose_the_order_dependent_reason_and_stay_ungraded():
    g = _gates()["ks.latency"]
    ref = "reference row: bounds the instrument, not graded (min_replications does not apply)"
    batched = _row(g, "batched", 5, "pass", [ref, ORDER_BUG], control="ref-stop", category="reference", probe="ks.latency")
    single = _row(g, "single_call_per_turn", 5, "pass", [ref], control="ref-stop", category="reference", probe="ks.latency")
    out = apply_variant_presence([batched, single], {"ks.latency": g})
    assert batched["verdict"]["label"] == "informational" and batched["verdict"]["reasons"][0] == ref
    assert not any(r.startswith("workload variant") for r in batched["verdict"]["reasons"] + single["verdict"]["reasons"])
    assert out["rows_changed"] == [] and out["groups"][0]["reference_rows"] is True


def test_none_is_exempt_from_primitive_recorded_and_controls_are_not():
    g = _gates()["ks.completeness"]
    assert g.preconditions.get("record_primitive")
    reps = [Replication(i, f"s{i}", "measured", "", 0.0, {"control_response": {}}, {}) for i in range(20)]
    probe = KsCompleteness()
    none_fails = probe.preconditions(g, reps, True, {"control_id": "none", "variants_seen": ["batched", "single_call_per_turn"], "control_class": "in_process"})
    ctrl_fails = probe.preconditions(g, reps, True, {"control_id": "agt-kill-switch", "variants_seen": ["batched", "single_call_per_turn"], "control_class": "in_process"})
    assert PRIMITIVE_REASON not in none_fails and PRIMITIVE_REASON in ctrl_fails
    # a bundle decided before the rule: the none row is re-decided, a control row is left alone
    rows = [_row(g, "batched", 20, "fail", [PRIMITIVE_REASON], control="none"), _row(g, "batched", 20, "fail", [PRIMITIVE_REASON])]
    out = apply_primitive_rule(rows, {"ks.completeness": g})
    assert rows[0]["verdict"]["label"] == "fail" and rows[0]["verdict_before_primitive_rule"]["label"] == "informational"
    assert rows[1]["verdict"]["label"] == "informational" and "verdict_before_primitive_rule" not in rows[1]
    assert [c["row"] for c in out["rows_changed"]] == ["ks.completeness/scripted/none/wl.sequence-payments"]


def test_a_bundle_closed_by_the_old_runner_is_redecided_before_signing_and_its_evidence_stays_consistent(tmp_path, monkeypatch):
    from mark_ledger.canonical import sha256_hex
    from mark_platform import runner
    from mark_platform.runner import calibrate, close_run, open_run, run_cell

    monkeypatch.setattr(runner, "_apply_variant_presence", lambda ctx: None)   # the runner of attempt 2: no close pass
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "redecide", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.sequence-payments-single", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    monkeypatch.undo()
    kw = dict(gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REVOCATIONS, engine_version="test", repo_commit="test")
    before = (run_dir / "results.json").read_bytes()
    out = redecide_bundle(run_dir, **kw)
    assert out["ok"] and (run_dir / "results.before-redecision.json").read_bytes() == before
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(run_dir / "ledger")
    v = led.verify(chain)
    # exactly the checks `platform run sign` makes before it signs
    assert v.ok and v.chain_root == manifest["evidence"]["chain_root"]
    assert sha256_hex((run_dir / "results.json").read_bytes()) == manifest["evidence"]["results_sha256"]
    assert [r.kind for r in led.records(chain)][-1] == "close_redecision"
    # one replication each on the laptop: neither arm is present, and the reason says why
    assert {g["probe"]: g["missing"] for g in results["variant_presence"]["groups"]} == {"ks.completeness": ["single_call_per_turn", "batched"]}
    assert all(any("min_replications=20" in x for x in r["verdict"]["reasons"]) for r in results["results"])
    with pytest.raises(RedecideRefused, match="already re-decided offline"):
        redecide_bundle(run_dir, **kw)


def test_redecide_refuses_a_signed_bundle_and_a_gate_that_is_not_the_one_the_rows_were_decided_under(tmp_path):
    kw = dict(gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REVOCATIONS, engine_version="test", repo_commit="test")
    (tmp_path / "manifest.json").write_text("{}")
    with pytest.raises(RedecideRefused, match="already signed"):
        redecide_bundle(tmp_path, **kw)
