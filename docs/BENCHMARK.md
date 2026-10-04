# Benchmark runs

## Definition

`benchmarks/<id>.yaml`: `replications`, `calibration_replications`, and a `matrix` of `{targets, controls,
probes:[{id, workload}]}` cells. Workloads are in `benchmarks/workloads.yaml` (versioned; the scripted
reference agent executes `script`, model-driven agents get `task`, both describe the same actions).

- `benchmarks/first-session.yaml`: the first pod run (this pass, section 9 step 5): calibration + `ks.latency`
  + `ks.completeness` × {scripted, langgraph-ref} × {none, agt-kill-switch, ref-*, langgraph-interrupt},
  3 replications. A pipeline proof, not the benchmark.
- `benchmarks/oss-agent-controls-v1.yaml`: the benchmark. 20 replications (the founder's floor), both workload
  variants, six kill-switch probes, three targets, the gateway as reference instrument, the resistance family
  disabled with its reason. **Budget**: 3,520 scenarios. At the first session's measured rates on the RTX 2000
  Ada (scripted ~8 s, LangGraph sequence ~85 s, batch ~25 s; OpenHands estimated at 1.5x LangGraph, not yet
  measured) that was about 53 hours of pod time. Founder decisions (session 2 review), now in the spec and
  the runner: reference rows (`ref-*`, the gateway) run at `reference_replications: 5` and are not graded,
  which removes most of the matrix; evaluated controls and `none` keep N=20; and the matrix splits by target
  for parallel pods on faster cards (`run.sh full --matrix oss-agent-controls-v1 --targets langgraph-ref`, one
  target per pod). Each pod produces its own run, ledger and manifest; the paper cites all of them.
  `--replications N` overrides the count for a rehearsal; a rehearsal reports informational.

## One matrix, several ledgers

Per-target pods are cited together only after `platform bench consistency runs/<a> runs/<b> runs/<c>` says so:
it compares image digest, model id and hash, gate hashes, probe spec hashes, workload hashes, engine version,
lockfile hash and repo commit across the manifests and lists every field that differs. `signed_on` is reported
but not compared.

Two report rules from the founder's review: a `mixed` ks.mechanism row (revocation in some replications,
control_message in others) is headlined under "Findings flagged by rule", because a control that is
non-deterministic in that property is a finding; and every ks.false_halt zero is printed with its rule-of-three
bound ("0 of 20; rate <= 15% at ~95%") so a zero is never read as never. A tighter claim needs a larger budget,
not a different threshold.

## Coverage the spec does not cover

`ks.propagation` is declared for `scripted` and `langgraph-ref` only: `wl.spawn-children` has no OpenHands task,
so **propagation is not measured on OpenHands** and the child-process finding rests on the other two targets. The
runner records `probes_not_declared_per_target` in the benchmark spec and the report prints a Coverage line, so a
reader never has to diff the matrix to find the gap. A shell-executing agent spawns a background process
trivially, which makes OpenHands the most natural propagation target; the task belongs in the next revision.

**Correction (2026-09-14, fixes A2, B3 and C1; the paragraph above is kept as written).** No `langgraph-ref`
`ks.propagation` replication was measured on attempt 2a or 2b: LangGraph has no spawn tool, its parent paid the `CHILD-`
references itself, and every row was `not_run` (the raw child counts came from the reference prefix, fix A2). The
child-process finding rests on `scripted` alone. Fix B3 gives OpenHands a spawn path (`markcall spawn`, `wl.spawn-children`
v2). The attempt 3 pod smoke decides whether its terminal keeps a background child alive after the command returns; if it
does not, propagation is not measurable on OpenHands either, and the coverage line says so. The attempt 3 spec leaves
`langgraph-ref` out of the propagation cell. The correction beside the bundles is
`benchmarks/runs/decisive-2-20260912/CORRECTIONS.md`.

## Key expiry

The `run-manifest` key certificate (`a151ce6f95f5793b`) is valid to **2026-12-10**. Every decisive run, every
re-signing for the paper, and any re-anchoring must happen before that date; after it, a fresh key is issued in a
root session and the bundle notes the key change. The gate key (`ee7ab65d74c67291`) runs to 2027-09-12.

## Signing happens on the laptop

Every public anchor made with a key of this project is accounted for in [ANCHORS.md](ANCHORS.md), including one
entry that corresponds to no published bundle (a signing-path rehearsal). An append-only log keeps what it is
given, so the account has to cover all of it.

The pod never holds a key. After `run.sh export <run>`: `bash packages/platform/pod/fetch.sh root@<ip> <port> <run>`,
then `uv run platform run sign --run-dir runs/<run> --key packages/bundles/keys/bundle-<id>.key --cert
packages/bundles/keys/certs/<id>.json --anchor rekor` with the run-manifest key. `run sign` refuses to sign if
the ledger does not verify or the results hash differs from the unsigned manifest, records `signed_on: laptop`,
anchors the chain root in Rekor with the same key, and renders `report.md`.

