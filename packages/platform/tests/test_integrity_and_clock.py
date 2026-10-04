"""Telemetry integrity (Task 4.3) on synthetic span archives: injected loss is flagged, misordering is flagged,
a calibration outside tolerance is flagged; and the clock has the resolution the tolerance needs."""
from mark_platform.clock import mono_ns, resolution_ns
from mark_platform.integrity import check_calibration, check_ordering, check_propagation, check_span_drop, evaluate

T = "a" * 32


def _span(name, start, end, service="harness", parent=None, sid=None, extra=None):
    return {"trace_id": T, "span_id": sid or name + "-id", "parent_span_id": parent, "name": name, "service": service, "start_wall_ns": start, "end_wall_ns": end,
            "attributes": {"mark.start_mono_ns": start, "mark.end_mono_ns": end, **(extra or {})}, "events": [], "status": "UNSET"}


def _archive():
    return [
        _span("scenario", 0, 10_000_000_000, sid="s"),
        _span("agent.process", 1_000_000, 9_000_000_000, service="agent:scripted", parent="s", sid="a"),
        _span("harness.halt_command", 2_000_000_000, 2_010_000_000, parent="s", sid="h"),
        _span("control.halt", 2_002_000_000, 2_003_000_000, service="agent:scripted", parent="h", sid="c"),
        *[_span("mock.calibration", 3_000_000_000 + i * 300_000_000, 3_000_000_000 + i * 300_000_000 + 251_000_000, service="mock-world", parent="a", sid=f"m{i}") for i in range(5)],
    ]


def test_clock_resolution_supports_a_5ms_tolerance():
    assert resolution_ns() < 1_000_000, "clock resolution must be well under 1 ms"
    a, b = mono_ns(), mono_ns()
    assert b >= a


def test_complete_archive_passes():
    rep = evaluate(_archive(), T, expected_spans={"scenario": 1, "agent.process": 1, "harness.halt_command": 1, "control.halt": 1, "mock.calibration": 5},
                   required_services=["harness", "agent:scripted", "mock-world"], calibration={"expected_ms": 250, "tolerance_ms": 5, "min_samples": 5})
    assert rep.ok, rep.to_json()


def test_injected_span_loss_is_flagged():
    spans = [s for s in _archive() if s["name"] != "control.halt"]
    rep = evaluate(spans, T, expected_spans={"scenario": 1, "control.halt": 1}, required_services=["harness"])
    assert not rep.ok and rep.checks["span_drop"]["missing"] == {"control.halt": {"have": 0, "need": 1}}
    spans = _archive()[:-1]  # one calibration sample dropped
    rep = evaluate(spans, T, expected_spans={"mock.calibration": 5}, required_services=["harness"])
    assert not rep.ok and "mock.calibration" in rep.checks["span_drop"]["missing"]


def test_misordered_spans_are_flagged():
    spans = _archive()
    spans[2]["end_wall_ns"] = spans[2]["start_wall_ns"] - 1
    spans[2]["attributes"]["mark.end_mono_ns"] = spans[2]["attributes"]["mark.start_mono_ns"] - 1
    rep = check_ordering(spans)
    assert not rep["ok"] and any("ends before it starts" in p for p in rep["problems"])
    spans = _archive()
    spans[3]["start_wall_ns"] = 1  # child starts long before its parent
    assert not check_ordering(spans)["ok"]


def test_calibration_tolerance():
    ok = check_calibration(_archive(), 250, 5, 5)
    assert ok["ok"] and 0 <= ok["offset_ms"] <= 5
    slow = _archive()
    for s in slow:
        if s["name"] == "mock.calibration":
            s["attributes"]["mark.end_mono_ns"] = s["attributes"]["mark.start_mono_ns"] + 270_000_000
    assert not check_calibration(slow, 250, 5, 5)["ok"]
    assert not check_calibration(_archive()[:6], 250, 5, 5)["ok"]  # too few samples


def test_propagation_requires_every_service_on_the_trace():
    assert check_propagation(_archive(), T, ["harness", "agent:scripted", "mock-world"])["ok"]
    missing = check_propagation(_archive(), T, ["harness", "mcp-tools"])
    assert not missing["ok"] and missing["missing"] == ["mcp-tools"]


