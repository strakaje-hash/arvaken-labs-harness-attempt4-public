"""C1 (attempt 4) through a real bundle: the mapping is pinned at close, every row carries its outcomes, a re-decision reads them
again under the pinned mapping, and a mapping that does not hash to the pin refuses the re-decision."""
import json
from pathlib import Path

import pytest

from mark_platform.redecide import RedecideRefused, redecide_bundle
from mark_platform.runner import calibrate, close_run, open_run, run_cell

REPO = Path(__file__).resolve().parents[3]
ROOT_PUB = (REPO / "packages" / "bundles" / "keys" / "root.pub").read_text().strip() if (REPO / "packages" / "bundles" / "keys" / "root.pub").exists() else None
REVOCATIONS = json.loads((REPO / "packages" / "bundles" / "keys" / "revocations.json").read_text()) if (REPO / "packages" / "bundles" / "keys" / "revocations.json").exists() else None
REDECIDE = dict(gates_dir=REPO / "gates", root_public_hex=ROOT_PUB, revocations=REVOCATIONS, engine_version="test", repo_commit="test")


def _bundle(tmp_path, name="tags"):
    ctx = open_run(tmp_path / name, name, llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.latency", "scripted", "none", "wl.sequence-payments", 1)
        run_cell(ctx, "ks.latency", "scripted", "ref-stop", "wl.sequence-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    return tmp_path / name


def _read(run_dir):
    return json.loads((run_dir / "results.json").read_text(encoding="utf-8")), json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))


def test_a_run_refuses_to_open_without_a_tag_mapping(tmp_path):
    with pytest.raises(FileNotFoundError, match="no tag mapping tag-outcomes"):
        open_run(tmp_path / "r", "r", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[], mappings_dir=tmp_path / "empty")


def test_the_bundle_pins_the_mapping_the_rows_carry_outcomes_and_a_re_decision_reads_them_under_the_pin(tmp_path):
    run_dir = _bundle(tmp_path)
    results, manifest = _read(run_dir)
    pin = manifest["pins"]["tag_mapping"]
    assert pin == results["tag_mapping"] and pin["id"] == "tag-outcomes" and len(pin["hash"]) == 64 and pin["tags_with_a_rule"] == ["halt:in_flight"]
    by_control = {r["control"]["id"]: r["tag_outcomes"] for r in results["results"]}
    assert by_control["ref-stop"]["by_tag"]["halt:in_flight"]["reading"] == "held in 1 of 1" and by_control["none"]["by_tag"]["halt:in_flight"]["reading"] == "absent in the tested configuration in 1 of 1"
    assert all(t["mapping"] == {k: pin[k] for k in ("id", "version", "hash", "signed")} for t in by_control.values())
    out = redecide_bundle(run_dir, **REDECIDE)
    assert out["ok"]
    results2, manifest2 = _read(run_dir)
    rec = manifest2["environment"]["close_redecision"]["tag_outcomes"]
    assert rec["recomputed"] is True and rec["mapping"]["hash"] == pin["hash"] and rec["rows_changed"] == 0   # a clean run reads the same
    assert {r["control"]["id"]: r["tag_outcomes"] for r in results2["results"]} == by_control
    # the read surface: the report prints the row's reading and names the mapping once
    from mark_platform.report import report_run

    md = report_run(run_dir)
    assert pin["signed"] is True and pin["signed_by"] == "ee7ab65d74c67291", pin   # the run loaded the founder's signed v1 under the root
    assert f"Tag mapping: tag-outcomes v{pin['version']} `{pin['hash'][:16]}` signed by ee7ab65d74c67291; tags with a rule: halt:in_flight" in md, md
    # the reference controls record no tag states (never an evaluated control), so their cell prints a dash even though the row carries
    # the outcome; the cell's reading for an evaluated control is tested in test_report_rules
    ref_line = next(l for l in md.splitlines() if l.startswith("| ks.latency") and "| ref-stop" in l)
    assert ref_line.endswith("| - |"), ref_line


def test_a_mapping_that_does_not_hash_to_the_pin_refuses_the_re_decision(tmp_path):
    run_dir = _bundle(tmp_path, "pinned")
    body = json.loads((REPO / "mappings" / "tag-outcomes.draft.json").read_text(encoding="utf-8"))
    body["version"] = 2
    body["why"]["v2"] = "a newer mapping than the one the bundle was sealed under"
    (tmp_path / "newer").mkdir()
    (tmp_path / "newer" / "tag-outcomes.draft.json").write_text(json.dumps(body), encoding="utf-8")
    results_before = (run_dir / "results.json").read_bytes()
    with pytest.raises(RedecideRefused, match="re-decided under the mapping it was sealed with"):
        redecide_bundle(run_dir, **REDECIDE, mappings_dir=tmp_path / "newer")
    assert (run_dir / "results.json").read_bytes() == results_before   # refused before anything was written
