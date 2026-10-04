"""R20 as a check: an assertion that can be skipped without anyone noticing (attempt 4, 2026-09-21).

A pipeline test guarded its assertion with `if ev is not None:` around a path that did not exist. The assertion never
ran, the tally said "4 passed", and two real defects sat behind it -- a probe field the runner silently overwrote and a
sealed row that could not reproduce its own number. TESTING.md R20 states the rule; this module is the rule that does
not depend on anyone reading it at the convenient moment.

**Three shapes, each one that has actually bitten**, over `test_*.py` only:

1. `SKIPPED_BY_EXISTENCE` -- an `assert` reachable only under an existence or None guard: `if p.exists():`,
   `if x is not None:` / `if x:` where `x` was assigned in the same function from a conditional expression or from a
   call to `.exists()` / `.get(...)`. The case itself.
2. `SWALLOWED` -- an `assert` inside a `try:` whose handler body is only `pass` or `continue`. The same defect with an
   exception instead of a guard.
3. `ONLY_IN_A_LOOP` -- a test whose assertions are ALL inside a `for`/`while`, with nothing asserting how many times it
   ran. A loop over an empty list asserts nothing; `assert checked >= len(CELLS)` is the fix, written by hand in
   `test_row_reproduction_and_reserved_keys.py` before this check existed.

**The escape hatch is an exception, not a suppression.** A `# r20: <reason>` comment on the guard, the `try`, or the
loop allows it and records why, the way the version-pin registry records a pin. A bare `# r20` with no reason does not
count -- an exception nobody had to justify is a suppression.

A check that fires everywhere gets suppressed, and a suppressed check is a silent skip in a new costume; so each rule
is narrow on purpose, and `test_assertion_shapes.py` asserts both that each shape IS flagged and that ordinary
conditionals are NOT.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[3]
SKIP = ("/.venv/", "/node_modules/", "/__pycache__/", "/.git/", "/benchmarks/runs/", "/tests/fixtures/")
SKIPPED_BY_EXISTENCE = "assertion reachable only under an existence/None guard"
SWALLOWED = "assertion inside a try whose handler swallows it"
ONLY_IN_A_LOOP = "every assertion is inside a loop, and nothing asserts the loop ran"
EXEMPT = re.compile(r"#\s*r20:\s*(\S.*)$", re.I)
EXISTENCE_CALLS = ("exists", "is_file", "is_dir", "get")

# DEBT, frozen 2026-09-21, keyed by (file, test, shape) so an entry survives edits to its file. These are real
# instances that predate the check, in suites needing Postgres or a pod (packages/record, packages/product,
# tests/env) which cannot be run from the laptop. **This list may not grow**: a new finding anywhere fails
# `unbaselined()`. It is debt with a date, not an exemption with a reason -- the `# r20: <reason>` comment is the
# exemption, and none of these has one, because none of them is justified. Most are security properties that pass
# on an empty collection, which is the worst form of the defect: "no role holds UPDATE" is green on a schema that
# never migrated. Burn-down spawned the same day.
#
# PRIORITY (founder ruling 2026-09-21): the security-property tests do not age on this list. "They're the guarantees
# the customer letter and the boundary statement rest on, and each is currently a promise the test can't distinguish
# from an empty catalogue. Fix them on the next platform-with-Postgres run, before anything else on that list: assert
# the collection is non-empty and name its expected size from the catalogue, then assert the property over it."
# 7 qualify by that description rather than four -- named in PRIORITY below, so the classification is visible
# and correctable rather than a count someone has to take on trust.
KNOWN: set[tuple[str, str, str]] = {
    ("packages/product/tests/test_aws_calls.py", "test_every_action_the_manifest_names_passes_the_gate", ONLY_IN_A_LOOP),   # passes on an empty manifest
    ("packages/product/tests/test_aws_manifest.py", "test_every_entry_on_our_list_is_one_aws_actually_calls_read_only", ONLY_IN_A_LOOP),   # passes on an empty list
    ("packages/product/tests/test_aws_preflight.py", "test_every_canary_would_be_refused_if_it_were_granted", ONLY_IN_A_LOOP),   # a security property: passes when there are no canaries
    ("packages/product/tests/test_classes_and_membership.py", "test_a_new_class_takes_its_floor", ONLY_IN_A_LOOP),   # passes when no class is new
    ("packages/product/tests/test_membership_window.py", "test_the_product_source_holds_no_window_number", ONLY_IN_A_LOOP),   # passes when the source scan finds no files
    ("packages/product/tests/test_package_runner.py", "test_the_artifacts_in_the_bucket_could_never_have_installed", ONLY_IN_A_LOOP),   # a security property: passes on an empty bucket listing
    ("packages/record/tests/test_append_only.py", "test_no_service_role_holds_update_delete_truncate", ONLY_IN_A_LOOP),   # a security property: passes on an empty catalogue, so 'no role holds UPDATE' would be green on a schema that never migrated
    ("packages/record/tests/test_append_only.py", "test_every_table_has_the_blocking_triggers", ONLY_IN_A_LOOP),   # same: passes when the catalogue is empty
    ("packages/record/tests/test_append_only.py", "test_every_mutation_attempt_fails_for_every_service_role", ONLY_IN_A_LOOP),   # same: passes when there are no roles or no tables
    ("packages/record/tests/test_append_only.py", "test_every_star_view_outside_the_current_convention_shows_every_column", ONLY_IN_A_LOOP),   # passes when no view is outside the convention, which is also the pass condition
    ("packages/record/tests/test_instrument_boundary.py", "test_each_product_table_is_written_by_exactly_its_account", ONLY_IN_A_LOOP),   # passes on an empty table list
    ("packages/record/tests/test_instrument_boundary.py", "test_backup_role_is_read_only", ONLY_IN_A_LOOP),   # a security property: passes when the table list is empty
    ("packages/record/tests/test_sentry_checkin.py", "test_the_timer_and_the_monitor_describe_the_same_schedule", ONLY_IN_A_LOOP),   # passes when no unit is found
    ("packages/record/tests/test_sentry_checkin.py", "test_the_check_in_margin_is_not_shorter_than_the_timers_own_jitter", ONLY_IN_A_LOOP),   # passes when no unit is found
    ("packages/record/tests/test_sentry_checkin.py", "test_a_value_with_a_space_is_quoted_so_systemd_does_not_truncate_it", ONLY_IN_A_LOOP),   # passes when no unit is found
    ("packages/record/tests/test_sentry_checkin.py", "test_no_unit_line_starts_with_a_dash", ONLY_IN_A_LOOP),   # passes when no unit is found
    ("packages/record/tests/test_sentry_checkin.py", "test_every_unit_that_checks_in_names_the_file_that_carries_the_dsn", ONLY_IN_A_LOOP),   # passes when no unit is found
    ("tests/env/test_pod_env.py", "test_the_pod_holds_no_signing_key", SKIPPED_BY_EXISTENCE),   # the secrets directory guard: off-pod the assertion is skipped, in a test whose docstring says 'asserted rather than assumed'
}


class Finding:
    __slots__ = ("file", "line", "func", "rule", "detail")

    def __init__(self, file: str, line: int, func: str, rule: str, detail: str) -> None:
        self.file, self.line, self.func, self.rule, self.detail = file, line, func, rule, detail

    @property
    def key(self) -> tuple[str, str, str]:
        """What a baseline entry is keyed by: the file, the test and the shape -- never the line, which moves."""
        return (self.file, self.func, self.rule)

    def __repr__(self) -> str:
        return f"{self.file}:{self.line}: {self.func}: {self.rule} -- {self.detail}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Finding) and self.key == other.key


def _asserts(node: ast.AST) -> Iterator[ast.AST]:
    """Assertions: `assert`, and `with pytest.raises(...)`, which is one."""
    for n in ast.walk(node):
        if isinstance(n, ast.Assert):
            yield n
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                c = item.context_expr
                if isinstance(c, ast.Call) and "raises" in ast.dump(c.func):
                    yield n


def _exempt(lines: list[str], line: int) -> str | None:
    """A `# r20: <reason>` on the statement's own line. The reason is required."""
    if 1 <= line <= len(lines):
        m = EXEMPT.search(lines[line - 1])
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def _nullable_names(fn: ast.AST) -> dict[str, str]:
    """Names assigned from something that can be absent: `x = a if c else None`, `x = p.exists()`, `x = d.get(k)`."""
    out: dict[str, str] = {}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Assign) or len(n.targets) != 1 or not isinstance(n.targets[0], ast.Name):
            continue
        name, v = n.targets[0].id, n.value
        if isinstance(v, ast.IfExp):
            out[name] = "assigned from a conditional expression"
        elif isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in EXISTENCE_CALLS:
            out[name] = f"assigned from .{v.func.attr}()"
    return out


