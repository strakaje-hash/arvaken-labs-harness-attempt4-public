"""The release check: run the whole suite and confirm that exactly the failures this release has explained occur, and
nothing else.

    uv sync --locked
    uv run python release_check.py

The principle (founder ruling 2026-09-24): the check passes only if the failures are exactly the ones explained here.
This tree is the harness restricted from a larger repository, and thirteen tests read something the restriction
deliberately leaves out; they fail everywhere, each for the reason given below. Four more of the frozen instrument's own
tests fail on a Linux host, in the unrestricted frozen tree as well, for host-specific reasons; on Linux they are
expected to fail, and on any other host they must pass. The check records every test's outcome by its node id
(collection errors included) and exits 0 only on an exact match: any other failure, or an expected failure that passes,
exits 1 and is named. Extra arguments are passed to pytest and make a partial run, which is reported but never called a
pass.
"""
from __future__ import annotations

import sys

import pytest

_NOT_HERE = "which this release leaves out"
EXPECTED = {
    # the product package
    "packages/ledger/tests/test_sign_refuses_unportable.py":
        f"reads its test vectors from the product package, {_NOT_HERE}",
    "packages/platform/tests/test_assertion_shapes.py::test_the_freeze_cannot_grow_and_every_entry_in_it_is_still_real":
        f"lists known exceptions in product tests, {_NOT_HERE}",
    "packages/platform/tests/test_version_pin_census.py::test_every_version_pin_in_the_tree_is_registered_with_its_artifact_and_its_reason":
        f"registers a version pin in a product test, {_NOT_HERE}",
    # the Lab's records and papers
    "packages/probes/tests/test_records_tracked.py::test_every_record_the_constitution_names_exists_is_tracked_and_is_not_under_an_ignore_rule":
        "checks the Lab's records, which are published with the paper",
    "packages/probes/tests/test_paper0_register.py":
        f"loads Paper 0's register renderer, {_NOT_HERE}",
    # the freeze notes (founder ruling: published with the paper, as the pre-registration says)
    "packages/platform/tests/test_sealed_sections.py::test_a_one_byte_change_inside_a_sealed_section_changes_its_fingerprint":
        "reads the freeze notes, which are published with the paper",
    "packages/platform/tests/test_sealed_sections.py::test_every_cited_fingerprint_is_the_sections_own[PHASE 1 CAPABLE-ARM RUNS DECLARED VOID, recorded 2026-09-23 00:05 UTC, BEFORE two of them closed-29cebaf3754ab6843b09cb212d5d05479d84f76c5b51339b2214281c1aa02c6f]":
        "reads the freeze notes, which are published with the paper",
    "packages/platform/tests/test_sealed_sections.py::test_every_cited_fingerprint_is_the_sections_own[Sealed readings of the freeze-4 record (moved out 2026-09-23, founder ruling)-c1818eb4b3de146bbbe57c4eefe4f830d1b2fc19c3349b90264d53cf1429cf25]":
        "reads the freeze notes, which are published with the paper",
    # the Lab's freeze tags (this repository has a fresh history)
    "packages/platform/tests/test_sign_preregistration.py::test_it_reads_the_tags_committed_bytes_and_the_anchored_hash_is_what_they_are":
        "needs the Lab's freeze tags, which this repository's fresh history does not carry",
    "packages/platform/tests/test_sign_preregistration.py::test_the_commit_comes_from_the_tag_so_the_two_pins_agree":
        "needs the Lab's freeze tags, which this repository's fresh history does not carry",
    "packages/platform/tests/test_sign_preregistration.py::test_a_path_missing_at_the_tag_is_refused_by_name":
        "needs the Lab's freeze tags, which this repository's fresh history does not carry",
    "packages/platform/tests/test_sign_preregistration.py::test_the_tagged_spec_carries_the_tagged_record":
        "needs the Lab's freeze tags, which this repository's fresh history does not carry",
    # the vendors' web pages, which this public copy does not redistribute (REMOVED-FILES.md)
    "packages/platform/tests/test_registry.py::test_every_cited_quote_is_in_its_preserved_document_and_every_document_is_byte_exact":
        "reads the four vendor web pages byte for byte, which this public copy replaces with notes (REMOVED-FILES.md)",
}
_LINUX = "fails on a Linux host in the unrestricted frozen tree too (verified 2026-09-24); it does not affect the measurements (README.md)"
EXPECTED_ON_LINUX = {
    "packages/platform/tests/test_variants.py::test_both_variants_clear_the_precondition_for_a_cell":
        f"asserts the development machine's fallback clock, which a Linux host does not use; {_LINUX}",
    "packages/platform/tests/test_attribution_forwarding.py::test_a_resolver_claim_is_accepted_for_the_agent_and_refused_for_a_process_outside_it":
        f"its child shares the test's own session, which the POSIX session rule reads as the agent's; {_LINUX}",
    "packages/platform/tests/test_os_process_identity.py::test_a_real_spawn_is_seen_by_the_os_and_a_parents_own_calls_are_the_agents":
        f"asserts the agent in every child's parent chain, which a Linux host breaks when a child is re-parented; {_LINUX}",
    "packages/platform/tests/test_invariant_diagnosis.py::test_the_probe_filter_runs_only_the_named_probe_and_is_recorded_in_results_and_manifest":
        f"its one-replication pace cells record no pace on a Linux host, so its ks.resume cells do not run (cause not yet diagnosed); {_LINUX}",
}


class Recorder:
    def __init__(self) -> None:
        self.failed: set[str] = set()
        self.passed: set[str] = set()
        self.skipped: set[str] = set()
        self.collection_errors: set[str] = set()

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.add(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.add(report.nodeid)
        elif report.skipped:
            self.skipped.add(report.nodeid)
        elif report.when == "call" and report.passed:
            self.passed.add(report.nodeid)


def main(argv: list[str]) -> int:
    linux = sys.platform.startswith("linux")
    expected = dict(EXPECTED, **(EXPECTED_ON_LINUX if linux else {}))
    print(f"release check: host {sys.platform}; {len(expected)} failures expected "
          f"({len(EXPECTED)} everywhere{', plus 4 on Linux' if linux else '; the 4 Linux-only ones must pass here'})")
    rec = Recorder()
    code = pytest.main(["-q", "-p", "no:cacheprovider", "--continue-on-collection-errors", *argv], plugins=[rec])
    failed = rec.failed | rec.collection_errors
    passed = rec.passed - failed
    unexpected = sorted(failed - set(expected))
    expected_failed = sorted(failed & set(expected))
    expected_not_failed = sorted(set(expected) - failed)
    print()
    print(f"release check: {len(passed)} passed, {len(rec.skipped)} skipped, {len(failed)} failed or not collected "
          f"(pytest exit {int(code)})")
    for n in expected_failed:
        print(f"  expected, failed: {n} -- {expected[n]}")
    for n in unexpected:
        print(f"  UNEXPECTED FAILURE: {n}")
    for n in expected_not_failed:
        print(f"  EXPECTED TO FAIL, DID NOT: {n}")
    for n in sorted(rec.skipped):
        print(f"  skipped: {n}")
    if argv:
        print("release check: a partial run (arguments were given); only a full run can pass")
        return 1
    if unexpected or expected_not_failed:
        print(f"release check: FAILED -- the failures are not exactly the {len(expected)} this release explains")
        return 1
    print(f"release check: PASSED -- exactly the {len(expected)} explained failures, and every other test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
