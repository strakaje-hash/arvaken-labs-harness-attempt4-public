"""`platform` CLI (Tasks 6.2/6.3 shape; this pass implements what the first session needs):

  platform env check                              what this host can run (python, GPU, sandbox, vLLM, extras)
  platform calibrate --run-dir D                  the known-latency scenario; writes the ledger record
  platform probe run ks.latency --target scripted --control none --workload wl.sequence-payments -n 3 --run-dir D
  platform bench run <benchmark.yaml> --run-dir D  the matrix (agents x controls x probes x replications)
  platform run close --run-dir D [--sign-key K --cert C]   results.json + signed manifest + ledger verify
  platform run export --run-dir D --dest /workspace/results   copy the bundle after verifying it
  platform ledger verify D/ledger [chain]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .runner import REPO_ROOT, calibrate, close_run, environment_fingerprint, open_run, run_cell


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sandbox_cmd(a: argparse.Namespace) -> list[str]:
    if a.sandbox:
        return a.sandbox.split()
    return []


def _open(a: argparse.Namespace):
    run_dir = Path(a.run_dir)
    run_id = a.run_id or run_dir.name or _run_id()
    return open_run(run_dir, run_id, llm_url=a.llm_url, llm_model=a.llm_model, tools_mode=a.tools, sandbox_cmd=_sandbox_cmd(a), registry_path=a.registry)


def cmd_env_check(a: argparse.Namespace) -> int:
    env = environment_fingerprint()
    extras = {}
    for mod in ("langgraph", "mcp", "langchain_mcp_adapters", "hypervisor", "openhands.sdk", "vllm"):
        try:
            __import__(mod)
            extras[mod] = "importable"
        except Exception as e:  # noqa: BLE001
            extras[mod] = f"not importable: {type(e).__name__}"
    llm = None
    try:
        import httpx

        r = httpx.get(a.llm_url.rstrip("/") + "/models", timeout=3.0)
        llm = {"status": r.status_code, "models": [m.get("id") for m in r.json().get("data", [])]} if r.status_code == 200 else {"status": r.status_code}
    except Exception as e:  # noqa: BLE001
        llm = {"error": f"{type(e).__name__}"}
    out = {"environment": env, "extras": extras, "llm": llm, "sandbox_cmd": _sandbox_cmd(a), "bwrap": shutil.which("bwrap"), "firejail": shutil.which("firejail")}
    print(json.dumps(out, indent=1))
    return 0


def cmd_calibrate(a: argparse.Namespace) -> int:
    ctx = _open(a)
    try:
        cal = calibrate(ctx, target_id=a.target, replications=a.n, expected_ms=a.expected_ms, tolerance_ms=a.tolerance_ms)
    finally:
        ctx.mock.stop()
    print(json.dumps({"ok": cal["ok"], "run_dir": str(ctx.run_dir), "replications": [{"status": r["status"], "reason": r["reason"], "checks": {k: v["ok"] for k, v in r["integrity"]["checks"].items()}, "calibration": r["integrity"]["checks"].get("calibration")} for r in cal["replications"]]}, indent=1, default=str))
    return 0 if cal["ok"] else 1


def cmd_probe_run(a: argparse.Namespace) -> int:
    ctx = _open(a)
    # a probe run is a smoke or a rehearsal: the only kind of run that may use a target's smoke bound (fix A9)
    from .next_step import RUN_KIND_PROBE

    ctx.run_kind = RUN_KIND_PROBE
    try:
        if not a.skip_calibration:
            calibrate(ctx, target_id="scripted", replications=1)
        for control in a.control:
            res = run_cell(ctx, a.probe, a.target, control, a.workload, a.n)
            print(json.dumps({"probe": a.probe, "target": a.target, "control": control, "aggregate": res["aggregate"], "verdict": {k: res["verdict"][k] for k in ("label", "decisive", "outcome_if_decisive", "reasons")},
                              "not_run": res["replications"]["not_run"], "per_replication": [{"i": r["index"], "status": r["status"], "value": r["value"], "raw": {k: r["raw"].get(k) for k in ("post_halt_landed", "pre_halt_delayed", "halt_class", "effects_total", "payments_total")}} for r in res["per_replication"]]}, indent=1, default=str))
        out = close_run(ctx, benchmark=None, sign_key_path=Path(a.sign_key) if a.sign_key else None, cert_path=Path(a.cert) if a.cert else None)
    except Exception:
        ctx.mock.stop()
        raise
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["ledger"]["ok"] else 1


def cmd_bench_run(a: argparse.Namespace) -> int:
    import yaml

    spec = yaml.safe_load(Path(a.benchmark).read_text(encoding="utf-8"))
    # fix B6: an evaluated cell runs `replications_scheduled` and counts the first `replications` measured in schedule order
    from .runner import replication_schedule

    try:
        spec["replications_scheduled"], spec["replications"] = replication_schedule(spec, override=int(a.replications) if a.replications else None)
    except ValueError as e:
        raise SystemExit(f"bench run refused (fix B6): {e}")
    if a.reference_replications:
        spec["reference_replications"] = int(a.reference_replications)
    # fix A9 (founder ruling 2026-09-14): a bench run refuses, before it opens anything, any single-call target x model without
    # the value the bound rule produced; a smoke bound never carries a matrix
    from .next_step import check_window_bounds
    from .workloads import load as load_workloads

    refusals = check_window_bounds(spec["matrix"], load_workloads(), a.llm_model, targets=a.targets.split(",") if a.targets else None,
                                   probes=sorted(a.probes.split(",")) if getattr(a, "probes", None) else None)
    if refusals:
        raise SystemExit("bench run refused (fix A9): " + " | ".join(refusals))
    # fix A5: a declared scope resting on fewer replications than a gate's floor must say it is provisional, before anything opens
    from .next_step import check_declared_scopes
    from .runner import gate_min_replications

    scope_refusals = check_declared_scopes(spec["matrix"], load_workloads(), gate_min_replications(sorted({p["id"] for c in spec["matrix"] for p in c["probes"]})),
                                           targets=a.targets.split(",") if a.targets else None, probes=sorted(a.probes.split(",")) if getattr(a, "probes", None) else None)
    if scope_refusals:
        raise SystemExit("bench run refused (fix A5): " + " | ".join(scope_refusals))
    # change-set B5 (founder ruling 2026-09-15): the Lab's study set and a platform demonstration set are disjoint by upstream
    # subject; a run never opens on an overlap
    from .registry import load as load_registry
    from .registry import study_sets_record

    sets = study_sets_record(load_registry(a.registry).values())
    if sets["overlap"]:
        raise SystemExit("bench run refused (change-set B5): the labs and platform_demo study sets share " + ", ".join(sets["overlap"]))
    # fix A7: the env-test's attempts are read before anything opens; a failed env-test (a noisy host) refuses the run
    if a.env_test:
        from .env_test_record import collect

        et = collect(a.env_test)
        if et["outcome"] != "passed":
            raise SystemExit(f"bench run refused (fix A7): the env-test {et['prefix']} failed on its {et['final_attempt']} attempt; the calibration rule replaces the pod")
    # A4 (attempt 4): a bench run refuses, before it opens anything, any scheduled target without a pre-registered stall baseline
    # for the run's model; a probe run records the check as not applied and reads the replication normally. Last of the pre-open
    # refusals, so a spec from before this check still reports what it always reported first.
    from .stall import check_stall_baselines

    stall_refusals = check_stall_baselines(spec["matrix"], spec.get("stall_check"), a.llm_model, spec_id=spec.get("id"), targets=a.targets.split(",") if a.targets else None)
    if stall_refusals:
        raise SystemExit("bench run refused (A4): " + " | ".join(stall_refusals))
    ctx = _open(a)
    from .env_test_record import record_env_test

    record_env_test(ctx, a.env_test)
    ctx.benchmark_spec = {k: spec.get(k) for k in ("id", "description", "pre_registration", "replications", "replications_scheduled", "reference_replications", "calibration_replications", "families_disabled", "replay",
                                                   "stall_check")}
    # Coverage, per target, as the SPEC declares it: which probes this benchmark asks of each target at all. A
    # per-target pod runs one target, so the report cannot infer from its own rows that a probe was never asked
    # (oss-agent-controls-v1 has no openhands-sdk ks.propagation cell; fix B3 added the task, and langgraph-ref, with no
    # spawn tool, is left out of the propagation cell from attempt 3). The
    # report states it as a coverage line; a reader must not have to diff the matrix to notice (founder, 2026-09-12).
    declared: dict[str, list[str]] = {}
    for cell in spec["matrix"]:
        for target in cell["targets"]:
            for probe in cell["probes"]:
                declared.setdefault(target, [])
                if probe["id"] not in declared[target]:
                    declared[target].append(probe["id"])
    # A probe filter (founder ruling 2026-09-12: ks.resume re-run alone) selects cells from the unchanged spec; it is
    # recorded in the results and the manifest so a reader never mistakes a filtered run for the full matrix.
    probe_filter = sorted(a.probes.split(",")) if getattr(a, "probes", None) else None
    unknown = [p for p in (probe_filter or []) if not any(p == pr["id"] for c in spec["matrix"] for pr in c["probes"])]
    if unknown:
        raise SystemExit(f"--probes names probes the spec does not declare: {unknown}")
    ctx.benchmark_spec["probe_filter"] = probe_filter
    # Run order is a declared rule, not an emergent one (founder ruling 2026-09-12; the second time order mattered)
    ctx.benchmark_spec["ordering"] = {"rules": [
        "none runs before any control on the same target and workload",
        "a pace-dependent probe (ks.resume v3) runs only after a none cell on the same target and workload has set the measured pace; "
        "when none has (a filtered run), the runner first runs a recorded pace cell: ks.latency none on that workload",
        "a pace-dependent cell without a valid measured pace is not_run pace_unavailable or pace_invalid, never computed from a guess"]}
    all_probes = sorted({p for ps in declared.values() for p in ps})
    ctx.benchmark_spec["probes_declared_per_target"] = {t: sorted(ps) for t, ps in declared.items()}
    ctx.benchmark_spec["probes_not_declared_per_target"] = {t: [p for p in all_probes if p not in ps] for t, ps in declared.items() if [p for p in all_probes if p not in ps]}
    try:
        calibrate(ctx, target_id="scripted", replications=int(spec.get("calibration_replications", 1)))
        from mark_probes import PROBES

        for cell in spec["matrix"]:
            for probe in cell["probes"]:
                if probe_filter and probe["id"] not in probe_filter:
                    continue
                workloads = probe.get("workloads") or [probe["workload"]]
                for target in cell["targets"]:
                    if a.targets and target not in a.targets.split(","):
                        continue
                    # the ordering rule: none first on every cell
                    controls = (["none"] + [c for c in cell["controls"] if c != "none"]) if "none" in cell["controls"] else list(cell["controls"])
                    # a pace-dependent probe needs a measured pace from an earlier none cell; a filtered run gets a recorded pace cell
                    if getattr(PROBES.get(probe["id"]), "HOLD_PACES", 0):
                        for workload in workloads:
                            if (target, workload) not in ctx.paces:
                                pace_n = int(spec["replications"])
                                run_cell(ctx, "ks.latency", target, "none", workload, pace_n)
                                entry = {"for_probe": probe["id"], "cell": f"ks.latency/{target}/none/{workload}", "replications": pace_n, "pace": ctx.paces.get((target, workload))}
                                ctx.pace_cells.append(entry)
                                print(json.dumps({"pace_cell": entry["cell"], "pace": entry["pace"]}, default=str))
                    for control in controls:
                        for workload in workloads:
                            ctrl = ctx.registry[control]
                            is_ref = ctrl.category == "reference" or ctrl.control_class == "reference_instrument"
                            # a reference row runs and counts its own number; an evaluated row is over-scheduled (fix B6)
                            n = int(spec.get("reference_replications", 5)) if is_ref else int(spec["replications_scheduled"])
                            res = run_cell(ctx, probe["id"], target, control, workload, n, counted=None if is_ref else int(spec["replications"]))
                            print(json.dumps({"probe": probe["id"], "target": target, "control": control, "workload": workload, "variant": res["context"]["variant"], "aggregate": {k: res["aggregate"].get(k) for k in ("n", "median", "max")}, "verdict": res["verdict"]["label"], "not_run": len(res["replications"]["not_run"]), "extra": len(res["replications"].get("extra") or [])}, default=str))
        out = close_run(ctx, benchmark=spec.get("id"), sign_key_path=Path(a.sign_key) if a.sign_key else None, cert_path=Path(a.cert) if a.cert else None, signed_on=a.signed_on)
    except Exception:
        ctx.mock.stop()
        raise
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["ledger"]["ok"] else 1


def cmd_run_sign(a: argparse.Namespace) -> int:
    """Laptop side: sign manifest.unsigned.json where the key lives, verify, anchor, render. The pod never held the key."""
    from mark_ledger.canonical import sha256_hex
    from mark_ledger.manifest import manifest_signature_status, read_signed, sign_manifest, verify_manifest, write_signed
    from mark_ledger.store import Ledger

    from .report import report_run

    run_dir = Path(a.run_dir)
    manifest = json.loads((run_dir / "manifest.unsigned.json").read_text(encoding="utf-8"))
    chain = manifest["evidence"]["chain_id"]
    led = Ledger(run_dir / "ledger")
    v = led.verify(chain)
    if not v.ok or v.chain_root != manifest["evidence"]["chain_root"]:
        print(json.dumps({"ok": False, "reason": "ledger does not verify or its root differs from the unsigned manifest", "ledger": v.to_json()}))
        return 1
    if sha256_hex((run_dir / "results.json").read_bytes()) != manifest["evidence"]["results_sha256"]:
        print(json.dumps({"ok": False, "reason": "results.json hash differs from the unsigned manifest"}))
        return 1
    # A5 (attempt 4): the manifest's own account of the run is recomputed from the cell records; a manifest that states a count its
    # results do not add up to is not signed. Checked before the close steps so the refusal names the account and nothing else.
    from .account import check_manifest_account

    account_problems = check_manifest_account(run_dir)
    if account_problems:
        print(json.dumps({"ok": False, "reason": "the manifest's run-level account differs from the cell records (A5)", "differences": account_problems}))
        return 1
    # C2 (attempt 4): the operator of record in the manifest must be the one chained in the run_open record; a run that started
    # somewhere cannot finish claiming somewhere else. Checked beside the account, before the close steps.
    from .operator import check_operator_binding, run_open_operator

    operator_problems = check_operator_binding(manifest.get("operator"), run_open_operator(run_dir))
    if operator_problems:
        print(json.dumps({"ok": False, "reason": "the manifest's operator of record differs from the run_open record (C2)", "differences": operator_problems}))
        return 1
    # The close steps are kept by the tool, not the plan (founder ruling 2026-09-16): no signature without the offline
    # re-decision and the pass-sample review of this results.json in the ledger.
    from .close_steps import missing_close_steps

    missing = missing_close_steps(run_dir)
    if missing:
        print(json.dumps({"ok": False, "reason": "close steps missing before signing", "missing": missing}))
        return 1
    # The publication reading is decided here and signed inside the manifest (founder rulings 2026-09-12 and 2026-09-14): a
    # baseline invariant firing that is unexplained at signing makes the bundle informational; a bundle scoped per target and
    # workload variant is read per cell only when clarification v2 verifies as signed.
    from .invariant_diagnosis import CLARIFICATION_V2_ID, CLARIFICATION_V2_PATH, clarification_signed, publication_reading

    keys_dir = REPO_ROOT / "packages" / "bundles" / "keys"
    rev_path = keys_dir / "revocations.json"
    v2_signed = clarification_signed(REPO_ROOT / "gates", CLARIFICATION_V2_PATH, (keys_dir / "root.pub").read_text().strip(), json.loads(rev_path.read_text()) if rev_path.exists() else None,
                                     clarification_id=CLARIFICATION_V2_ID)
    manifest.setdefault("environment", {})["publication_reading"] = publication_reading(json.loads((run_dir / "results.json").read_text(encoding="utf-8")), manifest, v2_signed=v2_signed)
    so = sign_manifest(manifest, Path(a.key).read_text().strip(), json.loads(Path(a.cert).read_text()), signed_on="laptop")
    write_signed(run_dir / "manifest.json", so)
    root_pub = (REPO_ROOT / "packages" / "bundles" / "keys" / "root.pub").read_text().strip()
    rev_p = REPO_ROOT / "packages" / "bundles" / "keys" / "revocations.json"
    verify_manifest(read_signed(run_dir / "manifest.json"), root_pub, revocations=json.loads(rev_p.read_text()) if rev_p.exists() else None)
    out = {"ok": True, "manifest": str(run_dir / "manifest.json"), "signature": manifest_signature_status(so), "signed_on": "laptop", "anchor": None,
           "publication_reading": manifest["environment"]["publication_reading"]}
    if a.anchor == "rekor":
        from mark_ledger.anchor import RekorAnchor, ed25519_public_pem

        priv = Path(a.key).read_text().strip()
        anc = RekorAnchor(priv, ed25519_public_pem(priv)).anchor(chain, v.chain_root)
        led.record_anchor(anc.to_json())
        out["anchor"] = {"authority": "rekor", "logIndex": anc.receipt.get("logIndex"), "url": anc.receipt.get("url")}
    out["ledger_after"] = led.verify(chain).to_json()["ok"]
    report_run(run_dir)
    print(json.dumps(out, indent=1))
    return 0 if out["ledger_after"] else 1


def cmd_run_redecide(a: argparse.Namespace) -> int:
    """Laptop side, before signing: decide variant presence over the whole matrix and apply the primitive rule for
    none, for a bundle closed by a runner that decided them per cell. Verdicts only; measurements unchanged."""
    from .redecide import RedecideRefused, redecide_bundle
    from .runner import ENGINE_VERSION, repo_commit

    keys = REPO_ROOT / "packages" / "bundles" / "keys"
    rev_p = keys / "revocations.json"
    try:
        out = redecide_bundle(a.run_dir, gates_dir=REPO_ROOT / "gates", root_public_hex=(keys / "root.pub").read_text().strip(),
                              revocations=json.loads(rev_p.read_text()) if rev_p.exists() else None, engine_version=ENGINE_VERSION, repo_commit=repo_commit(),
                              max_model_len=a.max_model_len)
    except RedecideRefused as e:
        print(json.dumps({"ok": False, "reason": str(e)}))
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_run_pass_sample_record(a: argparse.Namespace) -> int:
    """Laptop side, after redecide and before signing: the pass-sample review, written into the ledger (founder ruling
    2026-09-16). `run sign` refuses a bundle without it."""
    from .close_steps import CloseStepRefused, record_pass_sample_review
    from .runner import ENGINE_VERSION, repo_commit

    try:
        out = record_pass_sample_review(a.run_dir, json.loads(Path(a.review).read_text(encoding="utf-8")), engine_version=ENGINE_VERSION, repo_commit=repo_commit())
    except CloseStepRefused as e:
        print(json.dumps({"ok": False, "reason": str(e)}))
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_run_replay_fidelity(a: argparse.Namespace) -> int:
    """Pod side, after close and before export: re-send every scenario's captured first request to the run's own server
    and record the rate at which the captured reply is reproduced (founder ruling 2026-09-12)."""
    from .replay_fidelity import FidelityRefused, replay_fidelity
    from .runner import ENGINE_VERSION, repo_commit

    try:
        out = replay_fidelity(a.run_dir, arm=a.arm, llm_url=a.upstream, engine_version=ENGINE_VERSION, repo_commit=repo_commit())
    except FidelityRefused as e:
        print(json.dumps({"ok": False, "reason": str(e)}))
        return 1
    print(json.dumps({**{k: v for k, v in out.items() if k not in ("mismatches", "run_condition", "condition_at_replay")}, "mismatches_listed": len(out.get("mismatches") or [])}, indent=1))
    return 0


def cmd_run_replay_order(a: argparse.Namespace) -> int:
    """Pod side, only when the run-server arm's fidelity is poor: replay the captured first requests twice in opposite
    orders under the server as it now stands (--enforce-eager) and compare the two replays with each other."""
    from .replay_fidelity import FidelityRefused, replay_order_dependence
    from .runner import ENGINE_VERSION, repo_commit

    try:
        out = replay_order_dependence(a.run_dir, arm=a.arm, llm_url=a.upstream, engine_version=ENGINE_VERSION, repo_commit=repo_commit())
    except FidelityRefused as e:
        print(json.dumps({"ok": False, "reason": str(e)}))
        return 1
    print(json.dumps({**{k: v for k, v in out.items() if k not in ("disagreements", "run_condition", "condition_at_replay")}, "disagreements_listed": len(out.get("disagreements") or [])}, indent=1))
    return 0


def cmd_run_diagnose_invariant(a: argparse.Namespace) -> int:
    """Laptop side, before signing: record that a fired baseline invariant was an invariant defect, its probe fully excluded
    (clarification baseline_invariant_firing.v1, bundles scoped per probe) or its probe on the named target and workload
    variant fully excluded (v2, bundles scoped per target and variant; read per cell only once v2 verifies as signed)."""
    from .invariant_diagnosis import CLARIFICATION_V2_ID, CLARIFICATION_V2_PATH, DiagnosisRefused, clarification_signed, record_invariant_diagnosis
    from .runner import ENGINE_VERSION, repo_commit

    keys = REPO_ROOT / "packages" / "bundles" / "keys"
    rev_p = keys / "revocations.json"
    v2_signed = clarification_signed(REPO_ROOT / "gates", CLARIFICATION_V2_PATH, (keys / "root.pub").read_text().strip(), json.loads(rev_p.read_text()) if rev_p.exists() else None,
                                     clarification_id=CLARIFICATION_V2_ID)
    try:
        out = record_invariant_diagnosis(a.run_dir, probe_id=a.probe, classification=a.classification, diagnosis=Path(a.diagnosis).read_text(encoding="utf-8").strip(),
                                         evidence=Path(a.evidence).read_text(encoding="utf-8").strip(), fix_commit=a.fix_commit, engine_version=ENGINE_VERSION, repo_commit=repo_commit(),
                                         target=a.target, variant=a.variant, v2_signed=v2_signed)
    except DiagnosisRefused as e:
        print(json.dumps({"ok": False, "reason": str(e)}))
        return 1
    print(json.dumps(out, indent=1))
    return 0


def cmd_run_audit_evidence(a: argparse.Namespace) -> int:
    """Laptop side, first thing after a verified fetch: the attempt 3 evidence-sourcing audit (clarification
    evidence_sourcing_audit.v1). Writes <run>.evidence-audit.json beside the bundle and never touches the bundle."""
    from .evidence_audit import AuditRefused, run_audit

    keys = REPO_ROOT / "packages" / "bundles" / "keys"
    rev_p = keys / "revocations.json"
    try:
        out = run_audit(a.run_dir, gates_dir=REPO_ROOT / "gates", root_public_hex=(keys / "root.pub").read_text().strip(),
                        revocations=json.loads(rev_p.read_text()) if rev_p.exists() else None, out_dir=a.out_dir)
    except AuditRefused as e:
        print(json.dumps({"ok": False, "reason": str(e)}))
        return 1
    print(json.dumps({"ok": True, **out}, indent=1))
    return 0


def cmd_run_export(a: argparse.Namespace) -> int:
    from mark_ledger.store import Ledger

    run_dir = Path(a.run_dir)
    led = Ledger(run_dir / "ledger")
    chains = led.list_chains()
    bad = [c for c in chains if not led.verify(c).ok]
    if bad or not chains:
        print(json.dumps({"ok": False, "reason": f"ledger does not verify: {bad or 'no chains'}"}))
        return 1
    dest = Path(a.dest) / run_dir.name
    if dest.exists():
        print(json.dumps({"ok": False, "reason": f"{dest} exists; exports are never overwritten"}))
        return 1
    # never export secrets or the agents' scratch work; keep results, ledger, spans, mock calls, manifest, agent results/logs
    def ignore(d, names):
        return {n for n in names if n == "work" or n.endswith(".key") or n == ".env"}
    shutil.copytree(run_dir, dest, ignore=ignore)
    print(json.dumps({"ok": True, "exported": str(dest), "chains": chains}))
    return 0


def cmd_ledger_verify(a: argparse.Namespace) -> int:
    from mark_ledger.cli import main as ledger_main

    return ledger_main(["verify", a.ledger, *a.chain, "--pretty"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="platform")
    p.add_argument("--llm-url", default=os.environ.get("MARK_LLM_URL", "http://127.0.0.1:8000/v1"))
    p.add_argument("--llm-model", default=os.environ.get("MARK_LLM_MODEL", ""))
    p.add_argument("--tools", default=os.environ.get("MARK_TOOLS", "mcp"), choices=["mcp", "inproc"])
    p.add_argument("--sandbox", default=os.environ.get("MARK_SANDBOX_CMD", ""), help="command prefix that wraps the agent process, e.g. the bwrap wrapper")
    p.add_argument("--registry", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    env = sub.add_parser("env")
    envs = env.add_subparsers(dest="sub", required=True)
    envs.add_parser("check").set_defaults(fn=cmd_env_check)

    cal = sub.add_parser("calibrate")
    cal.add_argument("--run-dir", required=True)
    cal.add_argument("--run-id")
    cal.add_argument("--target", default="scripted")
    cal.add_argument("-n", type=int, default=1)
    cal.add_argument("--expected-ms", type=float, default=250.0)
    cal.add_argument("--tolerance-ms", type=float, default=5.0)
    cal.set_defaults(fn=cmd_calibrate)

    probe = sub.add_parser("probe")
    ps = probe.add_subparsers(dest="sub", required=True)
    pr = ps.add_parser("run")
    pr.add_argument("probe")
    pr.add_argument("--target", required=True)
    pr.add_argument("--control", action="append", required=True)
    pr.add_argument("--workload", required=True)
    pr.add_argument("-n", type=int, default=3)
    pr.add_argument("--run-dir", required=True)
    pr.add_argument("--run-id")
    pr.add_argument("--skip-calibration", action="store_true")
    pr.add_argument("--sign-key")
    pr.add_argument("--cert")
    pr.set_defaults(fn=cmd_probe_run)

    bench = sub.add_parser("bench")
    bs = bench.add_subparsers(dest="sub", required=True)
    br = bs.add_parser("run")
    br.add_argument("benchmark")
    br.add_argument("--run-dir", required=True)
    br.add_argument("--run-id")
    br.add_argument("--replications", type=int, default=None, help="override the spec's replications (the founder's floor is 20)")
    br.add_argument("--targets", default=None, help="comma-separated subset of targets (one target per pod for parallel runs)")
    br.add_argument("--probes", default=None, help="comma-separated subset of the spec's probes; recorded in results and manifest as probe_filter")
    br.add_argument("--reference-replications", type=int, default=None, help="override the spec's replications for reference rows (rehearsals)")
    br.add_argument("--sign-key")
    br.add_argument("--cert")
    br.add_argument("--signed-on", default="pod", help="recorded in the manifest when signing here; the laptop path is `run sign`")
    br.add_argument("--env-test", default=None, help="the env-test prefix env-test.sh wrote ($RUNS/env-test-latest): every attempt goes into the run record (fix A7)")
    br.set_defaults(fn=cmd_bench_run)

    cons = bs.add_parser("consistency", help="verify per-target runs share image, model, gates, probes, workloads before citing them as one matrix")
    cons.add_argument("run_dirs", nargs="+")
    cons.set_defaults(fn=lambda a: (lambda r: (print(json.dumps(r, indent=1)) or (0 if r["consistent"] else 1)))(__import__("mark_platform.report", fromlist=["consistency"]).consistency(a.run_dirs)))

    tl = bs.add_parser("timeline", help="raw timelines of sampled replications; --verdict pass is the pass-sample rule (pre-flight item 7)")
    tl.add_argument("--run-dir", required=True)
    tl.add_argument("--probe"); tl.add_argument("--control"); tl.add_argument("--workload")
    tl.add_argument("--verdict", default="pass", help="pass | fail | revocation | control_message | not_attempted | mixed | any")
    tl.add_argument("--sample", type=int, default=2); tl.add_argument("--seed", type=int, default=7); tl.add_argument("--calls", type=int, default=24)
    tl.set_defaults(fn=lambda a: (print(__import__("mark_platform.timeline", fromlist=["timeline"]).timeline(
        a.run_dir, probe=a.probe, control=a.control, workload=a.workload, verdict=a.verdict, sample=a.sample, seed=a.seed, calls=a.calls)) or 0))

    rep = bs.add_parser("report")
    rep.add_argument("run_dir")
    rep.set_defaults(fn=lambda a: (print(__import__("mark_platform.report", fromlist=["report_run"]).report_run(a.run_dir)) or 0))

    run = sub.add_parser("run")
    rs = run.add_subparsers(dest="sub", required=True)
    sg = rs.add_parser("sign")
    sg.add_argument("--run-dir", required=True)
    sg.add_argument("--key", required=True)
    sg.add_argument("--cert", required=True)
    sg.add_argument("--anchor", choices=["rekor", "none"], default="rekor")
    sg.set_defaults(fn=cmd_run_sign)

    rd = rs.add_parser("redecide", help="decide variant presence over the whole matrix (and the none primitive rule) before signing")
    rd.add_argument("--run-dir", required=True)
    rd.add_argument("--max-model-len", type=int, default=None, help="the served context length, for a bundle that pre-dates the serving pin (recorded as supplied at read time)")
    rd.set_defaults(fn=cmd_run_redecide)

    ps = rs.add_parser("pass-sample-record", help="record the pass-sample review of every decisive pass in the ledger, after redecide and before signing")
    ps.add_argument("--run-dir", required=True)
    ps.add_argument("--review", required=True, help="JSON: {reviewer, method, cells: {probe/target/control/workload: {reading: earned|unearned, basis, replications_read}}}")
    ps.set_defaults(fn=cmd_run_pass_sample_record)

    rf = rs.add_parser("replay-fidelity", help="re-send each scenario's captured first request to the run's server and record the reproduction rate, before signing")
    rf.add_argument("--run-dir", required=True)
    rf.add_argument("--arm", default="run-server", help="a name for this arm; one record per name")
    rf.add_argument("--upstream", default=None, help="the model server, when the run recorded none")
    rf.set_defaults(fn=cmd_run_replay_fidelity)

    ro = rs.add_parser("replay-order", help="replay the captured first requests twice in opposite orders and compare the replays with each other (the --enforce-eager arm)")
    ro.add_argument("--run-dir", required=True)
    ro.add_argument("--arm", default="enforce-eager", help="a name for this arm; one record per name")
    ro.add_argument("--upstream", default=None, help="the model server, when the run recorded none")
    ro.set_defaults(fn=cmd_run_replay_order)

    dg = rs.add_parser("diagnose-invariant", help="record, before signing, that a fired baseline invariant was an invariant defect")
    dg.add_argument("--run-dir", required=True)
    dg.add_argument("--probe", required=True)
    dg.add_argument("--classification", default="invariant_defect")
    dg.add_argument("--diagnosis", required=True, help="file: what was wrong with the invariant, in words")
    dg.add_argument("--evidence", required=True, help="file: the raw evidence the diagnosis rests on")
    dg.add_argument("--fix-commit", required=True)
    dg.add_argument("--target", default=None, help="the fired cell's target (bundles scoped per target and workload variant)")
    dg.add_argument("--variant", default=None, help="the fired cell's workload variant (bundles scoped per target and workload variant)")
    dg.set_defaults(fn=cmd_run_diagnose_invariant)

    ae = rs.add_parser("audit-evidence", help="attempt 3: check agent-side values against out-of-process receipts (clarification evidence_sourcing_audit.v1), first thing after a verified fetch")
    ae.add_argument("--run-dir", required=True)
    ae.add_argument("--out-dir", default=None, help="where <run>.evidence-audit.json goes (default: beside the bundle)")
    ae.set_defaults(fn=cmd_run_audit_evidence)

    ex = rs.add_parser("export")
    ex.add_argument("--run-dir", required=True)
    ex.add_argument("--dest", required=True)
    ex.set_defaults(fn=cmd_run_export)

    led = sub.add_parser("ledger")
    ls = led.add_subparsers(dest="sub", required=True)
    lv = ls.add_parser("verify")
    lv.add_argument("ledger")
    lv.add_argument("chain", nargs="*")
    lv.set_defaults(fn=cmd_ledger_verify)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
