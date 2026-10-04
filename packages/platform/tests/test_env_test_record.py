"""Fix A7 (attempt 3 fixes v1.1, 2026-09-14): every env-test attempt is inside the run record. On attempt 2b the calibration
rule's attempts were logs under $RUNS/env-test-*, outside the run directory, and none reached the exported bundle."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mark_platform.env_test_record import collect, record_env_test
from mark_platform.report import render
from mark_platform.runner import close_run, open_run

REPO = Path(__file__).resolve().parents[3]
ENV_TEST_SH = REPO / "packages" / "platform" / "pod" / "env-test.sh"
BASH = shutil.which("bash")
CAL_ONLY = "FAILED tests/env/test_pod_env.py::test_calibration_within_5ms - assert 26.4 <= 5.0\n1 failed, 11 passed in 40.1s\n"


def _prefix(tmp_path, attempts):
    """attempts: (kind, exit code, log text) in order, written as env-test.sh writes them."""
    runs = tmp_path / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    prefix = runs / "env-test-20260914T120000Z"
    names = {"full": ".log", "rerun": ".rerun.log"}
    lines = []
    for kind, code, text in attempts:
        log = prefix.name + names.get(kind, f".calibration-{kind[-1]}.log")
        (runs / log).write_text(text, encoding="utf-8")
        lines.append(f"{kind}\t{code}\t{log}")
    (runs / (prefix.name + ".attempts")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return prefix


RULE_PASSED = [("full", 1, CAL_ONLY), ("calibration-alone-1", 0, "1 passed\n"), ("calibration-alone-2", 0, "1 passed\n"),
               ("calibration-alone-3", 0, "1 passed\n"), ("rerun", 0, "12 passed in 39.8s\n")]


def test_the_attempts_are_read_in_order_with_the_rule_and_the_outcome(tmp_path):
    rec = collect(_prefix(tmp_path, RULE_PASSED))
    assert [a["kind"] for a in rec["attempts"]] == ["full", "calibration-alone-1", "calibration-alone-2", "calibration-alone-3", "rerun"]
    assert [a["exit_code"] for a in rec["attempts"]] == [1, 0, 0, 0, 0]
    assert rec["attempts"][0]["summary"] == "1 failed, 11 passed in 40.1s" and rec["calibration_rule_applied"] is True
    assert rec["outcome"] == "passed" and rec["final_attempt"] == "rerun"
    noisy = collect(_prefix(tmp_path / "noisy", [("full", 1, CAL_ONLY), ("calibration-alone-1", 0, "1 passed\n"), ("calibration-alone-2", 1, "1 failed\n"),
                                                 ("calibration-alone-3", 0, "1 passed\n")]))
    assert noisy["outcome"] == "failed" and noisy["final_attempt"] == "calibration-alone-3"
    clean = collect(_prefix(tmp_path / "clean", [("full", 0, "12 passed\n")]))
    assert clean["outcome"] == "passed" and clean["calibration_rule_applied"] is False


def test_a_listing_that_is_not_the_rules_attempts_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no env-test attempts listing"):
        collect(tmp_path / "absent")
    with pytest.raises(ValueError, match="first attempt must be the full env-test"):
        collect(_prefix(tmp_path / "a", [("rerun", 0, "12 passed\n")]))
    p = _prefix(tmp_path / "b", [("full", 0, "12 passed\n")])
    listing = p.with_name(p.name + ".attempts")
    for bad, match in (("retry\t0\tx.log", "unknown attempt kind"), ("full\tok\tx.log", "not a number"), ("full\t0\t../x.log", "beside the listing"),
                       ("full\t0\tmissing.log", "is missing"), ("full 0 x.log", "not kind<TAB>exit<TAB>log")):
        listing.write_text(bad + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            collect(p)


def test_a_run_whose_env_test_needed_the_rule_carries_every_attempt(tmp_path):
    prefix = _prefix(tmp_path, RULE_PASSED)
    run_dir = tmp_path / "run"
    ctx = open_run(run_dir, "env-test-record", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        record_env_test(ctx, prefix)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    et = results["env_test"]
    assert len(et["attempts"]) == 5 and et["copied_to"] == "env-test" and manifest["environment"]["env_test"] == et
    # every log and the listing are inside the run directory, byte-identical, so the export carries them
    for a in et["attempts"]:
        assert hashlib.sha256((run_dir / "env-test" / a["log"]).read_bytes()).hexdigest() == a["log_sha256"]
    assert (run_dir / "env-test" / "env-test-20260914T120000Z.attempts").exists()
    md = render(results)
    assert "Env-test: PASSED on the rerun attempt after the calibration rule (5 attempt(s): full exit 1; calibration-alone-1 exit 0;" in md


def test_a_run_without_an_env_test_records_the_absence(tmp_path):
    ctx = open_run(tmp_path / "run", "no-env-test", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        rec = record_env_test(ctx, None)
    finally:
        ctx.mock.stop()
    assert rec["recorded"] is False and "bench run --env-test" in rec["reason"]
    assert "Env-test: not recorded in this run" in render({"run_id": "r", "environment": {}, "results": [], "env_test": rec})
    assert "Env-test" not in render({"run_id": "r", "environment": {}, "results": []}), "a bundle from before the fix prints nothing"


def test_a_bench_run_refuses_a_failed_env_test_before_it_opens(tmp_path):
    from mark_platform.cli import main

    prefix = _prefix(tmp_path, [("full", 1, CAL_ONLY), ("calibration-alone-1", 1, "1 failed\n"), ("calibration-alone-2", 0, "1 passed\n"), ("calibration-alone-3", 0, "1 passed\n")])
    run_dir = tmp_path / "refused"
    with pytest.raises(SystemExit, match=r"bench run refused \(fix A7\).*failed on its calibration-alone-3 attempt"):
        main(["--tools", "inproc", "bench", "run", str(REPO / "benchmarks" / "oss-agent-controls-v1.yaml"), "--run-dir", str(run_dir), "--targets", "scripted",
              "--replications", "1", "--env-test", str(prefix)])
    assert not run_dir.exists()


def _fake_uv(tmp_path, plan):
    """A stand-in `uv run pytest ...`: call n answers with plan[n] (full env-test or calibration alone)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "plan").write_text("\n".join(plan) + "\n", encoding="utf-8")
    script = (
        "#!/usr/bin/env bash\n"
        f"state='{(tmp_path / 'n').as_posix()}'; plan='{(tmp_path / 'plan').as_posix()}'\n"
        'n=$(cat "$state" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$state"\n'
        'case "$(sed -n "${n}p" "$plan")" in\n'
        "  cal-only) echo 'FAILED tests/env/test_pod_env.py::test_calibration_within_5ms - assert 26.4 <= 5.0'; echo '1 failed, 11 passed'; exit 1 ;;\n"
        "  pass) echo '12 passed'; exit 0 ;;\n"
        "  pass-one) echo '1 passed'; exit 0 ;;\n"
        "  fail-one) echo '1 failed'; exit 1 ;;\n"
        "esac\n")
    (bindir / "uv").write_bytes(script.encode())
    (bindir / "uv").chmod(0o755)
    return bindir


