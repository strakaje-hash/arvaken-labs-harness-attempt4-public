"""The guard pass's own guard (founder ruling 2026-09-22).

A hand-kept list of checks goes stale, and a guard pass that silently stops covering a check is worse than no
guard pass, because it buys false confidence. These assertions make drift fail loudly and in one direction only:
the list can be wrong by running too little, and that is what is caught here.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "guard_pass.py"


@pytest.fixture(scope="module")
def gp():
    assert SCRIPT.exists(), "the guard pass must live in the tree"
    spec = importlib.util.spec_from_file_location("guard_pass", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_listed_check_exists(gp):
    assert gp.GUARDS, "an empty list would make every assertion below vacuous"
    missing = [p for p, _ in gp.GUARDS if not (REPO / p).exists()]
    assert not missing, f"the guard pass lists files that do not exist: {missing}"


def test_the_count_cannot_quietly_fall(gp):
    """**The drift direction that matters.** Removing a check makes the pass faster and greener, which is exactly
    why nobody notices. The floor turns that into a failure. Raising the floor is a deliberate act; lowering it
    without moving a check somewhere else is the thing this forbids."""
    assert len(gp.GUARDS) >= gp.FLOOR, (
        f"the guard pass covers {len(gp.GUARDS)} checks, below its floor of {gp.FLOOR}. A check was removed: put it "
        f"back, or lower the floor deliberately and say why.")


def test_every_check_says_what_it_catches(gp):
    """A list of paths ages into a list nobody can audit. The reason is what lets a reader tell whether the pass
    still covers what it claims."""
    for path, why in gp.GUARDS:
        assert why and len(why) > 20, f"{path} has no usable reason: {why!r}"
    assert len({p for p, _ in gp.GUARDS}) == len(gp.GUARDS), "a file listed twice inflates the count against the floor"


def test_it_covers_the_checks_that_actually_fired(gp):
    """Not a general principle -- the specific checks that caught real defects on 2026-09-22, each of which sat
    inside a forty-minute suite. If one leaves this list, the reruns come back."""
    listed = {p for p, _ in gp.GUARDS}
    for must in ("packages/platform/tests/test_assertion_shapes.py",      # R20, caught the clockwatch loop
                 "packages/platform/tests/test_version_pin_census.py",    # R19, caught four stale pins
                 "packages/platform/tests/test_stall_check.py",           # caught baselines the checker could not read
                 "packages/probes/tests/test_records_tracked.py"):        # caught the untracked pre-registration
        assert must in listed, f"{must} caught a real defect and must stay in the guard pass"


def test_the_budget_is_stated_and_small(gp):
    """`fast` as a fact rather than a claim: a guard slow enough to be skipped in practice is not a guard, so the
    pass fails when it outgrows its budget instead of quietly becoming another forty-minute wait."""
    assert 0 < gp.BUDGET_S <= 600, gp.BUDGET_S


def test_the_hook_refuses_rather_than_warns(gp):
    """A hook that prints and exits zero is decoration. This one execs the pass, so its exit code is the commit's."""
    assert "exec" in gp.HOOK and "scripts/guard_pass.py" in gp.HOOK
    assert "--no-verify" not in gp.HOOK, "the script must not offer itself a bypass; that is the founder's call to make"
    # **and it must not assume `python` is the repository's interpreter.** The first commit through this hook was
    # refused because a bare `python` was the system one with no pytest: a hook that refuses for a defect in itself
    # teaches people to bypass it, which is the one way a refusal becomes worse than no check at all.
    assert ".venv/Scripts/python.exe" in gp.HOOK and ".venv/bin/python" in gp.HOOK
    assert "\nexec python scripts/guard_pass.py" not in gp.HOOK, "a bare `python` must not be the first choice"


def test_the_pass_runs_the_repositorys_interpreter(gp):
    """`sys.executable` is whatever launched the script, which under the hook is the shell's `python`. The pass
    resolves the venv itself so it behaves the same whether run by hand or by the hook."""
    chosen = gp._python()
    assert chosen.endswith("python.exe") or chosen.endswith("python"), chosen
    # R20: not `if the venv exists`, which would delete the assertion on a machine without one and still pass.
    # The repository HAS a venv, so requiring it is unconditional and the absence is the failure.
    assert (gp.REPO / ".venv").exists(), "this repository has a venv; the assertion below is only meaningful with it"
    assert ".venv" in chosen, f"a repository with a venv must use it, not {chosen}"
