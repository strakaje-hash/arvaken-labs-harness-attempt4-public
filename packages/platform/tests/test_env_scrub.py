"""Incident 2026-09-12 (benchmarks/runs/decisive-2-20260912/HISTORY.md): a shell that had sourced Runpod's injected
RUNPOD_API_KEY started the chain, the key survived into the env test's process, and a failure message printed its
value into a log. Same class as the grep exit code and the self-matching watcher, from the other side: a parent's
state survived into a child. The instrument must never hold what it could leak: no RUNPOD_* and no credential-shaped
variable reaches an agent process or the pod test process, whatever their parent held, and checks name, never print."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from mark_platform import envscrub
from mark_platform.runner import calibrate, close_run, open_run, run_cell

REPO = Path(__file__).resolve().parents[3]
PLANTED = {"RUNPOD_API_KEY": "rpa_planted_value", "RUNPOD_POD_ID": "pod-planted", "GH_TOKEN": "planted-gh", "AWS_SECRET_ACCESS_KEY": "planted-aws",
           "HF_TOKEN": "planted-hf", "SOME_PASSWORD": "planted-pw", "OPENAI_API_KEY": "planted-openai"}


def test_forbidden_names_are_host_identifiers_and_credential_shapes_and_only_names():
    env = {**PLANTED, "PATH": "/bin", "HOME": "/root", "MARK_MOCK_URL": "http://127.0.0.1:1"}
    assert envscrub.forbidden_names(env) == sorted(PLANTED)
    assert set(envscrub.scrubbed(env)) == {"PATH", "HOME", "MARK_MOCK_URL"}
    assert envscrub.forbidden_names(env, allow={"GH_TOKEN"}) == sorted(set(PLANTED) - {"GH_TOKEN"})
    assert not any("planted" in n for n in envscrub.forbidden_names(env))


def test_no_host_or_credential_variable_reaches_an_agent_process_whatever_the_harness_inherited(tmp_path, monkeypatch):
    for k, v in PLANTED.items():
        monkeypatch.setenv(k, v)
    launched: list[dict[str, str]] = []
    real = subprocess.Popen

    def spy(cmd, *a, **kw):
        if any("mark_platform.agentproc" in str(c) for c in cmd):
            launched.append(dict(kw.get("env") or {}))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    ctx = open_run(tmp_path / "run", "envscrub", llm_url="http://127.0.0.1:9/v1", llm_model="none", tools_mode="inproc", sandbox_cmd=[])
    try:
        calibrate(ctx, replications=1, expected_ms=250.0, tolerance_ms=25.0)
        run_cell(ctx, "ks.completeness", "scripted", "none", "wl.batch-payments", 1)
        close_run(ctx, benchmark=None, sign_key_path=None, cert_path=None)
    finally:
        ctx.mock.stop()
    assert launched, "no agent process was launched, so nothing was checked"
    for env in launched:
        # MARK_MOCK_TOKEN is the one credential the harness issues itself (an in-process control's agent presents it)
        assert envscrub.forbidden_names(env, allow={"MARK_MOCK_TOKEN"}) == []


def test_the_pod_env_suite_refuses_to_start_on_an_inherited_credential_and_names_it_without_its_value(monkeypatch):
    spec = importlib.util.spec_from_file_location("pod_env_conftest", REPO / "tests" / "env" / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class OnPod:
        def __init__(self, *_):
            pass

        def is_dir(self):
            return True

    monkeypatch.setattr(mod, "Path", OnPod)
    monkeypatch.setenv("RUNPOD_API_KEY", "rpa_planted_value")
    with pytest.raises(pytest.exit.Exception) as e:
        mod.pytest_sessionstart(None)
    assert "RUNPOD_API_KEY" in str(e.value) and "rpa_planted_value" not in str(e.value)