def _run_env_test(tmp_path, plan):
    bindir = _fake_uv(tmp_path, plan)
    runs = tmp_path / "runs"
    runs.mkdir()
    env = {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""), "RUNS": runs.as_posix()}
    r = subprocess.run([BASH, str(ENV_TEST_SH)], capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120)
    latest = (runs / "env-test-latest").read_text(encoding="utf-8").strip()
    return r, runs / Path(latest).name


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_env_test_sh_lists_every_attempt_of_the_calibration_rule(tmp_path):
    r, prefix = _run_env_test(tmp_path, ["cal-only", "pass-one", "pass-one", "pass-one", "pass"])
    assert r.returncode == 0, r
    rec = collect(prefix)
    assert [(a["kind"], a["exit_code"]) for a in rec["attempts"]] == [("full", 1), ("calibration-alone-1", 0), ("calibration-alone-2", 0), ("calibration-alone-3", 0), ("rerun", 0)]
    assert rec["outcome"] == "passed" and rec["attempts"][-1]["summary"] == "12 passed"


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_env_test_sh_a_repeat_is_a_noisy_host_and_the_listing_says_so(tmp_path):
    r, prefix = _run_env_test(tmp_path, ["cal-only", "pass-one", "fail-one", "pass-one"])
    assert r.returncode != 0 and "replace the pod" in r.stdout, r
    rec = collect(prefix)
    assert [a["kind"] for a in rec["attempts"]] == ["full", "calibration-alone-1", "calibration-alone-2", "calibration-alone-3"]
    assert rec["outcome"] == "failed" and rec["attempts"][2]["exit_code"] == 1
