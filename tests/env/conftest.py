"""Before any test in tests/env runs, and so before any failure message can print anything: the test process must
hold no host identifier (RUNPOD_*) and no credential-shaped variable (incident 2026-09-12). A parent shell's state
surviving into this process is the defect; the check names the variables and never prints a value.

Pod only (/etc/platform exists): on the laptop the rule is tested by packages/platform/tests/test_env_scrub.py."""
import os
from pathlib import Path

import pytest


def pytest_sessionstart(session):
    if not Path("/etc/platform").is_dir():
        return
    from mark_platform.envscrub import forbidden_names

    bad = forbidden_names(os.environ)
    if bad:
        pytest.exit(f"tests/env refuses to start: the test process inherited host or credential-shaped variables (names only): {bad}", returncode=3)


def _unreadable_for(user: str, path: Path) -> str | None:
    """None when `user` can traverse every directory from / down to `path` and read `path`; otherwise the sentence naming the
    first place it cannot, with that place's mode and owner as root sees them."""
    import pwd
    import subprocess

    path = Path(path).resolve()
    chain = [str(p) for p in reversed(path.parents)] + [str(path)]
    script = 'for d in "$@"; do test -x "$d" || { echo "$d"; exit 1; }; done; test -r "${@: -1}" || { echo "${@: -1}"; exit 1; }'
    r = subprocess.run(["runuser", "-u", user, "--", "bash", "-c", script, "check", *chain], capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        return None
    where = Path((r.stdout.strip() or str(path)).splitlines()[-1])
    try:
        st = where.stat()
        mode, owner = oct(st.st_mode & 0o777)[2:], pwd.getpwuid(st.st_uid).pw_name
    except OSError as e:
        mode, owner = "?", f"unreadable ({e.strerror})"
    return (f"the agents' user ({user}) cannot read {path}: {where} is mode {mode}, owned by {owner}. A sandboxed agent launched "
            f"there fails before its first call (agent exit 1, zero calls) -- put the run under $MARK_RUNS, which run.sh makes traversable")


@pytest.fixture
def agents_user_can_read():
    """Before an environment test launches an agent as the agents' user, it calls this on the directory the agent will work in,
    and the test fails with the sentence if that user cannot read it (founder ruling 2026-09-23). The first pod run of the
    attribution test put its run under pytest's tmp_path (/tmp/pytest-of-root, mode 700): the agent could not read its own
    workload, and the failure read "agent exit 1, zero calls" until its stderr was read."""
    def check(path, user: str = "runner") -> None:
        why = _unreadable_for(user, Path(path))
        if why:
            pytest.fail(why, pytrace=False)
    return check


@pytest.fixture
def agents_workdir(request, agents_user_can_read):
    """The working folder for anything an environment test runs as the agents' user: a fresh folder under $MARK_RUNS -- where
    every agent works -- given to that user and checked readable by it before anything is launched (founder ruling 2026-09-23).
    Twice in one day a test worked somewhere no agent works (pytest's tmp_path, /tmp) and passed or failed for reasons a real
    run never meets; tests/test_env_tests_run_where_agents_run.py (on the laptop) refuses a test that launches as the agents'
    user without this fixture, or that hands such a launch a working folder under /tmp."""
    import re
    import shutil
    import time

    name = re.sub(r"[^a-z0-9]+", "-", request.node.name.lower()).strip("-")[:60]
    d = Path(os.environ.get("MARK_RUNS") or "/root/runs") / f"envtest-{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    d.mkdir(parents=True)
    shutil.chown(d, "runner", "runner")
    agents_user_can_read(d)
    return d