def _provably_runs(loop: ast.AST) -> bool:
    """A `for` over a literal with at least one element, or over `range(<positive constant>)`, always runs -- so its
    assertions are not skippable and flagging it would be noise. A check that fires everywhere gets suppressed, and a
    suppressed check is the silent skip it exists to catch."""
    if not isinstance(loop, ast.For):
        return False   # `while` is never provably entered
    it = loop.iter
    if isinstance(it, (ast.List, ast.Tuple, ast.Set)) and it.elts:
        return True
    if isinstance(it, ast.Dict) and it.keys:
        return True
    if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
        args = [a for a in it.args if isinstance(a, ast.Constant) and isinstance(a.value, int)]
        if len(args) == len(it.args) and args:
            lo, hi = (0, args[0].value) if len(args) == 1 else (args[0].value, args[1].value)
            return hi > lo
    # a literal the loop unpacks (`for a, b in [(1, 2), ...]`) is the list case above; anything else is unknown
    return False


def _guard_is_existence(test: ast.expr, nullable: dict[str, str]) -> str | None:
    """`if p.exists():`, `if x is not None:` or `if x:` where x is nullable. Not `if n > 3:` or `if flag:`."""
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) and test.func.attr in EXISTENCE_CALLS:
        return f"guarded by .{test.func.attr}()"
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot)):
        if isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None:
            left = test.left
            if isinstance(left, ast.Name) and left.id in nullable:
                return f"guarded by `{left.id} is{'' if isinstance(test.ops[0], ast.Is) else ' not'} None`, {nullable[left.id]}"
    if isinstance(test, ast.Name) and test.id in nullable:
        return f"guarded by a bare `{test.id}`, {nullable[test.id]}"
    return None


