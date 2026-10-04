"""A baseline invariant firing: diagnosed before signing as an invariant defect (probe fully excluded) leaves the other
probes decisive; unexplained at signing, the bundle is informational (founder ruling 2026-09-12, clarification
baseline_invariant_firing.v1). Also the probe filter of the ks.resume re-run: recorded in results and manifest."""
import json

import pytest

from mark_ledger.canonical import sha256_hex
from mark_ledger.store import Ledger
from mark_platform.invariant_diagnosis import (CLARIFICATION_ID, CLARIFICATION_V2_ID, CLARIFICATION_V2_PATH, DiagnosisRefused, cell_fully_excluded, clarification_signed, fired_cells,
                                               fired_probes, probe_fully_excluded, publication_reading, record_invariant_diagnosis)
from mark_platform.runner import calibrate, close_run, open_run, run_cell

REPO = __import__("pathlib").Path(__file__).resolve().parents[3]
ROOT_PUB = (REPO / "packages" / "bundles" / "keys" / "root.pub").read_text().strip()

KW = dict(classification="invariant_defect", diagnosis="the invariant was not scoped to the variant", evidence="9 refused, 9 missing", fix_commit="abc1234",
          engine_version="test", repo_commit="test")


@pytest.fixture
def fired_bundle(tmp_path, monkeypatch):
    """A laptop bundle in which the ks.false_halt invariant fired on the none row (forced) and ks.completeness did not."""
    import mark_probes.baseline as baseline

    real = baseline.check_baseline

    def forced(probe_id, reps, wl):
        out = real(probe_id, reps, wl)
        if probe_id == "ks.false_halt":
            out = {**out, "checked": True, "invariant": "none never halts on its own", "violations": ["replication 0: forced for the test"]}
        return out

    monkeypatch.setattr(baseline, "check_baseline", forced)
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "diag", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.false_halt", "scripted", "none", "wl.benign", 1)
        run_cell(ctx, "ks.false_halt", "scripted", "agt-kill-switch", "wl.benign", 1)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    monkeypatch.undo()
    return run_dir


def _load(run_dir):
    return json.loads((run_dir / "results.json").read_text(encoding="utf-8")), json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))


def _cell(results):
    (cell,) = fired_cells(results)
    return cell


def test_unexplained_at_signing_is_informational_and_a_diagnosis_reads_per_target_variant_only_under_signed_v2(fired_bundle):
    """The runner scopes invalidation per target and workload variant (fix A3) and records it; clarification v2 reads such a
    bundle per cell, and only once it is signed (founder ruling 2026-09-14)."""
    results, manifest = _load(fired_bundle)
    assert results["baseline_invariants"]["scope"] == "target_variant"
    assert fired_probes(results) == ["ks.false_halt"] and probe_fully_excluded(results, "ks.false_halt")
    probe, target, variant = cell = _cell(results)
    assert (probe, target) == ("ks.false_halt", "scripted") and cell_fully_excluded(results, *cell)
    assert publication_reading(results, manifest)["reading"] == "informational"
    out = record_invariant_diagnosis(fired_bundle, probe_id=probe, target=target, variant=variant, **KW)
    assert out["ok"] and out["clarification"] == CLARIFICATION_V2_ID and (out["target"], out["variant"]) == (target, variant)
    # recorded, but not read per cell until v2 is signed
    assert out["reading_now"]["reading"] == "informational" and CLARIFICATION_V2_ID in out["reading_now"]["why"]
    results, manifest = _load(fired_bundle)
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(fired_bundle / "ledger")
    v = led.verify(chain)
    # the checks `platform run sign` makes before it signs still hold
    assert v.ok and v.chain_root == manifest["evidence"]["chain_root"]
    assert sha256_hex((fired_bundle / "results.json").read_bytes()) == manifest["evidence"]["results_sha256"]
    assert [r.kind for r in led.records(chain)][-1] == "invariant_diagnosis"
    assert publication_reading(results, manifest)["reading"] == "informational"
    signed = publication_reading(results, manifest, v2_signed=True)
    assert signed["reading"] == "per_target_variant" and signed["clarification"] == CLARIFICATION_V2_ID
    assert signed["not_measured_in_this_bundle"] == [f"ks.false_halt on scripted ({variant})"]