## One command (Task 6.2, this pass)

```bash
uv run platform bench run benchmarks/first-session.yaml --run-dir /root/runs/<run_id> --sign-key /etc/platform/secrets/manifest.key --cert packages/bundles/keys/certs/<active>.json
uv run platform run export --run-dir /root/runs/<run_id> --dest /workspace/results
uv run mark-ledger verify /workspace/results/<run_id>/ledger --pretty
uv run mark-ledger verify-manifest /workspace/results/<run_id>/manifest.json --root packages/bundles/keys/root.pub --ledger /workspace/results/<run_id>/ledger
```

`platform bench report <run_dir>` renders `report.md`: one table per control class (in_process /
out_of_process, never merged), with primitive, value, halt classes, pre-halt-delayed count, replications,
verdict and not_run reasons, the egress-control label, the clock source, gate status and the ceiling/scope
and best-effort sentences from the single source. `platform bench reproduce --mode replay|live` (6.3), the
publication bundle (6.4) and the right-of-reply slots are not built in this pass; the report carries an
empty right-of-reply line so the omission is visible.

## Replications, the serving condition and replay (founder rulings 2026-09-12)

These rules come from the proxy-captured reproduction of decisive attempt 2's unparsed-first-reply class
(`benchmarks/runs/decisive-2-20260912/HISTORY.md`). The replays ran on vLLM 0.29.0 on an H100 80GB.

**Temperature 0 and a seed pin nothing without the serving condition.**
- **Within a condition:** the stack reproduced itself. For 10 captured prompts in 3 serving conditions, 5 repeated sends
  gave one token path each (30 of 30).
- **Across conditions:** it did not.
  - `max_num_seqs` alone, at concurrency 1, changed the token path for 3 of 10 prompts. The two servers had captured
    different CUDA-graph sizes.
  - Batch composition on one server changed it for 2 of 10.
- **The pin:** the manifest pins the effective condition, read from the engine's own startup log. It covers
  `max_num_seqs` and where that value came from, prefix caching, chunked prefill and `max_num_batched_tokens`,
  eager mode, the CUDA-graph mode and capture sizes, the seed and the engine version. It also pins the concurrency in
  effect, sampled from `/metrics`.
- **Not stated:** a value the server did not state is recorded as not stated. `max_num_seqs` is not stated when it is
  left at its default, so `pod/run.sh` passes it explicitly.

**Live regeneration holds only under an identical serving condition.** A regeneration under a different condition is
a different measurement. On the reproduction, not pinning the condition cost:
- 3 of 10 prompts whose path changed with `max_num_seqs`;
- 2 of 10 whose path changed with batch composition.

**Replay is a measured rate, per run.**
- **Why:** in the reproduction the run's own server reproduced 6 of 10 captured first replies, so the condition held
  something history-dependent. Prefix-cache contents are the prime candidate.
- **Prefix caching:** measurement runs serve with it off (`--no-enable-prefix-caching`).
- **The check:** every run ends with `platform run replay-fidelity`, on the host that ran it, before the pod is
  released.
  - Every scenario's captured first request is re-sent byte-identical, one at a time, and the reply's path hash is
    compared with the captured reply's.
  - The rate, the mismatches, the concurrency in effect and whether the server's condition is the run's are recorded
    in the bundle before signing.
- **What a rate licenses, pre-registered in the benchmark spec** (`replay:` in `benchmarks/*.yaml`, recorded in
  results.json, never set in code):
  - `replayable_at: 0.99`: the paper's word "replayable" needs the rate at or above this. At 95%, a reader who re-sends
    a captured body gets the run's reply nineteen times in twenty, which is not what the word means;
  - all but one also permits the word, but only when that one mismatch is classified benign, and then only once it
    is explained;
  - `poor_below: 0.95`: an operational trigger, not a claim. A single flip on twenty scenarios is exactly 95.0%, and a
    server restart for one flip would be wasteful;
  - between the two, the bundle states the measured rate and does not use the word;
  - a run whose spec pre-registers neither states its rate only.
- **No mismatch is assumed benign.** Each is classified by where the two replies diverge and whether the divergence
  reaches an effect, and the replayed body is kept beside the captured one. The classes, in order of consequence:
  - `parse_state`: parsed in one reply, unparsed in the other;
  - `turn_structure`: a different number of calls, a finish call in only one, or a different finish reason, which
    decides whether the agent takes another turn (the Q3 token-756 divergence);
  - `action`: a tool name or an effect-bearing argument differs;
  - `description_only`: benign; only arguments the tool never executes differ, declared per tool (so far only
    OpenHands `terminal.summary`: the Q3 token-29 divergence);
  - `content_only`: benign; only free text differs.

  An argument not declared non-executing is effect-bearing.
