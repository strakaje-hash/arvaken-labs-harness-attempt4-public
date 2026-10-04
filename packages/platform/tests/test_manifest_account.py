"""A5: the manifest carries scheduled, recorded and not-run-by-reason totals, inside the signed object; a mismatch refuses signing.

The plan's test: manifest totals equal the cell records' sums, and a mismatch refuses signing. The mismatch is made the way a
mismatch would actually arise -- the manifest's own field edited while results.json is untouched -- so the results hash check,
which is what guarded the manifest before A5, is shown NOT to catch it (R10: the old guard against the new defect), and the
account check is.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mark_platform.account import ACCOUNT_SCHEMA, account_differences, check_manifest_account, reason_key, run_level_account


def _rep(status, reason=""):
    return {"index": 0, "scenario_id": "s", "status": status, "reason": reason, "value": 1.0 if status.startswith("measured") else None, "raw": {}, "telemetry": {}}


def _row(reps, requested=None):
    return {"probe": {"id": "p"}, "target": {"id": "t"}, "control": {"id": "c"}, "workload": {"id": "w"},
            "replications": {"requested": requested if requested is not None else len(reps), "counted_limit": 20}, "per_replication": reps}


def test_the_account_is_the_cell_records_sums_with_every_not_run_reason_counted_under_its_own_text():
    long_reason = "model_error: " + "x" * 200
    results = {"results": [_row([_rep("measured"), _rep("measured"), _rep("not_run", "telemetry_incomplete: span_drop"), _rep("measured_extra")], requested=6),
                           _row([_rep("not_run", "telemetry_incomplete:   span_drop"), _rep("not_run", long_reason)], requested=2),
                           _row([], requested=3)]}
    a = run_level_account(results)
    assert a["schema"] == ACCOUNT_SCHEMA and "refuses the signature" in a["rule"]
    assert a["cells"] == 3 and a["scheduled_replications"] == 11 and a["recorded_replications"] == 6 and a["unaccounted_replications"] == 5
    assert a["measured"] == 2 and a["measured_extra"] == 1 and a["not_run"] == 3
    # whitespace collapsed so the two span_drop reasons are one bucket; the long reason truncated at 120 the audit's way
    assert a["not_run_by_reason"] == {"telemetry_incomplete: span_drop": 2, reason_key(long_reason): 1}
    assert len(reason_key(long_reason)) == 120 and reason_key(long_reason).endswith("...")
    assert run_level_account({"results": []}) == {**run_level_account({"results": []}), "cells": 0, "scheduled_replications": 0, "not_run_by_reason": {}}


def test_differences_are_named_field_by_field_and_a_missing_account_is_its_own_refusal():
    results = {"results": [_row([_rep("measured"), _rep("not_run", "why")], requested=3)]}
    a = run_level_account(results)
    assert account_differences(a, a) == [] and account_differences(dict(a), run_level_account(results)) == []
    tampered = {**a, "recorded_replications": 3, "not_run_by_reason": {}}
    diffs = account_differences(tampered, a)
    assert diffs == ["recorded_replications: the manifest says 3, the cell records give 2", 'not_run_by_reason: the manifest says {}, the cell records give {"why": 1}']
    [missing] = account_differences(None, a)
    assert missing.startswith("the manifest carries no run-level account")


@pytest.fixture
def closed_run(tmp_path):
    from mark_platform.runner import calibrate, close_run, open_run, run_cell

    ctx = open_run(tmp_path / "run", "a5", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 2)
        run_cell(ctx, "ks.latency", "scripted", "ref-stop", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return tmp_path / "run"


def test_close_writes_the_account_into_the_unsigned_manifest_and_it_equals_the_recomputation(closed_run):
    manifest = json.loads((closed_run / "manifest.unsigned.json").read_text(encoding="utf-8"))
    results = json.loads((closed_run / "results.json").read_text(encoding="utf-8"))
    acct = manifest["account"]
    assert acct["schema"] == ACCOUNT_SCHEMA and acct["cells"] == 2 and acct["scheduled_replications"] == 3 and acct["recorded_replications"] == 3
    assert acct["unaccounted_replications"] == 0 and acct["measured"] + acct["not_run"] + acct["measured_extra"] == 3
    assert acct == run_level_account(results, run_dir=closed_run) and check_manifest_account(closed_run) == []
    assert "account" not in results, "the account is in the signed object, not derived from results.json"


def test_an_edited_account_passes_the_results_hash_check_and_is_caught_by_the_account_check(closed_run):
    from mark_ledger.canonical import sha256_hex

    mp = closed_run / "manifest.unsigned.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["account"]["recorded_replications"] += 1
    manifest["account"]["not_run_by_reason"] = {}
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    # the guard that existed before A5 still passes: results.json is untouched and its hash still matches the manifest
    assert sha256_hex((closed_run / "results.json").read_bytes()) == manifest["evidence"]["results_sha256"]
    diffs = check_manifest_account(closed_run)
    assert any(d.startswith("recorded_replications: the manifest says 4, the cell records give 3") for d in diffs), diffs
    # and a manifest with the field removed is refused as such, not silently accepted as "nothing to compare"
    del manifest["account"]
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    assert check_manifest_account(closed_run)[0].startswith("the manifest carries no run-level account")


def test_run_sign_refuses_the_edited_account_before_anything_else_is_asked_of_the_bundle(closed_run, tmp_path, monkeypatch, capsys):
    """Through the CLI: the refusal names the account, and comes before the close-steps check, so a tampered manifest is never told
    it merely lacks a re-decision. No key is needed to be refused; none is consumed."""
    import mark_platform.cli as cli

    mp = closed_run / "manifest.unsigned.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["account"]["scheduled_replications"] = 99
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (tmp_path / "rm.key").write_text("00" * 32)
    (tmp_path / "rm.cert.json").write_text("{}")
    rc = cli.main(["run", "sign", "--run-dir", str(closed_run), "--key", str(tmp_path / "rm.key"), "--cert", str(tmp_path / "rm.cert.json"), "--anchor", "none"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and out["ok"] is False and "run-level account differs" in out["reason"]
    assert out["differences"] == ["scheduled_replications: the manifest says 99, the cell records give 3"]
    assert not (closed_run / "manifest.json").exists()


def test_redecide_recomputes_the_account_and_says_whether_it_changed(closed_run):
    from mark_platform.redecide import redecide_bundle

    REPO = Path(__file__).resolve().parents[3]
    keys = REPO / "packages" / "bundles" / "keys"
    rev = json.loads((keys / "revocations.json").read_text()) if (keys / "revocations.json").exists() else None
    before = json.loads((closed_run / "manifest.unsigned.json").read_text(encoding="utf-8"))["account"]
    redecide_bundle(closed_run, gates_dir=REPO / "gates", root_public_hex=(keys / "root.pub").read_text().strip(), revocations=rev, engine_version="test", repo_commit="test")
    manifest = json.loads((closed_run / "manifest.unsigned.json").read_text(encoding="utf-8"))
    results = json.loads((closed_run / "results.json").read_text(encoding="utf-8"))
    assert manifest["account"] == run_level_account(results, run_dir=closed_run) and check_manifest_account(closed_run) == []
    cr = manifest["environment"]["close_redecision"]
    assert cr["account_before"] == before and cr["account_changed"] == (before != manifest["account"])
    # a clean run changes nothing (the plan's phase 1 step 8), and this laptop run is one
    assert cr["account_changed"] is False


# ---------------------------------------------------------------- A8: the directory count includes calibration

def test_the_expected_directory_count_includes_the_calibration_scenarios_and_matches_what_is_on_disk(closed_run):
    """Attempt 3's audit expected measured + extra + not_run-with-a-scenario and read the three calibration directories as a
    surplus. Here one calibration scenario ran beside three replications: four directories, four expected."""
    manifest = json.loads((closed_run / "manifest.unsigned.json").read_text(encoding="utf-8"))
    results = json.loads((closed_run / "results.json").read_text(encoding="utf-8"))
    acct = manifest["account"]
    assert acct["calibration_scenarios"] == len(results["calibration"]["replications"]) == 1
    assert acct["scenario_directories_expected"] == 3 + 1 and acct["scenario_directories"] == 4 and "calibration" in acct["scenario_directories_rule"]
    # the audit's arithmetic, on the same bundle, would have expected three: the gap A8 names
    assert acct["measured"] + acct["measured_extra"] + sum(1 for r in results["results"] for p in r["per_replication"] if p["status"] == "not_run" and p["scenario_id"]) == 3
    # the count is recomputed at signing, so a scenario directory removed from the bundle refuses the signature
    assert check_manifest_account(closed_run) == []
    victim = sorted(p for p in (closed_run / "scenarios").iterdir() if p.is_dir())[0]
    import shutil

    shutil.rmtree(victim)
    diffs = check_manifest_account(closed_run)
    assert diffs == ["scenario_directories: the manifest says 4, the cell records give 3"]
