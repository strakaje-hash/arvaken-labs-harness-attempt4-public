"""Fix A6 (attempt 3 fixes v1.1, 2026-09-14): the laptop-side pod watcher names the pod it polled, never one captured at
launch. On attempt 2b a watcher started with one pod's id kept naming it after that pod was gone. A stand-in `ssh` reports
the pod's own id (as PID 1's RUNPOD_POD_ID would); the laptop's environment carries a stale one."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WATCH = Path(__file__).resolve().parents[1] / "pod" / "pod-watch.sh"
BASH = shutil.which("bash")


def _fake_ssh(tmp_path, pod_id, state):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_bytes(f"#!/usr/bin/env bash\necho {pod_id}\necho {state}\necho 'cell 3 of 40'\n".encode())
    ssh.chmod(0o755)
    return bindir


def _watch(tmp_path, bindir, **env):
    full = {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""), "HOST": "203.0.113.7", "PORT": "22022",
            "RUNPOD_POD_ID": "stale-launch-pod", "POLL_S": "0", **env}
    return subprocess.run([BASH, str(WATCH)], capture_output=True, text=True, env=full, timeout=60)


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_the_billing_bound_names_the_pod_it_polled(tmp_path):
    r = _watch(tmp_path, _fake_ssh(tmp_path, "live-polled-pod", "CHAIN_RUNNING"), BOUND_S="0")
    assert r.returncode == 3, r
    bound = [line for line in r.stdout.splitlines() if "BILLING BOUND" in line]
    assert len(bound) == 1 and "on pod live-polled-pod " in bound[0], r.stdout
    assert "stale-launch-pod" not in r.stdout and "pod live-polled-pod running: cell 3 of 40" in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_a_finished_chain_names_the_pod_it_polled(tmp_path):
    r = _watch(tmp_path, _fake_ssh(tmp_path, "live-polled-pod", "CHAIN_GONE"), BOUND_S="600")
    assert r.returncode == 0 and "pod live-polled-pod: chain process gone" in r.stdout and "stale-launch-pod" not in r.stdout, r
