"""The pre-launch check itself (founder ruling 2026-09-23): it names a directory the agents' user cannot read, and passes one it
can (R22). Pod only: the second user it checks against exists there."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not Path("/etc/platform").is_dir(), reason="pod only: the agents' user (runner) exists there")


def test_the_check_names_a_directory_the_agents_user_cannot_read_and_passes_one_it_can():
    from conftest import _unreadable_for

    base = Path(os.environ.get("MARK_RUNS") or "/root/runs") / "envtest-prelaunch"
    closed, inner = base / "closed", base / "closed" / "run"
    inner.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(closed, 0o700)                       # the shape of /tmp/pytest-of-root on 2026-09-23
        why = _unreadable_for("runner", inner)
        assert why is not None and f"{closed} is mode 700, owned by root" in why, why
        os.chmod(closed, 0o711)                       # the shape run.sh gives /root and $MARK_RUNS
        assert _unreadable_for("runner", inner) is None
    finally:
        os.chmod(closed, 0o755)