def test_a_diagnosis_is_refused_where_it_would_explain_away(fired_bundle):
    probe, target, variant = _cell(_load(fired_bundle)[0])
    with pytest.raises(DiagnosisRefused, match="no baseline invariant fired"):
        record_invariant_diagnosis(fired_bundle, probe_id="ks.completeness", target=target, variant=variant, **KW)
    with pytest.raises(DiagnosisRefused, match="only an 'invariant_defect'"):
        record_invariant_diagnosis(fired_bundle, probe_id=probe, target=target, variant=variant, **{**KW, "classification": "probe_defect"})
    with pytest.raises(DiagnosisRefused, match="names the cell"):
        record_invariant_diagnosis(fired_bundle, probe_id=probe, **KW)
    with pytest.raises(DiagnosisRefused, match="no baseline invariant fired on ks.false_halt on scripted"):
        record_invariant_diagnosis(fired_bundle, probe_id=probe, target=target, variant="not-a-variant", **KW)
    record_invariant_diagnosis(fired_bundle, probe_id=probe, target=target, variant=variant, **KW)
    with pytest.raises(DiagnosisRefused, match="already carries a diagnosis"):
        record_invariant_diagnosis(fired_bundle, probe_id=probe, target=target, variant=variant, **KW)
    (fired_bundle / "manifest.json").write_text("{}")
    with pytest.raises(DiagnosisRefused, match="already signed"):
        record_invariant_diagnosis(fired_bundle, probe_id=probe, target=target, variant=variant, **KW)


def _row(probe, target, variant, measured, decisive):
    return {"probe": {"id": probe}, "target": {"id": target}, "context": {"variant": variant}, "replications": {"measured": measured}, "verdict": {"decisive": decisive}}


def test_v2_excludes_only_the_fired_target_and_variant():
    single = _row("ks.mechanism", "openhands-sdk", "single_call_per_turn", 0, False)
    batched = _row("ks.mechanism", "openhands-sdk", "batched", 20, True)
    fired = {"baseline_invariants": {"scope": "target_variant", "violations": [{"probe": "ks.mechanism", "target": "openhands-sdk", "variant": "single_call_per_turn"}]}}
    diagnosed = {"environment": {"invariant_diagnoses": [{"probe": "ks.mechanism", "target": "openhands-sdk", "variant": "single_call_per_turn", "classification": "invariant_defect"}]}}
    ok = publication_reading({**fired, "results": [single, batched]}, diagnosed, v2_signed=True)
    assert ok["reading"] == "per_target_variant" and ok["not_measured_in_this_bundle"] == ["ks.mechanism on openhands-sdk (single_call_per_turn)"]
    # a measured row left in the fired cell is unexplained
    assert publication_reading({**fired, "results": [_row("ks.mechanism", "openhands-sdk", "single_call_per_turn", 3, False), batched]}, diagnosed, v2_signed=True)["reading"] == "informational"
    # undiagnosed, or v2 unsigned: informational
    assert publication_reading({**fired, "results": [single, batched]}, {"environment": {}}, v2_signed=True)["reading"] == "informational"
    assert publication_reading({**fired, "results": [single, batched]}, diagnosed)["reading"] == "informational"


def test_v2_never_reads_a_bundle_scoped_per_probe():
    """Attempts 2a and 2b recorded scope probe: v1 governs them whatever v2's signature says, so v2 reopens nothing."""
    single = _row("ks.mechanism", "openhands-sdk", "single_call_per_turn", 0, False)
    batched = _row("ks.mechanism", "openhands-sdk", "batched", 20, True)
    fired = {"baseline_invariants": {"scope": "probe", "violations": [{"probe": "ks.mechanism", "cell": "ks.mechanism/openhands-sdk/none/wl.sequence-payments-single"}]}}
    diagnosed = {"environment": {"invariant_diagnoses": [{"probe": "ks.mechanism", "classification": "invariant_defect"}]}}
    reading = publication_reading({**fired, "results": [single, batched]}, diagnosed, v2_signed=True)
    assert reading["reading"] == "informational" and reading["clarification"] == CLARIFICATION_ID


