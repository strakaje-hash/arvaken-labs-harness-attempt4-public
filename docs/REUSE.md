# What the platform reuses from `mark` (read before building)

Written at the start of the Layer 1 platform pass (2026-09-11) after reading every package. The platform
extends this monorepo; the components below are proven by their tests and are the foundation, not a starting
point to rewrite. Where the build instructions assumed something that does not exist, that is said here.

## Reusable components and their tests

| Component | Where | What it proves | Tests |
| --- | --- | --- | --- |
| **Ed25519 key hierarchy** | `packages/core/src/bundle.ts` | root → purpose-bound key certificate (validity window, revocation list with staleness policy, sticky revocation) → signed object. `canonicalJson` (sorted keys, `undefined` dropped) is the byte form under every signature; `keyId` = first 16 hex of SHA-256(pubkey). `Signer` is a function so a KMS/hardware token plugs in (`packages/bundles/src/signer.ts`: `file:` and `cmd:` specs). Fail-closed verification with typed reasons. | `packages/core/test/bundle.test.ts` (12 cases: forged list, other root, mismatched key id, tampered manifest, expired/not-yet-valid, sticky revocation, signer-output check) |
| **Key material and CLI** | `packages/bundles/keys`, `packages/bundles/scripts/*.ts` | `ceremony-root`, `issue-key --days`, `revoke`, `refresh-revocations`, `sign`, `verify`. Dev root is on this laptop (`keys/root.key`, gitignored); `docs/KEYS.md` records the ceremony and the two trust semantics. | `packages/bundles/test/registry.test.ts` (every cert on disk issued by the pinned root; every bundle verifies) |
| **Ceiling / scope single source + drift test** | `packages/core/src/ceiling.ts` ↔ `docs/CEILING.md` | One TS constant renders everywhere; the doc must contain every sentence verbatim. This is the doc-drift pattern the platform copies for its constitution and ceiling. | `packages/core/test/ceiling.test.ts` |
| **Gate pattern (pre-registered, signed, informational until signed)** | `packages/harness/mark_harness/report.py` (`GATE`, `FINETUNE_GATE`, `finetune_verdict`) + `packages/harness/PREREGISTRATION.md` | Thresholds + preconditions + `status: signed` in code, the reasons in a dated record; a failed precondition makes `verdict = informational` with `outcome_if_decisive` shown; supersession = new dated section + new value in one commit. `not_run` with a reason is a first-class result; `None` (never `0.0`) for an undefined ratio. | exercised by the harness CLI runs (`packages/harness/runs/*`); no unit test of its own — the platform's `Gate` gets one |
| **Content hashing / manifests** | `packages/harness/mark_harness/corpus.py` (`freeze`, `_sha256`, `corpus_fingerprint`, `consent_pointer`) | SHA-256 of file BYTES (not decoded text), fingerprint over sorted hashes, pointer-not-record custody. | `validate-corpus` path exercised by `pod/ship-corpus.sh` |
| **Pod runbook, offline mode** | `packages/harness/pod/{setup,sideload,ship-corpus,run,teardown}.sh`, `pod/README.md` | Repo ships as a git bundle (no credential on the pod); HF cache side-loaded to the network volume, `HF_HUB_OFFLINE=1`; `/root/...` container disk for anything that must not outlive the pod; teardown deletes and then CHECKS nothing sensitive remains (exit 1 if it does); runs committed on `harness/<run>` branches. Ops facts: SSH via `PUBLIC_KEY` env, port changes on restart, ~0.9 MB/s per scp stream (split + parallel), `nohup setsid` for long jobs. | verified on three pods 2026-09-10 (memory `ops_runpod_pod_access`) |
| **Storage adapter** | `apps/api/src/db.ts` (`Db` interface, sqlite via `node:sqlite` + Postgres via `pg`, `?`→`$n` rewrite) | One SQL dialect, two drivers, TEXT/INTEGER/REAL-only DDL with a privacy test that fails on any binary column. | `apps/api/test/{api,postgres,privacy}.test.ts` (Postgres test fails unless a server is present: no false green) |
| **Efficacy table shape** | `packages/core/src/bundle.ts` `EfficacyTable` + `packages/harness/scripts/apply-to-manifest.mjs` | A measured table lives INSIDE the signed object; `measured=false` on synthetic data regardless of numbers. | `registry.test.ts` "unmeasured table carries the honesty note" |

## What the build instructions assumed that is not here

- **"The provenance repo's hash-chained store"** does not exist in this monorepo. There is no `prev_hash`/chain
  anywhere. `packages/ledger` is built fresh in this pass, on the hashing and signing primitives above
  (`canonicalJson`, SHA-256, the key hierarchy), with a TS↔Python canonical-JSON conformance test so the TS verifier
  and the Python ledger agree byte for byte.
- **`Gate` as a class** does not exist; the pattern exists (dict + verdict function). The platform makes it a typed,
  signed object.
- **`pod/teardown.sh` is harness-specific** (`/root/consented`). The platform gets its own `pod/` directory under
  `packages/platform/pod/` with the same shape (sideload → setup → run → teardown that checks), and the harness
  scripts stay untouched.
- **Docker on the authoring machine is linux/aarch64**; the pod is x86_64 + CUDA. The `platform-runtime` image is
  therefore a pinned Runpod base image (by digest) plus an idempotent, pinned layering script run on the pod, with a
  `Dockerfile` that is the same steps for a registry build later. The manifest records the base digest and every lock hash.

## Rules carried over verbatim (from the reviewer's record on the harness)

- A measurement that did not happen is `not_run` with a reason; `0.0` never stands in for "didn't happen".
- A verdict is `informational` until the gate is signed AND every precondition passed; the record shows what the
  numbers would have said.
- Numbers only count when the denominator exists (`None` for 0/0).
- A check must be scoped to the seam it closes; a green test proves the check, not the property.
