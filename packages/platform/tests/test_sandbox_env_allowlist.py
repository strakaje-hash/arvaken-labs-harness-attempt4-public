"""The sandbox passes the agent an allowlist of variables (pod/sandbox.sh, `env -i`). A variable scenario.py sets for
the agent but the allowlist omits is dropped on the pod and nowhere else: fix A1's MARK_AWAIT_RESUME_S was, so the
wait for a held resume never reached a sandboxed agent. Found while adding MARK_CHILDREN_FILE (fix B3, 2026-09-14)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# read by sandbox.sh itself before it clears the environment, not by the agent
SANDBOX_OWN = {"MARK_SANDBOX_REPORT"}


def _keep() -> set[str]:
    m = re.search(r'^KEEP="([^"]*)"$', (ROOT / "pod" / "sandbox.sh").read_text(encoding="utf-8"), re.M)
    assert m, "sandbox.sh has no KEEP line"
    return set(m.group(1).split())


def _set_for_agent() -> set[str]:
    src = (ROOT / "mark_platform" / "scenario.py").read_text(encoding="utf-8")
    return set(re.findall(r'"(MARK_[A-Z0-9_]+)":', src)) | set(re.findall(r'\benv\["(MARK_[A-Z0-9_]+)"\]', src))


def test_every_variable_the_scenario_hands_the_agent_passes_the_sandbox():
    handed = _set_for_agent()
    # the scope is counted, so a parse that finds nothing cannot pass: these are set by the dict literal and by assignment
    assert {"MARK_MOCK_URL", "MARK_TURN_FILE", "MARK_CHILDREN_FILE", "MARK_MOCK_TOKEN", "MARK_AWAIT_RESUME_S"} <= handed, handed
    missing = sorted(handed - SANDBOX_OWN - _keep())
    assert not missing, f"set for the agent in scenario.py but dropped by sandbox.sh: {missing}"
