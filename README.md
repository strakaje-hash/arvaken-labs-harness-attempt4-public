# Arvaken Labs harness — attempt 4, as frozen

The measurement harness behind Arvaken Labs' attempt 4 measurements of AI agent stop controls. It holds the probes,
signed gates, mock world, runner, agent and control adapters, workloads, benchmark specification, model pins and pod
provisioning.

**Published with Paper 1.** This is the public copy of the harness, published with Arvaken Labs Paper 1, *Where the
stop happens* (DOI 10.5281/zenodo.23145196). It is the tree of the anchored commit `2505773` of the Lab's private
harness repository, with the changes `REMOVED-FILES.md` lists: four vendor web pages the Lab captured as evidence are
each replaced at its path by a short note, listed with their SHA-256 and the Wayback Machine's nearest captures, and
this README, `NOTICE` and `release_check.py` are updated for this copy. The anchored commit and its archive are
available on request. The run bundles and the freeze notes are in the paper's deposit.

## What this is

- **The exact tree that produced the attempt 4 results.** It is the Lab's private repository at `attempt4-freeze-4`
  (commit `af1675dca1b08454baf4b8a924a3a7d0f98a1cc8`), restricted to the harness. Nothing was changed except the
  workspace file and its lock, to leave out the product packages, and one AWS account id (see `REDACTIONS.md`); in this
  public copy, four vendor web pages are replaced by notes (see `REMOVED-FILES.md`). The
  frozen commit is named in the pre-registration's signature and in the tag's message, both anchored before the run.
  The commit of this repository was anchored on the day it was created.
- **Added:** `LICENSE` (Apache-2.0), `NOTICE`, this README, `REDACTIONS.md`, `REMOVED-FILES.md`, `FROZEN-BLOBS.txt` (the git blob id of every
  file as frozen), `release_check.py`, and the signature over the freeze-4 pre-registration. `scripts/guard_pass.py`, the
  pre-commit guard, is included as frozen.
- **Not here:** the Lab's product code, all private key material, the run records and bundles, and the freeze notes. The
  bundles and the freeze notes are in the paper's deposit (DOI 10.5281/zenodo.23145196), as the pre-registration says
  of the freeze notes.

## What the rows measure

On each agent target, every in-process control in the matrix ends in one stop flag inside the Lab's adapter, which then
calls the framework's own interrupt (LangGraph) or pause (OpenHands). The result rows measure the stop mechanism as the
Lab wired it into each framework. They are not independent evaluations of separate products; rows on one target agree
by construction and are read per mechanism, never as a ranking.

## Run it

```bash
uv sync --locked
uv run python release_check.py
```

`release_check.py` runs the whole suite and passes only if the failures are exactly the ones explained here, each
named in the script with its reason; any other failure, or an explained one passing, fails the check and is named.

**Thirteen fail on every host,** because each reads something this release deliberately leaves out:
- the product package: `test_sign_refuses_unportable.py` (its test vectors), `test_assertion_shapes.py`'s freeze test
  (known exceptions in product tests), and `test_version_pin_census.py` (a version pin in a product test);
- the Lab's records and papers: `test_records_tracked.py` and `test_paper0_register.py`;
- the freeze notes, published with the paper as the pre-registration says: three tests in `test_sealed_sections.py`;
- the Lab's freeze tags, which this repository's fresh history does not carry: four tests in
  `test_sign_preregistration.py`;
- the four vendor web pages, which this public copy replaces with notes: `test_registry.py`'s test that every cited
  quote is in its preserved document, byte for byte (`REMOVED-FILES.md`).

**Four fail on Linux hosts.** Attempt 4's full suite (1,867 passed, 2 skipped, at the frozen commit) was verified on the
development machine. On a Linux host, the system the runs happen on, four of the frozen instrument's own tests fail, in
the unrestricted frozen tree as well, for host-specific reasons named here, and none of them affects the measurements:
- `test_variants.py::test_both_variants_clear_the_precondition_for_a_cell` asserts the development machine's fallback
  clock; a Linux host has `CLOCK_MONOTONIC_RAW`, which is what a decisive run requires.
- `test_attribution_forwarding.py::test_a_resolver_claim_is_accepted_for_the_agent_and_refused_for_a_process_outside_it`
  starts its child in the test's own session, which the POSIX session rule reads as the agent's. In the runs every agent
  was launched in its own session, and the environment's attribution test passed on every machine before its run.
- `test_os_process_identity.py::test_a_real_spawn_is_seen_by_the_os_and_a_parents_own_calls_are_the_agents` asserts the
  agent in every child's parent chain; on a Linux host a child can be re-parented, and the runs placed such children by
  the session recorded at the agent's registration, or by the census, as their evidence records.
- `test_invariant_diagnosis.py::test_the_probe_filter_runs_only_the_named_probe_and_is_recorded_in_results_and_manifest`:
  on a Linux host the test's one-replication pace cells record no pace, so its `ks.resume` cells do not run. The cause
  is not yet diagnosed; in the runs the pace was measured on every machine and the `ks.resume` cells measured.

On Linux the check expects these four to fail; on any other host it requires them to pass.

The check includes a timing-sensitive test: run it on an otherwise idle machine.
`test_b1_propagation_shell_path.py::test_a_shell_that_spawns_through_markcall_is_measured_with_the_os_attributing`
needs the children it starts to keep acting after the halt; on a machine busy with other work they can finish first,
and the test then fails for that reason alone. On 2026-10-04 it failed once that way, and passed three times out of
three when run by itself.

**Run on 2026-09-24, from commit `c332bd7e50d18b10b6f8b4dab0b38c8956997984`:**
- **Linux:** a GPU pod (NVIDIA H100 80GB, driver 580.126.09, CUDA 13.0) from the pinned image
  `ghcr.io/strakaje-hash/mark-platform-runtime@sha256:66e6be2e0725b0187306bdd0f041d9deeeaacfa21e95dab016f31e481fd929e8`,
  set up by this tree's own `packages/platform/pod/setup.sh`, with the agent extras. `release check: PASSED -- exactly the
  16 explained failures` (752 passed, 3 skipped). Then one scripted smoke, `run.sh smoke ks.latency --target scripted
  --control agt-kill-switch --workload wl.sequence-payments -n 3`, with `Qwen/Qwen2.5-7B-Instruct-AWQ` served: 3 of 3
  replications measured, 0 ms of further action after the halt, every halt graceful (informational, as a smoke is).
- **Windows:** the development machine (ARM64), without the agent extras, which do not install there.
  `release check: PASSED -- exactly the 12 explained failures` (755 passed, 4 skipped).

**This public copy, run on 2026-10-04:**
- **Windows:** the development machine (ARM64), without the agent extras, with only light other work running.
  `release check: PASSED -- exactly the 13 explained failures, and every other test passed` (754 passed, 4 skipped).
  An earlier run that day, while the machine ran other heavy work, failed the timing-sensitive test above and
  nothing else.
- **Linux:** not run on this copy. The Linux run above is of the frozen tree before the four vendor pages were
  replaced; on Linux this copy expects seventeen failures, the thirteen and the four.

A matrix runs on a GPU pod: `docs/POD.md` is the runbook, `benchmarks/attempt4-agent-controls.yaml` the specification,
`benchmarks/attempt4-preregistration-freeze4.md` the pre-registration it was run under (with its signature beside it),
and `gates/` the signed gates. Definitions are in `docs/PROBES.md`; what changed from attempt 3 is in
`docs/attempt4-instrument-fixes.md`. Verification keys are in `packages/bundles/keys/` (public material only).

On Windows, clone with `git clone -c core.longpaths=true`.
