"""Every environment test that runs anything as the agents' user runs it where agents work (founder ruling 2026-09-23).

Twice in one day a pod test worked somewhere no real agent works. The attribution test put its run under pytest's tmp_path
(/tmp/pytest-of-root, mode 700): the agent could not read its own workload and exited before its first call. The sandbox-tier
test had always worked in a tmp_path folder and passed only because Popen(cwd=...) changes directory as root before runuser
switches user. Both were found on three pods at $10 an hour. This finds the next one on the laptop, where the tests cannot run
(no second user, no sandbox -- R23), by reading them:

  * a test or fixture in tests/env that launches anything as the agents' user takes the `agents_workdir` fixture -- a fresh
    folder under $MARK_RUNS, given to that user and checked readable by it before anything is launched;
  * no launch in tests/env is handed a working folder under /tmp or pytest's tmp_path.

"Launches as the agents' user" is read from the code, not from a list of test names: the sandbox command (`SANDBOX`, a
`sandbox.sh` path), `runuser`, `sudo ... runner`, the resolver (`start_if_needed`), the capability probe (`probe`), `open_run`
with a sandbox command that is not empty, and any helper in the same file that does one of those. Root launches (open_run with
`sandbox_cmd=[]`) are outside it. conftest.py is the check itself and is not read.
"""
from __future__ import annotations

import ast
from pathlib import Path

ENV = Path(__file__).resolve().parents[3] / "tests" / "env"
DIRECT_CALLS = {"start_if_needed", "probe"}
FIXTURE = "agents_workdir"


def _is_fixture(fn: ast.FunctionDef) -> bool:
    return any("fixture" in ast.unparse(d) for d in fn.decorator_list)


def _launches(node: ast.AST, launchers: set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == "SANDBOX":
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and ("sandbox.sh" in n.value or n.value == "runuser"):
            return True
        if isinstance(n, ast.List) and [getattr(e, "value", None) for e in n.elts[:3]] == ["sudo", "-u", "runner"]:
            return True
        if isinstance(n, ast.Call):
            name = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else None)
            if name in DIRECT_CALLS or name in launchers:
                return True
            if name == "open_run":
                for kw in n.keywords:
                    if kw.arg == "sandbox_cmd" and not (isinstance(kw.value, ast.List) and not kw.value.elts):
                        return True
    return False


def _bad_workdirs(node: ast.AST) -> list[str]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            targets = [kw.value for kw in n.keywords if kw.arg == "cwd"]
            fname = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if fname == "chdir" and n.args:
                targets.append(n.args[0])
            for v in targets:
                text = ast.unparse(v)
                if "tmp_path" in text or (isinstance(v, ast.Constant) and str(v.value).startswith("/tmp")):
                    out.append(f"line {n.lineno}: a working folder of {text}")
    return out


def violations(source: str, filename: str) -> list[str]:
    tree = ast.parse(source)
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    # helpers that launch become launchers themselves, until nothing new is found
    launchers: set[str] = set()
    while True:
        found = {f.name for f in fns if f.name.startswith("_") and _launches(f, launchers)} - launchers
        if not found:
            break
        launchers |= found
    out = []
    for f in fns:
        if not (f.name.startswith("test_") or _is_fixture(f)):
            continue
        params = {a.arg for a in f.args.args}
        if _launches(f, launchers) and FIXTURE not in params:
            out.append(f"{filename}::{f.name} launches as the agents' user without the {FIXTURE} fixture")
        out += [f"{filename}::{f.name}: {b}" for b in _bad_workdirs(f)]
    return out


def launching(source: str) -> set[str]:
    tree = ast.parse(source)
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    launchers = {f.name for f in fns if f.name.startswith("_") and _launches(f, set())}
    return {f.name for f in fns if (f.name.startswith("test_") or _is_fixture(f)) and _launches(f, launchers)}


def test_every_env_test_that_launches_as_the_agents_user_works_where_agents_work():
    files = sorted(p for p in ENV.glob("*.py") if p.name != "conftest.py")
    assert files, f"no environment tests found under {ENV}: the walk below would assert nothing"
    found = {name for p in files for name in launching(p.read_text(encoding="utf-8"))}
    # the positive control (R3): the eight places the 2026-09-23 audit found by hand are all seen by the reading
    expected = {"test_no_host_or_credential_variable_reaches_the_agent_through_the_sandbox", "test_runner_user_exists_and_cannot_read_secrets_or_write_models",
                "test_sandbox_tier_is_reported_and_escape_attempts_fail", "caps",
                "test_the_run_decides_attribution_from_the_kernel_and_the_resolver_runs_as_the_agents_user",
                "test_a_sandboxed_processs_socket_is_attributed_to_it_and_root_alone_cannot",
                "test_through_the_runner_children_are_survivors_and_a_killed_resolver_is_not_run",
                "test_a_child_that_detaches_after_its_parent_exits_is_attributed_or_could_not_tell_never_unrelated"}
    assert expected <= found, f"the reading no longer sees: {sorted(expected - found)}"
    bad = [v for p in files for v in violations(p.read_text(encoding="utf-8"), p.name)]
    assert bad == [], "environment tests that run as the agents' user somewhere no agent works:\n  " + "\n  ".join(bad)


def test_the_two_shapes_found_on_the_pods_are_refused_and_the_shape_that_replaced_them_is_not():
    """R10: the old things, refused. R22: the new thing and a root launch, let through."""
    attribution_before = '''
def test_holder(tmp_path, agents_user_can_read):
    agents_user_can_read("/tmp")
    held = subprocess.Popen([*SANDBOX, sys.executable, "-c", code], cwd="/tmp")
'''
    sandbox_before = '''
def test_tier(tmp_path):
    work = tmp_path / "work"
    subprocess.run(["bash", str(REPO / "packages" / "platform" / "pod" / "sandbox.sh"), "--", "id"], cwd=str(work))
'''
    helper_launch = '''
def _as_runner(cmd, cwd):
    return subprocess.run(["sudo", "-u", "runner", "-E", *cmd], cwd=str(cwd))

def test_secret(tmp_path):
    _as_runner(["cat", "/x"], tmp_path)
'''
    after = '''
def test_holder(agents_workdir):
    held = subprocess.Popen([*SANDBOX, sys.executable, "-c", code], cwd=str(agents_workdir))
'''
    root_only = '''
def test_calibration(tmp_path):
    ctx = open_run(tmp_path / "run", "cal", llm_url=U, llm_model=M, tools_mode="inproc", sandbox_cmd=[])
'''
    v = violations(attribution_before, "a.py")
    assert any("without the agents_workdir fixture" in x for x in v) and any("a working folder of '/tmp'" in x for x in v), v
    v = violations(sandbox_before, "b.py")
    assert any("without the agents_workdir fixture" in x for x in v), v
    v = violations(helper_launch, "c.py")
    assert any("test_secret launches as the agents' user" in x for x in v), v
    assert violations(after, "d.py") == []
    assert violations(root_only, "e.py") == []