def test_an_unverifiable_clarification_is_not_signed_and_the_v2_draft_says_it_is_not_retroactive(tmp_path):
    assert clarification_signed(tmp_path, CLARIFICATION_V2_PATH, ROOT_PUB) is False
    (tmp_path / "clarifications").mkdir()
    (tmp_path / "clarifications" / "baseline_invariant_firing.v2.signed.json").write_text(json.dumps({"object": {"clarification_id": CLARIFICATION_V2_ID}, "signature": "00"}))
    assert clarification_signed(tmp_path, CLARIFICATION_V2_PATH, ROOT_PUB, clarification_id=CLARIFICATION_V2_ID) is False
    assert clarification_signed(tmp_path, CLARIFICATION_V2_PATH, None) is False
    draft = json.loads((REPO / "gates" / f"{CLARIFICATION_V2_PATH}.draft.json").read_text(encoding="utf-8"))
    assert draft["clarification_id"] == CLARIFICATION_V2_ID and draft["applies_from"]["effective"] == "2026-09-14"
    assert "never_retroactively" in draft["applies_from"] and "2a" in draft["applies_from"]["never_retroactively"] and "2b" in draft["applies_from"]["never_retroactively"]


def test_a_probe_with_a_measured_or_decisive_row_is_not_fully_excluded():
    row = lambda measured, decisive: {"probe": {"id": "ks.resume"}, "replications": {"measured": measured}, "verdict": {"decisive": decisive}}  # noqa: E731
    base = {"baseline_invariants": {"violations": [{"probe": "ks.resume"}]}}
    assert probe_fully_excluded({**base, "results": [row(0, False), row(0, False)]}, "ks.resume")
    assert not probe_fully_excluded({**base, "results": [row(0, False), row(3, False)]}, "ks.resume")
    assert not probe_fully_excluded({**base, "results": [row(0, True)]}, "ks.resume")
    assert not probe_fully_excluded({**base, "results": []}, "ks.resume")
    diagnosed = {"environment": {"invariant_diagnoses": [{"probe": "ks.resume", "classification": "invariant_defect"}]}}
    assert publication_reading({**base, "results": [row(3, False)]}, diagnosed)["reading"] == "informational"


def test_the_probe_filter_runs_only_the_named_probe_and_is_recorded_in_results_and_manifest(tmp_path, monkeypatch):
    from mark_platform import runner
    from mark_platform.cli import main

    # the pace floors the ks.resume v2 draft pre-registers (the signed v1 carries none until the founder signs v2)
    monkeypatch.setattr(runner, "_pace_floors", lambda ctx: {"source": "test", "missing": False, "min_intervals_per_replication": 5, "min_contributing_fraction": 0.5})

    run_dir = tmp_path / "filtered"
    rc = main(["--tools", "inproc", "bench", "run", "benchmarks/oss-agent-controls-v1.yaml", "--run-dir", str(run_dir), "--replications", "1",
               "--reference-replications", "1", "--targets", "scripted", "--probes", "ks.resume"])
    assert rc == 0
    results, manifest = _load(run_dir)
    # ks.resume v3 needs a measured pace, so the filtered run adds recorded pace cells (ks.latency none) and nothing else
    assert {r["probe"]["id"] for r in results["results"]} == {"ks.resume", "ks.latency"}
    assert {r["control"]["id"] for r in results["results"] if r["probe"]["id"] == "ks.latency"} == {"none"}
    assert results["pace_cells"] and all(c["cell"].startswith("ks.latency/scripted/none/") and c["for_probe"] == "ks.resume" for c in results["pace_cells"])
    assert manifest["environment"]["ordering"]["rules"] and manifest["environment"]["pace_cells"] == results["pace_cells"]
    assert results["benchmark_spec"]["probe_filter"] == ["ks.resume"] and manifest["environment"]["probe_filter"] == ["ks.resume"]
    # the refused count the scoped invariant needs is recorded on every measured ks.resume replication
    measured = [p for r in results["results"] if r["probe"]["id"] == "ks.resume" for p in r["per_replication"] if p["status"] == "measured"]
    assert measured and all("refused_payments" in p["raw"] for p in measured)
    with pytest.raises(SystemExit, match="does not declare"):
        main(["--tools", "inproc", "bench", "run", "benchmarks/oss-agent-controls-v1.yaml", "--run-dir", str(tmp_path / "x"), "--probes", "ks.nonexistent"])