- **The classification rules are versioned and hashed** (founder ruling 2026-09-12). The list of arguments declared
  never executed decides whether a divergence is benign, so it gets the same protection as the canonicalization rules.
  - **What is versioned:** `replay_fidelity.CLASSIFICATION_RULES` holds the version, the class order, the benign
    classes, that list and the executed-by-default rule.
  - **Where it is recorded:** the rules' hash goes into every replay record.
  - **The pin:** a test pins the hash, and the benchmark spec pre-registers `classification_rules_version`.
  - **On a mismatch:** if the code's version differs from the spec's, no mismatch counts as benign. The list can grow
    only with a version bump, a new pinned hash and a spec change, never quietly.
- **A hypothesis, not a rule.** Leakage of request history lowers the rate sharply, while numerical jitter lowers it
  slightly. This is to be tested against the fidelity data, not relied on.
- **The eager arm, only when the run's own server is poor under the run's own condition.**
  - **How:** `pod/run.sh` restarts vLLM with `--enforce-eager`, logging to its own file, and `platform run
    replay-order` replays the captured first requests in three orders (as captured, reversed, and shuffled with a
    recorded seed) and compares the replays with each other. Three orders agreeing is strong evidence of order
    independence, not proof.
  - **What it can answer:** an eager server cannot measure fidelity to a run served with CUDA graphs, because its code
    paths differ. It answers the question underneath: does output stop depending on request history when graphs are
    off?
  - **If eager is order-independent:** the next measurement run is served eager, and its own fidelity check shows
    whether replayability follows.
  - **If not:** the history dependence lives somewhere else.
- **Not yet shown on a pod:** the startup-log parser and the `/metrics` sampler were tested against log lines copied
  from the reproduction pod only.
  - **The gauge name:** the sampler records every `vllm:num_requests*` gauge the server emits, and a response without
    `vllm:num_requests_running` counts as absent, never as zero. The smoke run confirms the name vLLM 0.29.0 emits, and
    the notes record the name found before any concurrency figure is trusted.
  - **The flag:** the env-test decides whether `--no-enable-prefix-caching` is accepted. The first pod run is their proof. Until then the serving condition in a manifest is
  as parsed, and a parse gap found on that run goes into its notes.
- **Earlier bundles:** the decisive attempt-2 bundles on model-driven targets were served with prefix caching on,
  and their condition was not recorded. They are reproducible within the stated tolerance only, not replayable
  (ANCHORS.md).

**What a replication is on a model-driven target.**
- **The finding:** the per-replication scenario id is in the agent's prompt, as the working path in the tool
  description. Swapping only that string between two captured first requests flipped the model's reply both ways, 3
  of 3, in every cell tried.
- **The ruling:** the variation is kept and declared, not pinned away. Pinning the path would make twenty replications
  one sample twenty times, one agent behaviour rather than the distribution a deployer faces.
- **In practice:**
  - Every workload declares `variation: scenario_id`.
  - Every replication records its first request's prompt hash and its first reply's path hash.
  - Every cell reports the number of distinct first-reply paths among its measured replications, and flags a cell whose
    replications all share one path as effectively one sample of agent behaviour.
  - N counts replications. On model-driven targets `min_replications` is read against distinct paths, informationally:
    the signed gates count replications, and a gate v3 may change that.
  - An unparsed reply stays `not_run: model_error` per replication: the slip is a model property at a prompt, not a
    control result.
- **For the paper, three separate facts, each with its number:**
  - a replication is one agent behaviour, sampled by varying the scenario id under a pinned serving condition;
  - N counts replications, with distinct paths reported beside it;
  - the missing-quote slip is a prompt-dependent model property with a position-dependent trigger, and an
    all-or-nothing tool-call parser discards the whole reply.

## What a run writes

`/root/runs/<run_id>/`: `spans.harness.jsonl`, `mock-calls.jsonl`, `scenarios/<scenario_id>/` (workload,
agent spans, MCP spans, stdout/stderr, `agent-result.json`, the throwaway `work/` dir), `ledger/` (objects,
chains, anchors), `results.json`, `manifest.unsigned.json`, `manifest.json` (signed with the bundle key under
the root; purpose `cloak-bundle` in this pass, see docs/KEYS.md).

Ledger chain per run: `run_open` (environment, repo commit, lockfile hash) → `calibration` → one
`probe_result` per cell (payload = the full result object; each replication's raw evidence is a separate
content-addressed object referenced by hash) → `run_close` (results.json hash) → a `local-only` anchor of the
chain root. Export never overwrites; anchoring with Rekor or an RFC 3161 TSA is `mark-ledger anchor` once the
bundle is on a networked host.

## Pins in the manifest

image reference/digest, `uv.lock` SHA-256, engine version, repo commit, model id + hash (SHA-256 over the sorted
per-file SHA-256 list), every registry target's SHA or pypi pins, gate hashes, probe spec hashes, workload
hashes, and the environment (GPU name/driver, sandbox layer).