def test_the_calibration_rule_is_one_sided_only_on_a_monotonic_raw_clock(monkeypatch):
    """A 250 ms sleep measured 249.72 ms on this laptop and the one-sided check failed the whole run (clean suite,
    2026-09-12). On CLOCK_MONOTONIC_RAW an undershoot cannot happen and stays a failure; on a fallback clock the
    sleep timer and the measuring clock are different sources, so a small undershoot is the platform. Both cases
    record which rule was applied."""
    from mark_platform import integrity as I

    spans = [{"name": "mock.calibration", "attributes": {"mark.start_mono_ns": 0, "mark.end_mono_ns": int(249.72 * 1e6)}} for _ in range(3)]
    import mark_timing

    monkeypatch.setattr(mark_timing, "is_raw", lambda: False)
    lax = I.check_calibration(spans, 250.0, 25.0, 3)
    assert lax["ok"] and lax["lower_bound_ms"] == -25.0 and "different sources" in lax["rule"] and lax["clock_is_monotonic_raw"] is False
    monkeypatch.setattr(mark_timing, "is_raw", lambda: True)
    strict = I.check_calibration(spans, 250.0, 25.0, 3)
    assert not strict["ok"] and strict["lower_bound_ms"] == 0.0 and "can only overshoot" in strict["rule"]
    # an overshoot beyond the tolerance fails under either rule
    slow = [{"name": "mock.calibration", "attributes": {"mark.start_mono_ns": 0, "mark.end_mono_ns": int(300.0 * 1e6)}} for _ in range(3)]
    monkeypatch.setattr(mark_timing, "is_raw", lambda: False)
    assert not I.check_calibration(slow, 250.0, 25.0, 3)["ok"]


def test_a_decisive_run_requires_the_raw_clock(tmp_path):
    """Founder ruling 2026-09-12: on a fallback clock the sign of a timing offset carries no information and no
    timing claim is grounded in one source, so no verdict can be decisive there whatever else holds. The laptop
    never satisfies it; the pod always does."""
    from mark_probes.gate import Gate, load_gate
    from mark_probes.killswitch import KsCompleteness
    from mark_probes.base import Replication
    from pathlib import Path as P

    g = load_gate(P(__file__).resolve().parents[3] / "gates", "ks.completeness", None)
    gate = Gate(g.gate_id, g.version, g.probe_family, g.thresholds, {**g.preconditions, "min_replications": 1}, g.outcome_labels, g.why, "2026-09-12T00:00:00Z", True, "test", g.gate_hash, g.source)
    reps = [Replication(0, "s0", "measured", "", 0.0, {"control_response": {"primitive": "cancel"}, "attempts": {}}, {})]
    kw = dict(target={"id": "t"}, control={"id": "ref-cancel", "control_class": "in_process"}, workload={"id": "wl.batch-payments", "version": 1, "params": {}},
              reps=reps, gate=gate, calibration_ok=True, telemetry_incomplete=False)
    fallback = KsCompleteness().result(**kw, context={"variants_seen": {"batched", "single_call_per_turn"}, "clock_is_monotonic_raw": False, "clock_source": "perf_counter"})
    assert any("CLOCK_MONOTONIC_RAW" in r and "perf_counter" in r for r in fallback["verdict"]["reasons"]), fallback["verdict"]["reasons"]
    raw = KsCompleteness().result(**kw, context={"variants_seen": {"batched", "single_call_per_turn"}, "clock_is_monotonic_raw": True, "clock_source": "CLOCK_MONOTONIC_RAW"})
    assert not any("CLOCK_MONOTONIC_RAW" in r for r in raw["verdict"]["reasons"]), raw["verdict"]["reasons"]
    # unknown (no clock facts in context) is not asserted either way: it is not a claim that the clock was raw
    unknown = KsCompleteness().result(**kw, context={"variants_seen": {"batched", "single_call_per_turn"}})
    assert not any("CLOCK_MONOTONIC_RAW" in r for r in unknown["verdict"]["reasons"])
