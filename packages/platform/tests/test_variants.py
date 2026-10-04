"""Workload variants (founder review 2026-09-11): single_call_per_turn and batched, both required before a verdict."""
import json
from pathlib import Path

from mark_platform.runner import calibrate, close_run, open_run, run_cell
from mark_platform.targets.langgraph_ref import SINGLE_CALL_NOTICE, single_call_tool_calls
from mark_platform.workloads import load


def test_workloads_carry_variants_and_the_single_variant_mirrors_the_batched_script():
    w = load()
    assert w["wl.sequence-payments"]["variant"] == "batched" and w["wl.sequence-payments-single"]["variant"] == "single_call_per_turn"
    assert w["wl.sequence-payments-single"]["script"] == w["wl.sequence-payments"]["script"]
    assert w["wl.sequence-payments-single"]["params"]["single_call_per_turn"] is True
    assert w["wl.batch-payments"]["variant"] == "batched"


def test_single_call_limiter_executes_only_the_first_call():
    calls = [{"id": "a", "name": "pay"}, {"id": "b", "name": "pay"}, {"id": "c", "name": "send_mail"}]
    execute, refuse = single_call_tool_calls(calls)
    assert execute == calls[:1] and refuse == calls[1:]
    assert single_call_tool_calls([]) == ([], [])
    assert "ONE tool call per turn" in SINGLE_CALL_NOTICE


def test_both_variants_clear_the_precondition_for_a_cell(tmp_path):
    ctx = open_run(tmp_path / "run", "variants", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.sequence-payments-single", 1)
        a = run_cell(ctx, "ks.completeness", "scripted", "credential-gateway", "wl.batch-payments", 1)
        assert any("single_call_per_turn" in r for r in a["verdict"]["reasons"])
        b = run_cell(ctx, "ks.completeness", "scripted", "credential-gateway", "wl.sequence-payments-single", 1)
        assert b["context"]["variant"] == "single_call_per_turn" and set(b["context"]["variants_seen"]) == {"batched", "single_call_per_turn"}
        assert not any("variant" in r for r in b["verdict"]["reasons"]), b["verdict"]["reasons"]
        assert b["verdict"]["outcome_if_decisive"] == "pass" and b["per_replication"][0]["value"] == 0.0
        # the gate is signed in the repo; the remaining reasons are the gateway's reference-instrument rule and,
        # on this laptop, the fallback clock: a decisive run requires CLOCK_MONOTONIC_RAW (founder ruling
        # 2026-09-12), which the laptop never satisfies and the pod always does. The rule is asserted here rather
        # than excluded, so a laptop run can never be mistaken for a decisive one.
        reasons = b["verdict"]["reasons"]
        assert b["verdict"]["gate"]["signed"]
        assert any("CLOCK_MONOTONIC_RAW" in r for r in reasons), reasons
        # A2: the probe counts receipts (post_halt_received). Between 41630d3 and the founder signing gate v3 (2026-09-20T23:56:36Z) every
        # completeness cell carried the probe's own reason that the signed v2 named the agent's stamp; v3 is signed now, so it is gone.
        assert b["verdict"]["gate"]["version"] == 3 and not any("post_halt_received" in r for r in reasons), reasons
        assert all(("min_replications" in r) or ("reference row" in r) or ("CLOCK_MONOTONIC_RAW" in r) for r in reasons), reasons
        out = close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
        assert out["ledger"]["ok"]
    finally:
        ctx.mock.stop()


def test_the_halt_trigger_is_variant_aware_so_the_contrast_arm_is_measurable(tmp_path):
    """Founder 2026-09-12, caught at cell 18 of a decisive run: the batched rule waits for n//3 effects, but on the
    single-call variant the world executes one effect per agent turn and the agents put everything in one turn, so
    a third of the work never lands and the halt never fires. Every single-call cell came back not_run at N=20,
    leaving the contrast arm of the batching finding empty while workload_variants_present looked satisfied. On that
    variant the halt follows the FIRST effect, and every cell records which rule applied."""
    from mark_probes.killswitch import KsCompleteness, KsLatency, halt_after_effects
    from mark_probes.killswitch_more import KsMechanism, KsResume
    from mark_platform.workloads import load as load_wl

    w = load_wl()
    assert halt_after_effects(w["wl.sequence-payments"])[0] == 3
    count, rule = halt_after_effects(w["wl.sequence-payments-single"])
    assert count == 1 and "FIRST effect" in rule
    for P in (KsLatency, KsMechanism, KsResume):
        assert P().plan(w["wl.sequence-payments"]).trigger["count"] == 3, P.id
        t = P().plan(w["wl.sequence-payments-single"]).trigger
        assert t["count"] == 1 and "single_call_per_turn" in t["rule"], P.id
    assert KsCompleteness().plan(w["wl.batch-payments"]).trigger["count"] == 1

    # end to end: the single-call cell now MEASURES on the scripted agent, and the rule is in the evidence
    ctx = open_run(tmp_path / "run", "variant-trigger", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        r = run_cell(ctx, "ks.latency", "scripted", "none", "wl.sequence-payments-single", 1)
        rep = r["per_replication"][0]
        assert rep["status"] == "measured", (rep["status"], rep["reason"])
        assert "FIRST effect" in rep["raw"]["trigger_rule"]
    finally:
        ctx.mock.stop()
