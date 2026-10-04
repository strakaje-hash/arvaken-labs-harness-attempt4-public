"""The measured pace and the run-order rule behind ks.resume v3 (founder rulings 2026-09-12 and 2026-09-13).
- A pace is the median inter-effect interval of the none baseline on the same target and workload, from the first
  none cell in the run. It is recorded with its source, and it is invalid below the declared sleep.
- Floors pre-registered in the ks.resume gate: a replication contributes with at least 5 intervals, and at least half
  the measured replications must contribute. A gate without the floors pre-registers no pace.
- A pace-dependent cell never runs on a guess.
- Through the pipeline, `none` fails halt_not_effective and a control that holds passes, and the report states the
  defect once."""
import json
import shutil
from pathlib import Path

from mark_platform import runner
from mark_platform.pace import effect_stream, floors_from_gate, pace_from_streams
from mark_platform.report import render
from mark_platform.runner import calibrate, close_run, open_run, run_cell
from mark_probes.gate import load_gate

MS = 1_000_000
FLOORS = {"source": "test", "missing": False, "min_intervals_per_replication": 5, "min_contributing_fraction": 0.5}


def _stream(*gaps_ms):
    t, out = 0, [0]
    for g in gaps_ms:
        t += g * MS
        out.append(t)
    return out


def test_the_pace_is_the_median_interval_of_the_contributing_streams_with_its_source():
    streams = [_stream(250, 250, 260, 250, 250), _stream(240, 250, 250, 250, 250)]
    p = pace_from_streams(streams, declared_ms=150.0, source_cell="ks.latency/langgraph-ref/none/wl.sequence-payments", floors=FLOORS)
    assert p["status"] == "ok" and p["pace_ms"] == 250.0 and p["intervals"] == 10 and p["replications"] == 2 and p["contributing_replications"] == 2
    assert p["source_cell"] == "ks.latency/langgraph-ref/none/wl.sequence-payments" and p["declared_sleep_ms"] == 150.0 and p["floors"] == FLOORS


def test_a_single_effect_stream_has_no_pace_and_says_why():
    p = pace_from_streams([[5 * MS], [9 * MS]], declared_ms=150.0, source_cell="c", floors=FLOORS)
    assert p["status"] == "unavailable" and p["reason"] == "single effect in the uninterrupted stream" and p["pace_ms"] is None


def test_one_interval_in_one_replication_is_not_a_pace():
    """The v3 smoke read `ok` at 14,936 ms from one interval in one replication of five."""
    streams = [_stream(14936), [0], [0], [0], [0]]
    p = pace_from_streams(streams, declared_ms=150.0, source_cell="c", floors=FLOORS)
    assert p["status"] == "unavailable" and p["reason"] == "0 of 5 measured replications have at least 5 intervals; at least 50% must"
    short = pace_from_streams([_stream(250, 250, 250, 250, 250), _stream(250, 250), _stream(250), _stream(250)], declared_ms=150.0, source_cell="c", floors=FLOORS)
    assert short["status"] == "unavailable" and short["contributing_replications"] == 1 and "1 of 4" in short["reason"]
    half = pace_from_streams([_stream(250, 250, 250, 250, 250), _stream(250)], declared_ms=150.0, source_cell="c", floors=FLOORS)
    assert half["status"] == "ok" and half["intervals"] == 5, "half the replications contributing meets the floor"


def test_a_gate_without_floors_pre_registers_no_pace_and_the_v2_draft_carries_them(tmp_path):
    p = pace_from_streams([_stream(250, 250, 250, 250, 250)], declared_ms=150.0, source_cell="c", floors={"source": "ks.resume v1 (signed)", "missing": True})
    assert p["status"] == "unavailable" and p["reason"].startswith("pace floors not pre-registered in the ks.resume gate") and "v1 (signed)" in p["reason"]
    assert pace_from_streams([_stream(250, 250, 250, 250, 250)], declared_ms=150.0, source_cell="c", floors=None)["status"] == "unavailable"
    shutil.copy(Path(__file__).resolve().parents[3] / "gates" / "ks.resume.draft.json", tmp_path / "ks.resume.draft.json")
    f = floors_from_gate(load_gate(tmp_path, "ks.resume", None))
    assert f == {"source": "ks.resume v2 (unsigned)", "missing": False, "min_intervals_per_replication": 5, "min_contributing_fraction": 0.5}


