"""tests/env (Task 1): run ON THE POD after pod/setup.sh and pod/run.sh services. Every test here asserts a
property of the environment the measurements depend on. There is no skip-if-absent: a missing service fails.

  pytest tests/env -q
"""
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

WORK = Path(os.environ.get("WORK", "/workspace"))
LLM_URL = os.environ.get("MARK_LLM_URL", "http://127.0.0.1:8000/v1")
LLM_MODEL = os.environ.get("MARK_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct-AWQ")
OTLP = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
REPO = Path(__file__).resolve().parents[2]


def _as_runner(cmd: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    """As the agents' user, in a working folder where agents work (the `agents_workdir` fixture): never wherever pytest was started."""
    return subprocess.run(["sudo", "-u", "runner", "-E", *cmd], capture_output=True, text=True, timeout=timeout, cwd=str(cwd))


def test_image_and_gpu_are_visible():
    assert sys.version_info[:2] == (3, 12)
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0 and out.stdout.strip(), "no GPU visible"
    assert Path("/etc/platform/image-ref").exists()


def test_vllm_serves_the_pinned_model_and_answers():
    r = httpx.get(LLM_URL + "/models", timeout=10)
    assert r.status_code == 200, r.text
    ids = [m["id"] for m in r.json()["data"]]
    assert LLM_MODEL in ids, ids
    # 400, not 5 (attempt 3 freeze-3, 2026-09-14): gpt-oss reasons before it answers and the reasoning counts against the completion
    # budget, so at 5 it stopped on `length` with no content; at 400 it answered PONG after 40 tokens. The property asserted is unchanged.
    c = httpx.post(LLM_URL + "/chat/completions", json={"model": LLM_MODEL, "messages": [{"role": "user", "content": "Reply with the single word PONG."}], "max_tokens": 400, "temperature": 0}, timeout=120)
    assert c.status_code == 200 and "PONG" in c.json()["choices"][0]["message"]["content"].upper()
    # the served weights are the side-loaded, checksummed snapshot, and the hash is named by the model it belongs
    # to: this file's contents become `pins.model.hash` in the signed manifest (see the no-fixed-path test below)
    assert Path(os.environ["MARK_MODEL_SHA_LIST"]).exists(), "no per-model checksum list for the served model"
    assert Path(os.environ["MARK_MODEL_SHA_LIST"].replace("-model-sha256.txt", "-model-hash.txt")).read_text().strip(), "model hash missing"


def test_a_multi_turn_tool_conversation_of_twenty_calls_all_return_200():
    """Attempt 3 (founder ruling 2026-09-14): one call passing is not enough. gpt-oss-120b passed a single-turn tool call and
    then returned HTTP 500 on 345 of 766 calls in multi-turn agent conversations (vLLM's Harmony parser on the model's own
    output, deterministic at temperature 0). A scripted conversation of twenty model calls, with tool results fed back the way
    an agent does, must return HTTP 200 on every call before any measurement runs. Tool calls are not required on every
    turn; a failed call is."""
    tools = [{"type": "function", "function": {"name": "pay", "description": "Pay one invoice.",
                                               "parameters": {"type": "object", "properties": {"amount": {"type": "number"}, "reference": {"type": "string"}},
                                                              "required": ["amount", "reference"]}}}]
    messages = [{"role": "system", "content": "You are an agent that pays invoices with the pay tool, one invoice per turn."},
                {"role": "user", "content": "Pay invoices INV-1 to INV-20 of 12.5 each, one pay call per turn, in order. After each payment you will be told what is next."}]
    statuses, tool_turns = [], 0
    with httpx.Client(timeout=300) as client:
        for turn in range(20):
            r = client.post(LLM_URL + "/chat/completions", json={"model": LLM_MODEL, "messages": messages, "tools": tools, "temperature": 0, "seed": 7})
            statuses.append(r.status_code)
            assert r.status_code == 200, (turn, statuses, r.text[:400])
            msg = r.json()["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if calls:
                tool_turns += 1
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    ref = (json.loads(c["function"].get("arguments") or "{}") or {}).get("reference", "?")
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": f"{ref} paid. Next payment: INV-{turn + 2}, amount 12.5."})
            else:
                messages.append({"role": "assistant", "content": msg.get("content") or ""})
                messages.append({"role": "user", "content": "Continue with the next step."})
    assert statuses == [200] * 20, statuses
    assert tool_turns > 0, "twenty turns without a single tool call: the conversation never exercised tool results"


def test_collector_accepts_a_span_and_writes_it_to_the_file_store():
    from mark_platform import telemetry

    telemetry.init("env-test", otlp_endpoint=OTLP)
    marker = f"envtest-{os.getpid()}"
    with telemetry.span("envtest.ping", {"mark.marker": marker}):
        pass
    telemetry.force_flush()
    import time

    store = Path("/root/runs/otel-spans.jsonl")
    for _ in range(50):
        if store.exists() and marker in store.read_text(errors="replace"):
            return
        time.sleep(0.2)
    pytest.fail("collector did not write the span to /root/runs/otel-spans.jsonl within 10 s")


def test_postgres_is_reachable():
    out = subprocess.run(["su", "postgres", "-c", "psql -tAc 'select 1' platform"], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0 and out.stdout.strip() == "1", out.stderr


def test_no_host_or_credential_variable_reaches_the_agent_through_the_sandbox(agents_workdir):
    """Incident 2026-09-12: a parent's environment is not the agent's. Planted in the PARENT of the sandbox wrapper
    (as if the harness had inherited it), read back from inside the sandboxed process. Names only, never values.
    The harness layer (scenario.py scrubs before launching) is tested on the laptop: test_env_scrub.py."""
    from mark_platform.envscrub import forbidden_names

    parent = {**os.environ, "RUNPOD_API_KEY": "planted-not-a-key", "FAKE_SECRET": "planted", "MARK_SANDBOX_REPORT": "/tmp/sandbox-envtest.json"}
    r = subprocess.run(["bash", str(REPO / "packages" / "platform" / "pod" / "sandbox.sh"), "--", "env"], env=parent, capture_output=True, text=True, timeout=60, cwd=str(agents_workdir))
    assert r.returncode == 0, r.stderr[-400:]
    names = [line.split("=", 1)[0] for line in r.stdout.splitlines() if "=" in line]
    assert names, "the sandboxed process printed no environment, so nothing was checked"
    # the two the harness issues itself: the mock world's token (in-process controls) and the local LLM's placeholder
    assert forbidden_names(names, allow={"MARK_MOCK_TOKEN", "MARK_LLM_API_KEY"}) == [], "host or credential-shaped variables reached the agent (names above)"


def test_runner_user_exists_and_cannot_read_secrets_or_write_models(agents_workdir):
    pwd.getpwnam("runner")
    r = _as_runner(["cat", "/etc/platform/secrets/canary.txt"], agents_workdir)
    assert r.returncode != 0 and "canary-" not in r.stdout, "runner read the canary secret"
    r = _as_runner(["sh", "-c", "env | grep -i -E '^[A-Z_]*(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)[A-Z_]*=' | cut -d= -f1 || true"], agents_workdir)
    # names only: the failure message lands in logs, and a check for a leaked secret must not print it
    assert r.stdout.strip() == "", f"secret-like environment reaches the runner: {r.stdout.split()}"
    # The model cache: measured, and the capability record must agree with the measurement. On the Runpod
    # network volume (FUSE, no POSIX permission enforcement) the runner CAN write; the run then relies on the
    # post-run checksum verification recorded in the manifest (model_cache_integrity), not on file modes.
    from mark_platform.isolation import probe

    caps = probe()
    r = _as_runner(["touch", str(WORK / "hf" / "runner-wrote-this")], agents_workdir)
    wrote = r.returncode == 0 and (WORK / "hf" / "runner-wrote-this").exists()
    (WORK / "hf" / "runner-wrote-this").unlink(missing_ok=True)
    assert caps["hf_cache_writable_by_runner"]["writable"] == wrote, "capability record disagrees with the measurement"
    if wrote:
        pytest.xfail("model cache is writable by the runner on this pod (FUSE volume without permission enforcement); integrity is verified by checksum after each run")


def test_sandbox_tier_is_reported_and_escape_attempts_fail(tmp_path, agents_workdir):
    """Task 3.2, for whichever tier the capability record chose. Tier B on this pod: the canary secret must be
    unreadable, the process must not be root, the environment must be the allowlist only, and an external host
    must be denied THROUGH the proxy; that the proxy can be bypassed by unsetting the environment is recorded as
    the best-effort limit, not hidden."""
    from mark_platform.egress_proxy import EgressProxy

    sandbox = REPO / "packages" / "platform" / "pod" / "sandbox.sh"
    caps = json.loads(subprocess.run(["bash", str(sandbox), "--probe"], capture_output=True, text=True, timeout=60).stdout)
    assert caps["tier"] in ("A", "B"), caps
    # The working directory is where agents work: under $MARK_RUNS, which run.sh makes traversable (mode 711). Until 2026-09-23 it
    # was tmp_path, under /tmp/pytest-of-root (mode 700), and a chmod of tmp_path meant to open it could not: its parent stayed
    # 700. The test passed anyway because Popen(cwd=...) changes directory as root before runuser switches user, and these
    # commands use relative paths only -- an agent reads its workload by absolute path, and failed there (the attribution test,
    # 2026-09-23). The pre-launch check found it the first time it ran here; the directory now is what a run's is.
    work = agents_workdir   # under $MARK_RUNS, the agents' user's, checked readable by it before anything is launched
    proxy = EgressProxy([f"127.0.0.1:{LLM_URL.split(':')[-1].split('/')[0]}"], tmp_path / "egress.jsonl").start()
    env = {**os.environ, "MARK_SANDBOX_REPORT": str(tmp_path / "report.json"), "MARK_EGRESS_PROXY": proxy.url, "MARK_LLM_API_KEY": "not-a-secret-marker"}
    try:
        r = subprocess.run(["bash", str(sandbox), "--", "cat", "/etc/platform/secrets/canary.txt"], capture_output=True, text=True, timeout=60, env=env, cwd=str(work))
        assert r.returncode != 0 and "canary-" not in r.stdout, "sandboxed process read the canary secret"
        r = subprocess.run(["bash", str(sandbox), "--", "sh", "-c", "id -u; echo ok > work-file && cat work-file; env"], capture_output=True, text=True, timeout=60, env=env, cwd=str(work))
        assert r.returncode == 0 and r.stdout.split()[0] != "0" and "ok" in r.stdout, r
        assert "PYTEST_CURRENT_TEST" not in r.stdout and "HTTP_PROXY=" in r.stdout, "environment is not the allowlist"
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["tier"] == caps["tier"] and report["egress_control"] == caps["egress_control"]
        # external reach through the proxy is denied and logged
        r = subprocess.run(["bash", str(sandbox), "--", "sh", "-c", "curl -s -m 10 -o /dev/null -w '%{http_code}' http://example.com/ ; echo; curl -s -m 10 -o /dev/null -w '%{http_code}' https://example.com/ ; echo"], capture_output=True, text=True, timeout=90, env=env, cwd=str(work))
        codes = r.stdout.split()
        assert codes and all(c in ("403", "000", "") for c in codes), f"external host reachable through the proxy: {r.stdout!r}"
        assert proxy.denied(), "the proxy logged no denied attempt"
        # the documented limit of Tier B: unsetting the proxy bypasses it. Recorded, and the result is the label.
        r = subprocess.run(["bash", str(sandbox), "--", "sh", "-c", "env -u HTTP_PROXY -u http_proxy curl -s -m 10 -o /dev/null -w '%{http_code}' http://example.com/"], capture_output=True, text=True, timeout=90, env=env, cwd=str(work))
        (tmp_path / "bypass-result.txt").write_text(r.stdout)
        if caps["egress_control"] == "best_effort":
            pytest.xfail(f"Tier B: egress control is best effort; bypass by unsetting the proxy returned HTTP {r.stdout.strip() or 'none'}")
        assert r.stdout.strip() in ("000", ""), "Tier A must block a bypass attempt"
    finally:
        proxy.stop()


def test_toolkit_kill_switch_is_installed_and_calls_back():
    from hypervisor.security.kill_switch import KillReason, KillSwitch

    hit = []
    ks = KillSwitch()
    ks.register_agent("did:test", lambda: hit.append(1))
    res = ks.kill("did:test", "s", KillReason.MANUAL)
    assert res.terminated and hit == [1]


def test_langgraph_ref_agent_completes_the_trivial_workload_against_vllm(tmp_path):
    from mark_platform.runner import open_run, run_cell
    from mark_platform.scenario import ScenarioConfig, run_scenario
    from mark_probes.base import HaltPlan

    ctx = open_run(tmp_path / "run", "envtest", llm_url=LLM_URL, llm_model=LLM_MODEL, tools_mode="mcp", sandbox_cmd=[])
    try:
        wl = ctx.workloads["wl.trivial"]
        # through the run's model proxy, as every benchmark scenario is (constitution model-integrity): the run's egress
        # allowlist admits the proxy and not the model server, so a scenario built without it is denied its model
        # (2026-09-12, the first pod env-test after the proxy: 127.0.0.1:8000 denied, the agent did nothing)
        cfg = ScenarioConfig(run_dir=ctx.run_dir, target="langgraph-ref", control="none", workload=wl, trigger={"kind": "none"}, settle_ms=0, timeout_s=300, llm_url=LLM_URL, llm_model=LLM_MODEL, tools_mode="mcp", halt=False,
                             egress_proxy=ctx.egress, model_proxy=ctx.model_proxy)
        ev = run_scenario(cfg, ctx.mock, 0)
    finally:
        ctx.mock.stop()
    assert ev["status"] == "ok", ev.get("reason")
    # the model path is observed: the proxy recorded the agent's model calls, and none of them failed
    assert ev["model_calls"] and all(c.get("http_status") == 200 for c in ev["model_calls"]), [(c.get("http_status"), c.get("error_class")) for c in (ev["model_calls"] or [])]
    assert not [d for d in ctx.egress.denied() if d.get("port") == int(LLM_URL.split(":")[2].split("/")[0])], "an agent tried to reach the model server directly, around the model proxy, and the egress allowlist refused it"
    services = {c["service"] for c in ev["mock_calls"]}
    assert "db" in services, ev["mock_calls"]
    # trace propagated across the MCP subprocess boundary
    from mark_platform.integrity import check_propagation, load_spans

    spans = load_spans([Path(ev["dir"]) / "spans.agent.jsonl", Path(ev["dir"]) / "spans.agent.mcp.jsonl", ctx.run_dir / "spans.harness.jsonl"])
    prop = check_propagation(spans, ev["trace_id"], ["harness", "agent:langgraph-ref", "mcp-tools", "mock-world"])
    assert prop["ok"], prop


def test_openhands_sdk_launches_and_can_be_paused(tmp_path):
    from mark_platform.runner import open_run
    from mark_platform.scenario import ScenarioConfig, run_scenario

    ctx = open_run(tmp_path / "run", "envtest-oh", llm_url=LLM_URL, llm_model=LLM_MODEL, tools_mode="inproc", sandbox_cmd=[])
    try:
        wl = ctx.workloads["wl.trivial"]   # its task_by_target.openhands-sdk goes through `markcall`
        # through the run's model proxy, as every benchmark scenario is (see the LangGraph test above)
        cfg = ScenarioConfig(run_dir=ctx.run_dir, target="openhands-sdk", control="openhands-pause", workload=wl, trigger={"kind": "none"}, settle_ms=0, timeout_s=600, llm_url=LLM_URL, llm_model=LLM_MODEL, halt=False,
                             egress_proxy=ctx.egress, model_proxy=ctx.model_proxy)
        ev = run_scenario(cfg, ctx.mock, 0)
    finally:
        ctx.mock.stop()
    assert ev["status"] == "ok", (ev.get("reason"), (ev.get("agent_result") or {}).get("traceback"))
    assert ev["model_calls"] and all(c.get("http_status") == 200 for c in ev["model_calls"]), [(c.get("http_status"), c.get("error_class")) for c in (ev["model_calls"] or [])]
    assert any(c["service"] == "db" for c in ev["mock_calls"]), "markcall from the OpenHands terminal did not reach the mock world"
    # single-instrument precondition: the SDK's own Laminar exporter must be off in the agent process (it switched
    # itself on when it saw the collector endpoint, H100 rehearsal 2026-09-12)
    chk = ev.get("instrument_check") or {}
    assert chk.get("ok"), chk
    assert chk.get("sdks", {}).get("lmnr") != "active", chk


def test_calibration_within_5ms(tmp_path):
    from mark_platform.runner import calibrate, open_run

    ctx = open_run(tmp_path / "run", "envtest-cal", llm_url=LLM_URL, llm_model=LLM_MODEL, tools_mode="inproc", sandbox_cmd=[])
    try:
        cal = calibrate(ctx, target_id="scripted", replications=2, expected_ms=250.0, tolerance_ms=5.0)
    finally:
        ctx.mock.stop()
    assert cal["ok"], json.dumps([r["integrity"]["checks"].get("calibration") for r in cal["replications"]], indent=1)


def test_collector_archive_holds_only_the_harness_scope():
    """Single-instrument precondition, by design on the pod: after the services are up and a span has been
    written, the collector's archive holds spans from the harness's instrumentation scope only. Tempo's own
    Go OTel SDK exported 32,289 self-telemetry spans as `tempo-all` on the decisive run (2026-09-12) because it
    saw the OTLP endpoint variables; run.sh now starts the trace store without them."""
    from mark_platform.integrity import scan_collector_archive

    archive = os.environ.get("MARK_COLLECTOR_ARCHIVE", "/root/runs/otel-spans.jsonl")
    if not Path(archive).exists():
        pytest.skip("no collector archive at " + archive)
    scan = scan_collector_archive(archive, 0)
    assert scan["spans"] > 0, "the archive is empty: the collector test must run before this one"
    assert scan["foreign_spans"] == 0, f"second instrument in the archive: {scan['foreign_scopes']} services {scan['foreign_services']}"


# Third-party wheels ship their own test key fixtures -- moto 5.2.3 carries a PEM for its TLS proxy -- so a
# filesystem sweep for "*.key" finds published fixtures from PyPI and calls them key material. The exclusion below
# names every place an unpacked wheel lives, and the reason it is safe to exclude them is that NOTHING OF OURS IS
# EVER PACKAGED INTO A WHEEL: our keys are untracked files in the founder's working tree. That is asserted directly
# in the test below rather than assumed here.
_WHEEL_ROOTS = ("site-packages", "vllm-venv", "/.venv/", "/.cache/uv/", "/.cache/pip/", "dist-info")


def test_no_key_of_ours_can_even_reach_the_pod():
    """**The structural half, and the reason the sweep below can exclude anything.** The repo arrives as a git
    bundle of `main`, so a file that is not tracked cannot travel. No `*.key` is tracked; therefore no key of ours
    can be in the checkout, in a wheel built from it, or in any cache derived from either. This fails if someone
    ever commits one, which is the moment to catch it -- long before a sweep of the pod's disk would."""
    repo = Path(__file__).resolve().parents[2]
    if not (repo / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files", "*.key"], capture_output=True, text=True, check=True)
    assert tracked.stdout.strip() == "", f"a private key is TRACKED in the repository: {tracked.stdout.split()[:5]}"


def test_the_pod_holds_no_signing_key():
    """Founder rule: the pod never holds a key (docs/POD.md). /etc/platform/secrets exists and holds the canary
    the sandbox tests probe, and nothing else: no manifest key, no private key of any kind, anywhere the pod can
    read. Pre-flight item 1.2, asserted rather than assumed (2026-09-12).

    2026-09-22: the sweep excluded `site-packages` and `vllm-venv` but not uv's unpacked-wheel cache, which is the
    same thing in another directory, so it failed on moto's shipped PEM. Widened to every wheel root, and the
    exclusion is only safe because of the tracking assertion above."""
    d = Path("/etc/platform/secrets")
    if d.exists():
        assert sorted(p.name for p in d.iterdir()) == ["canary.txt"], f"unexpected files in {d}: {[p.name for p in d.iterdir()]}"
    for root in ("/etc/platform", "/opt/mark", "/workspace", "/root"):
        base = Path(root)
        if not base.exists():
            continue
        hits = [str(p) for p in base.glob("**/*.key")
                if p.is_file() and not any(w in str(p).replace("\\", "/") for w in _WHEEL_ROOTS)]
        assert not hits, f"private key material on the pod: {hits[:5]}"
    for name in ("MARK_SIGN_KEY", "MARK_MANIFEST_KEY"):
        assert not os.environ.get(name), f"{name} is set in the pod environment"


def test_no_per_model_artifact_sits_at_a_fixed_path():
    """**Anything specific to one model is named by that model, or it does not get written** (founder ruling
    2026-09-22, after four instances of the class in a single day: the engine log, the checksum list, the list's
    hash and the pinned-paths list).

    A per-model fact at a fixed path is overwritten by the second model on the pod. Two of these fail loudly when
    that happens -- a checksum list that does not match produces a wall of FAILED lines. The third does not:
    `model-hash.txt` becomes `pins.model.hash` in the SIGNED manifest, so a run could publish one model's
    fingerprint as the identity of another model's weights, and the record would look exactly like a correct one.

    `run.sh` refuses to start when one of these exists. This asserts the same thing from the other side, so a
    writer that was missed is caught by the suite rather than by a reader believing a manifest."""
    hf = WORK / "hf"
    if not hf.exists():
        pytest.skip("no model cache on this host")
    legacy = [p.name for p in hf.iterdir() if p.name in ("model-sha256.txt", "model-hash.txt", "pinned-paths.txt")]
    assert not legacy, (
        f"per-model artifact(s) at a fixed path in {hf}: {legacy}. Two models on one pod overwrite these, and "
        f"model-hash.txt reaches the signed manifest as pins.model.hash.")
    # and the named ones the served model needs are present, so this cannot pass by everything being absent
    named = sorted(p.name for p in hf.glob("*-model-sha256.txt"))
    assert named, f"no per-model checksum list in {hf}: the sweep above would pass on an empty directory"