def scan_file(path: str | Path) -> list[Finding]:
    p = Path(path)
    try:
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    lines = src.splitlines()
    rel = p.name if p.is_absolute() and not str(p).startswith(str(REPO)) else str(p.relative_to(REPO)).replace("\\", "/") if str(p).startswith(str(REPO)) else str(p)
    out: list[Finding] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not fn.name.startswith("test_"):
            continue
        nullable = _nullable_names(fn)
        guarded: list[ast.AST] = []
        for node in ast.walk(fn):
            # 1. an assertion reachable only under an existence/None guard
            if isinstance(node, ast.If):
                why = _guard_is_existence(node.test, nullable)
                inner = [a for a in _asserts(ast.Module(body=node.body, type_ignores=[]))]
                if why and inner and not _exempt(lines, node.lineno):
                    out.append(Finding(rel, inner[0].lineno, fn.name, SKIPPED_BY_EXISTENCE, f"{why}; if it is absent the assertion is skipped and the test still passes"))
                guarded.extend(inner)
            # 2. an assertion inside a try whose handler swallows it
            if isinstance(node, ast.Try):
                swallows = any(all(isinstance(s, (ast.Pass, ast.Continue)) for s in h.body) for h in node.handlers)
                inner = [a for a in _asserts(ast.Module(body=node.body, type_ignores=[]))]
                if swallows and inner and not _exempt(lines, node.lineno):
                    out.append(Finding(rel, inner[0].lineno, fn.name, SWALLOWED, "the handler is `pass`/`continue`, so a failing assertion is caught and the test passes"))
        # 3. every assertion inside a loop that might not run, with nothing asserting it did
        all_asserts = list(_asserts(fn))
        loops = [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.While))]
        maybe_empty = [lp for lp in loops if not _provably_runs(lp)]
        if all_asserts and maybe_empty:
            in_loop = {id(a) for lp in maybe_empty for a in _asserts(lp)}
            if len(in_loop) == len(all_asserts) and not any(_exempt(lines, lp.lineno) for lp in maybe_empty):
                out.append(Finding(rel, all_asserts[0].lineno, fn.name, ONLY_IN_A_LOOP,
                                   "a loop over an empty sequence asserts nothing; assert how many times it ran, or assert something outside the loop"))
    return sorted(out, key=lambda f: (f.file, f.line, f.rule))


# The security properties among the frozen findings: each currently passes on an empty collection, which is the worst
# form of the defect. These leave the freeze first, on the next run that has Postgres and a pod.
PRIORITY: set[str] = {
    "test_no_service_role_holds_update_delete_truncate",           # record: "no role holds UPDATE" is green on an empty catalogue
    "test_every_mutation_attempt_fails_for_every_service_role",    # record: green when there are no roles or no tables
    "test_every_table_has_the_blocking_triggers",                  # record: green when the catalogue is empty
    "test_backup_role_is_read_only",                               # record: green when the table list is empty
    "test_every_canary_would_be_refused_if_it_were_granted",       # product: green when there are no canaries
    "test_the_artifacts_in_the_bucket_could_never_have_installed",  # product: green on an empty bucket listing
    "test_the_pod_holds_no_signing_key",                           # tests/env: skips itself when the secrets directory is absent
}


def unbaselined(root: str | Path = REPO) -> list[Finding]:
    """Findings outside the dated freeze below. A new one fails; the freeze cannot grow."""
    return [f for f in scan(root) if f.key not in KNOWN]


def python_test_files(root: str | Path = REPO) -> list[Path]:
    root = Path(root)
    return sorted(p for p in root.rglob("test_*.py") if not any(s in f"/{p.relative_to(root).as_posix()}" for s in SKIP))


def scan(root: str | Path = REPO) -> list[Finding]:
    return [f for p in python_test_files(root) for f in scan_file(p)]


def exemptions(root: str | Path = REPO) -> list[tuple[str, int, str]]:
    """Every `# r20: <reason>` in the tree, so the exceptions are as readable as the findings."""
    out = []
    for p in python_test_files(root):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            m = EXEMPT.search(line)
            if m and m.group(1).strip():
                out.append((str(p.relative_to(Path(root))).replace("\\", "/"), i, m.group(1).strip()))
    return out
