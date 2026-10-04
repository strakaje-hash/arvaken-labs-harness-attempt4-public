"""A7: the report never reads "no spans" as "foreign tracer".

An agent process that emitted nothing reports no instrument check. Before A7 that was a failed check like any other, the
runner's summary counted it under `scenarios_failed`, and the report printed that number as "with a foreign tracer live" --
a claim about a second instrument made from silence. The check now names what it saw, the summary counts the two apart,
and the report prints each under its own name.
"""
from __future__ import annotations

from mark_platform.integrity import NO_INSTRUMENT_CHECK, check_single_instrument


def test_no_check_is_named_no_agent_spans_and_a_foreign_tracer_is_named_a_foreign_tracer():
    silent = check_single_instrument(None)
    assert silent["ok"] is False and silent["kind"] == NO_INSTRUMENT_CHECK == "no_agent_spans" and silent["problems"] == ["no_agent_spans"]
    assert "emitted nothing" in silent["reason"] and "foreign" not in silent["reason"]
    foreign = check_single_instrument({"ok": False, "provider_is_ours": True, "foreign_processors": [], "foreign_active": ["laminar"], "sdks": {"laminar": "active"}})
    assert foreign["ok"] is False and foreign["kind"] == "foreign_instrument" and foreign["problems"] == ["foreign tracer active: laminar"]
    ours = check_single_instrument({"ok": True, "provider_is_ours": True, "foreign_processors": [], "foreign_active": [], "sdks": {"laminar": "present"}})
    assert ours["ok"] is True and ours["kind"] == "ok" and ours["problems"] == []


def test_the_run_summary_counts_the_two_apart_and_the_report_prints_them_by_name():
    from mark_platform.report import render
    from mark_platform.runner import RunContext, _process_instrument_summary

    def row(checks):
        return {"probe": {"id": "p"}, "target": {"id": "t"}, "control": {"id": "c"}, "workload": {"id": "w"},
                "integrity": [{"scenario_id": f"s{i}", "ok": c["ok"], "checks": {"single_instrument": c}} for i, c in enumerate(checks)]}

    ctx = RunContext.__new__(RunContext)
    ctx.results = [row([check_single_instrument(None), check_single_instrument(None),
                        check_single_instrument({"ok": False, "provider_is_ours": True, "foreign_processors": [], "foreign_active": ["laminar"], "sdks": {}}),
                        check_single_instrument({"ok": True, "provider_is_ours": True, "foreign_processors": [], "foreign_active": [], "sdks": {}})])]
    s = _process_instrument_summary(ctx)
    assert s["scenarios_checked"] == 4 and s["scenarios_failed"] == 3 and s["scenarios_foreign_instrument"] == 1 and s["scenarios_no_agent_spans"] == 2 and s["ok"] is False
    assert s["problems"] == {"no_agent_spans": 2, "foreign tracer active: laminar": 1}
    # the attempt 3 shape of the same fact, as an older bundle recorded it, is counted the same way
    old = {"ok": False, "problems": ["no instrument check"]}
    ctx.results = [row([old])]
    s2 = _process_instrument_summary(ctx)
    assert s2["scenarios_no_agent_spans"] == 1 and s2["scenarios_foreign_instrument"] == 0
    # the report: each number under its own name, and "foreign tracer live" never carries the silent ones
    results = {"run_id": "r", "environment": {}, "results": [], "single_instrument": {"process_checks": s, "archive": None}}
    md = render(results)
    line = next(ln for ln in md.splitlines() if "agent processes checked" in ln)
    assert "with a foreign tracer live: 1" in line and "reporting no instrument check (no agent spans): 2" in line, line