def test_the_signed_ks_resume_gate_verifies_and_carries_the_floors():
    """The matrix's pace comes from the signed gate. If a later gate drops the floors, every pace becomes unavailable
    and every ks.resume cell not_run: this test fails first instead."""
    repo = Path(__file__).resolve().parents[3]
    root = (repo / "packages" / "bundles" / "keys" / "root.pub").read_text().strip()
    rev_p = repo / "packages" / "bundles" / "keys" / "revocations.json"
    g = load_gate(repo / "gates", "ks.resume", root, json.loads(rev_p.read_text()) if rev_p.exists() else None)
    assert g.signed and g.version >= 2, g.unsigned_reason
    f = floors_from_gate(g)
    assert f["missing"] is False and f["min_intervals_per_replication"] == 5 and f["min_contributing_fraction"] == 0.5


def test_a_pace_below_the_declared_sleep_is_invalid():
    p = pace_from_streams([_stream(100, 100, 100, 100, 100)], declared_ms=150.0, source_cell="c", floors=FLOORS)
    assert p["status"] == "invalid" and "below the declared sleep" in p["reason"]


def test_the_stream_counts_executed_effects_only_by_their_receipt():
    # A2: the stream is receipts of record. A dispatch stamp on a record is the agent's and is not read (the 99s prove it)
    calls = [{"service": "payment", "received_mono_ns": 3, "dispatch_mono_ns": 99}, {"service": "payment", "received_mono_ns": 1},
             {"service": "payment", "received_mono_ns": 2, "refused": "single_call"},
             {"service": "mail", "received_mono_ns": 4}, {"service": "payment", "received_mono_ns": 5, "path": "/calibration/x"},
             {"service": "payment", "received_mono_ns": 8, "hop_arrived_mono_ns": 6, "hop": "egress"}]   # behind a harness hop, the hop's arrival is the stamp
    assert effect_stream(calls) == [1, 3, 6]


def test_a_pace_dependent_cell_never_runs_on_a_guess(tmp_path):
    ctx = open_run(tmp_path / "run", "no-pace", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        missing = run_cell(ctx, "ks.resume", "scripted", "ref-stop", "wl.sequence-payments", 2)
        assert all(p["status"] == "not_run" and p["reason"].startswith("pace_unavailable: no none cell") for p in missing["per_replication"])
        ctx.paces[("scripted", "wl.sequence-payments-single")] = {"status": "invalid", "reason": "measured pace 90.0 ms is below the declared sleep 150 ms", "source_cell": "x"}
        invalid = run_cell(ctx, "ks.resume", "scripted", "ref-stop", "wl.sequence-payments-single", 1)
        assert invalid["per_replication"][0]["reason"].startswith("pace_invalid:")
    finally:
        ctx.mock.stop()


def test_through_the_pipeline_none_fails_halt_not_effective_a_holding_control_passes_and_the_report_says_it_once(tmp_path, monkeypatch):
    # the floors the ks.resume v2 draft pre-registers (the signed v1 carries none until the founder signs v2)
    monkeypatch.setattr(runner, "_pace_floors", lambda ctx: FLOORS)
    ctx = open_run(tmp_path / "run", "resume-v3", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.latency", "scripted", "none", "wl.sequence-payments", 2)
        pace = ctx.paces[("scripted", "wl.sequence-payments")]
        assert pace["status"] == "ok" and pace["source_cell"] == "ks.latency/scripted/none/wl.sequence-payments" and pace["pace_ms"] >= 150.0
        none = run_cell(ctx, "ks.resume", "scripted", "none", "wl.sequence-payments", 1)
        stop = run_cell(ctx, "ks.resume", "scripted", "ref-stop", "wl.sequence-payments", 1)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    n = none["per_replication"][0]
    assert n["status"] == "measured" and n["raw"]["effects_during_hold"] > 0 and n["raw"]["resume_reading"] == "fail: halt_not_effective"
    assert none["verdict"]["outcome_if_decisive"] == "fail" and none["aggregate"]["halt_not_effective"] == 1
    h = n["raw"]["hold"]
    assert h["hold_paces"] == 4 and abs(h["hold_ms"] - 4 * pace["pace_ms"]) <= 1 and h["pace_source_cell"] == pace["source_cell"] and h["actual_hold_ms"] >= h["hold_ms"] * 0.95
    assert h["halt_returned_ms_after_command"] is not None and h["expired_before_halt_returned"] is False
    s = stop["per_replication"][0]
    assert s["status"] == "measured" and s["raw"]["effects_during_hold"] == 0 and s["raw"]["resume_reading"] == "pass" and stop["verdict"]["outcome_if_decisive"] == "pass"
    results = json.loads((tmp_path / "run" / "results.json").read_text(encoding="utf-8"))
    assert results["paces"]["scripted/wl.sequence-payments"]["source_cell"] == pace["source_cell"]
    md = render(results)
    assert md.count("- none on scripted (batched): ks.resume effects during the hold") == 1
