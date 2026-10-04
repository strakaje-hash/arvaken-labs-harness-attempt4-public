"""Rule 4 of the signed clarification evidence_sourcing_audit: every attempt 3 report states the sourcing limitation in the
same words, once, names the fix that lands in the next freeze, and carries its own audit record's findings. The Lab's scripted
agent gets the sentence that says what the label means there (founder, 2026-09-15)."""
import json

from mark_probes.constitution import EVIDENCE_SOURCING, EVIDENCE_SOURCING_LAB_AGENT
from mark_platform.report import render, report_run


def _row(target="langgraph-ref", control="agt-kill-switch"):
    return {"probe": {"id": "ks.latency", "version": 1, "family": "kill-switch", "spec_hash": "h"},
            "target": {"id": target, "version": "1", "category": "agent"}, "control": {"id": control, "version": "1", "control_class": "in_process", "category": "control"},
            "workload": {"id": "wl.x", "version": 11, "hash": "w"}, "replications": {"requested": 1, "measured": 1, "not_run": [], "extra": [], "counted_limit": 20},
            "per_replication": [], "aggregate": {"n": 1, "mean": 1.0, "median": 1.0, "min": 1.0, "max": 1.0},
            "verdict": {"label": "informational", "decisive": False, "outcome_if_decisive": "pass", "reasons": [], "gate": {"signed": False, "unsigned_reason": "draft"}},
            "context": {"variant": "v"}, "integrity": []}


def _results(rows):
    return {"run_id": "r", "benchmark": "attempt3-agent-controls", "started_at": "s", "ended_at": "e", "environment": {}, "calibration": {"ok": True}, "results": rows}


AUDIT = {"clarification": "evidence_sourcing_audit.v2", "clarification_signed_sha256": "a" * 64,
         "run_level_account": {"scheduled_replications": 860, "recorded_replications": 860, "unaccounted_replications": 0, "measured": 600, "measured_extra": 40,
                               "not_run": 220, "not_run_by_reason": {"control_not_applicable: langgraph-interrupt is native to ['langgraph-ref'], not scripted": 220},
                               "note": "the manifest does not carry scheduled-versus-recorded totals at freeze-8"},
         "totals": {"cells": 70, "decisive_before": 19, "decisive_after": 19, "cells_self_report_inconsistent": 0, "cells_selective_suppression": 0,
                    "cells_labeled_agent_side": 67, "flagged_replications": 0}}

CORRECTION = {"written_at": "2026-09-15T23:20:00Z", "corrects_sha256": "b" * 64, "field": "run_level_account.scenario_directories_expected",
              "recorded_value": 640, "corrected_value": 643, "why": "the three calibration scenarios are not in the formula.",
              "what_is_not_affected": "No rule reads this field."}


def test_the_sourcing_statement_appears_once_and_describes_the_self_report_record_as_built():
    md = render(_results([_row()]))
    assert md.count(EVIDENCE_SOURCING) == 1
    # A2 built what this sentence used to promise: it now describes the self_report record, not "the next freeze"
    assert "self_report record in the ledger" in md and "never one mixed object" in md and "the next freeze" not in md and "## Evidence sourcing" in md


def test_the_lab_agent_sentence_appears_only_when_a_scripted_row_is_in_the_bundle():
    assert EVIDENCE_SOURCING_LAB_AGENT not in render(_results([_row()]))
    md = render(_results([_row(), _row(target="scripted", control="none")]))
    assert md.count(EVIDENCE_SOURCING_LAB_AGENT) == 1


def test_the_audit_record_is_rendered_with_the_run_level_account_and_any_correction():
    md = render(_results([_row()]), audit=AUDIT, audit_correction=CORRECTION)
    assert "evidence_sourcing_audit.v2" in md and "decisive 19 before the audit and 19 after" in md
    assert "67 labelled sourcing: agent-side" in md
    assert "860 scheduled, 860 recorded, 0 unaccounted" in md and "220 not run (220 x control_not_applicable" in md
    assert "Audit correction (2026-09-15T23:20:00Z" in md and "recorded 640, corrected to 643" in md


def test_a_bundle_with_no_audit_beside_it_still_states_the_limitation(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "results.json").write_text(json.dumps(_results([_row()])), encoding="utf-8")
    md = report_run(d)
    assert EVIDENCE_SOURCING in md and "Audit (" not in md


def test_report_run_reads_the_audit_and_correction_that_sit_beside_the_bundle(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "results.json").write_text(json.dumps(_results([_row()])), encoding="utf-8")
    (tmp_path / "run.evidence-audit.json").write_text(json.dumps(AUDIT), encoding="utf-8")
    (tmp_path / "run.evidence-audit.correction.json").write_text(json.dumps(CORRECTION), encoding="utf-8")
    md = report_run(d)
    assert "evidence_sourcing_audit.v2" in md and "Audit correction" in md and "860 scheduled" in md
