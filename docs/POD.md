# The pod: Probe Runtime on a single Runpod GPU pod (offline mode)

The runtime lives in `packages/platform`; the pod scripts in `packages/platform/pod`. The harness runbook in
`packages/harness/pod` is unchanged and separate (it has its own lockfile and pods).

## Image (Task 1.1)

`platform-runtime` = the pinned Runpod base (`runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`, Ubuntu 24.04,
Python 3.12, CUDA 12.8 userspace; verified 2026-09-10) plus the layering in `pod/setup.sh`, which is the same
step list as `pod/Dockerfile`. The authoring machine's Docker is linux/arm64 and the pod is x86_64, so in this
pass the layering runs on the pod at first boot rather than as a registry build; the manifest records the
image reference the pod reports (`/etc/platform/image-ref`) and the digest once a registry build exists.

What setup.sh installs: NOTHING of the tooling (attempt 4, 2026-09-21). bubblewrap, firejail, postgres, iptables,
Node + pnpm, uv, the OTel collector and Tempo are built into the image, each verified there against its publisher's
checksum or signature (pod/Dockerfile). setup.sh checks they are present and stops, naming the expected image, if one
is not: a pod that was not created from the pinned image is not the instrument. What it used to install: the OTel
Collector (`otelcol-contrib` 0.128.0) and Tempo 2.8.1 single binaries; the locked Python environment
(`uv sync --frozen` with the `agents`, `agt`, `openhands` extras); vLLM 0.29.0 in its own venv (it pins its own
torch); a non-root `runner` user; a canary secret at `/etc/platform/secrets/canary.txt` (root-only, 0600).

### Registry image

`.github/workflows/platform-image.yml` builds `pod/Dockerfile` for linux/amd64 on every change to the pod
directory and pushes `ghcr.io/strakaje-hash/mark-platform-runtime` (tags `<sha>` and `latest`).

**The digest lives in `packages/platform/pod/IMAGE` and is not repeated here.** This section carried a copy of
it until 2026-09-21, and that copy went stale the moment the image was rebuilt: it still named the 2026-09-11
digest, so a reader following these instructions would have created a pod from an image the repo no longer
pins -- which is not the instrument. `run.sh` reads that file and records it as `image_digest` in every
manifest. One owner, read by the script and by the reader alike.

The 2026-09-11 build (`sha256:51659a9f...`) was an OCI index whose only member was the linux/amd64 image
manifest `sha256:ab26835f...` (tag `pinned-6e57588`), which is why a manifest records `image_digest` (what the
pod was created from) and `image_platform_digest` (the image bits) separately. The 2026-09-21 rebuild exports a
single image manifest and no index -- one platform, `provenance: false`, `sbom: false` -- so for that image the
two are the same value.

### Provenance incident (2026-09-12)

The package was made public for the decisive run and inspected: config labels, environment, every layer
command and the one copied file were clean, but the BuildKit provenance attestation that buildx attaches by
default sat beside the image as a second manifest and embedded the whole GitHub push event: the founder's
email as author, committer, pusher and owner, the private repository's name, id and description, the commit
message and the Actions run. The package went private again within minutes; every attestation-bearing index
and attestation manifest was deleted from the registry; a clean index over the unchanged image manifest was
pushed and is the digest above; the workflow builds with `provenance: false` and `sbom: false`. The three
decisive-run pods had been created from the original index digest
`sha256:26d41a6b6209dcf07dbf285fe4c51972cb6702b507a3a604c49ed1923f528c3b`, which no longer resolves; their
manifests record it as `image_digest` and the durable `image_platform_digest` beside it, and the run notes say
so. Rule kept from this: the reproducibility anchor is the platform manifest digest, and nothing is published
beside an image that was not itself inspected.

Create the next pod from that reference (`image: <the line above>`), not from the Runpod tag, and set the pod
env `MARK_IMAGE_REF` to the same line: the manifest records `MARK_IMAGE_REF` (via `/etc/platform/image-ref`)
and nothing else, so a pod created from a tag honestly records the tag. The stock
GitHub runner died mid-build twice until the preinstalled toolchains were removed (the CUDA base is ~20 GB
unpacked); the workflow does that first. The image holds no repo code, model or key; it needs the package to
be public (or a Runpod registry credential) to be pulled by a pod.

## Why there is no network volume any more (2026-09-12)

The volume `ktujbo0z23` stalled under the three pods of decisive attempt 1 (every `ls /workspace` hung in a FUSE
wait and never returned), and afterwards it stopped a pod from starting at all: a pod created from the pinned
digest with the volume mounted never produced a container in twelve minutes, while an identical pod created from
the same digest with **no mount** was running and answering SSH immediately. Three candidate causes were ruled
out by that one control: the hand-pushed index is pullable, a digest reference to an index is fine, and the pull
was not slow (the image is built FROM the Runpod template, so 40 of its 41 layers are already on every host in
the region; only a 153-byte layer is ours).

The volume also held the only copy of the model pin, which made the weakest component the custodian of the
strongest claim. Both are fixed:

- `benchmarks/model-pins/<model>-<revision>.sha256` is the per-file SHA-256 list, in the repo. Its own sha256 is
  the `pins.model.hash` every manifest records (`4ecaf87f�` for the pinned Qwen snapshot).
- `pod/model.sh` provisions a pod's cache from the volume if it answers and otherwise **from Hugging Face at the
  pinned revision**, then refuses to continue unless every file matches the list and no unlisted file is present.
  Hugging Face is reachable from these pods (the 429 recorded below was another region, months earlier);
  measured 2026-09-12: 5.2 GB in about four minutes, verifying to the same hash the side-loaded copy produced.
- `sideload.sh` and `localize.sh` default to the pod's container disk (`DEST=/root`, `WORK=/opt/mark`), exports
  go to `RESULTS=/root/results`, and `fetch.sh` brings them to the laptop.

A reproducer therefore needs the image digest and one command, and nothing from us.

**Two models (attempt 3 freeze-2, 2026-09-14).** Everything on the pod that depends on the served model is derived from
`MARK_LLM_MODEL` by `pod/model-env.sh`: the Hugging Face cache path vLLM serves from, the snapshot the post-run
integrity check reads, and the pinned `vllm serve` arguments. Freeze-1 hardcoded Qwen's snapshot path, so vLLM would have
served Qwen whatever model a run named. A model with no pinned arguments is refused before vLLM starts. For the capable
arm, set `MARK_LLM_MODEL=gpt-oss-120b` (the served name, freeze-3; see below), `MODEL_REPO=openai/gpt-oss-120b` and
`MODEL_REV=b5c939de8f754692c1647ca79fbf85e8c1e70f8a`, and give the pod about 150 GB of container disk. `model.sh`
downloads exactly the files its pin list names (65.3 GB, not the repository's 195.8 GB) and verifies them. Qwen's
arguments are unchanged from freeze-1, and gpt-oss's are recorded in `model-env.sh` with the reasons: the `openai` parser,
memory utilization 0.95, and chunked prefill at 1,024 batched tokens.

**The capable arm is Qwen3-32B (freeze-4, 2026-09-14).** — **SUPERSEDED the next day by freeze-5 below, and kept as
the record of the choice before the fallback.** Read the settings from `pod/model-env.sh`, which owns the served name,
the revision and the pinned `vllm serve` arguments, and from C4 in `docs/attempt4-instrument-fixes.md`, which names the
arm. This entry no longer states them: it named the bf16 revision, and a reader following it would have served a
checkpoint the attempt does not use (corrected 2026-09-21; the same defect as a digest copied into prose beside the
file that owns it).

gpt-oss-120b was set aside: a serving-stack limitation, not a model judgment. vLLM 0.29.0's Harmony parser raised HTTP
500 on the model's own output in multi-turn agent conversations (benchmarks/runs/attempt-3-smokes/NOTES.md). The arm
needs about 150 GB of container disk.
- **Serving arguments:** the official bf16 checkpoint with the hermes parser, as the small arm; memory utilization 0.95.
- **Thinking off:** set server-side with `--default-chat-template-kwargs {"enable_thinking":false}`, pre-registered, so no
  target's request code changes. A thinking-on arm is a later, separate measurement.
- **Recording:** the serving record keeps the argument in the declared arguments, the process arguments and the engine
  log's non-default arguments.
- **Before measuring:** env-test now requires a scripted conversation of twenty model calls with tool results fed back, all
  HTTP 200 (`test_a_multi_turn_tool_conversation_of_twenty_calls_all_return_200`). One passing call is not enough.

**The capable arm is Qwen3-32B (FP8) (freeze-5, 2026-09-15).** The bf16 checkpoint did not start at 32,768 tokens: its
weights (61.03 GiB) left 7.91 GiB of KV cache at 0.95, against the 8.0 GiB one full request needs. Per the founder's rule
the arm uses the official `Qwen/Qwen3-32B-FP8` checkpoint, a precision change recorded as such.
- **Settings:** set `MARK_LLM_MODEL=Qwen/Qwen3-32B-FP8`, `MODEL_REPO=Qwen/Qwen3-32B-FP8` and
  `MODEL_REV=aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df`.
- **Serving arguments:** identical to bf16's: hermes, thinking off, memory utilization 0.95, 32,768 context,
  `max_num_seqs` 1024, prefix caching off, seed 7.

**The served name is not the repository (freeze-3, 2026-09-14).** gpt-oss-120b is served as `gpt-oss-120b`, from the
repository `openai/gpt-oss-120b`; `model-env.sh` maps each served name to its repository, and `model.sh` refuses to
provision a repository that is not the served name's. On freeze-2 it was served as `openai/gpt-oss-120b`. The OpenHands
adapter's model string was then `openai/openai/gpt-oss-120b`, the request reached vLLM as `gpt-oss-120b`, and every call
was a 404 ("The model `gpt-oss-120b` does not exist"). A diagnostic on the same pod, not a smoke, served the same snapshot
with the same arguments under both names, and the OpenHands env test passed: three model calls, all 200, two of them
tool calls. The manifest records the served name as the model id, and `pins.model.hash`, the pin list's own sha256,
names the repository and revision in `benchmarks/model-pins/`.

**Open item: the model string's second prefix strip is not characterized.** LiteLLM 1.100.1's own provider parsing
(`get_llm_provider`) turns `openai/openai/gpt-oss-120b` into `openai/gpt-oss-120b`, which is correct, yet the request
carried `gpt-oss-120b`. So a second `openai/` strip happens somewhere in the OpenHands SDK (1.47.0) to LiteLLM path, and
the line has not been found. It is not in the SDK's `LLM` input validator, which only resets `base_url` for the real
OpenAI API. The unprefixed served name avoids the strip; it does not explain it. Do not assume the adapter's model-string
handling is characterized: a served name that starts with a provider prefix may be rewritten before it reaches vLLM.

**Output budget and sampling on gpt-oss (checked before the capable-arm smoke).** gpt-oss reasons before it answers, and
the reasoning counts against the completion budget, so a small `max_tokens` would read as truncation (`model_error`) in
every replication.
- **Our settings:** neither target sets one, and `mark_platform.serving.REQUEST_PARAMS` carries temperature and seed only.
- **The OpenHands SDK's own default:** it sends `max_completion_tokens` from LiteLLM's model table, looked up by name. That
  table has no `gpt-oss-120b` or `openai/gpt-oss-120b` key, and the freeze-2 diagnostic's requests carried none, with
  `temperature` 0.0.
- **Before measuring:** the capable-arm smoke prints the options the SDK would send under the freeze-3 model string, and
  stops if a completion budget appears or `temperature` is dropped.

**Pull expectations.** The digest is self-contained: all 41 layers are in GHCR and a puller anywhere gets the
whole image, 10.9 GB compressed (largest layers 4.0, 3.0 and 2.1 GB). On a Runpod host it is fast anyway, because
the image is built FROM `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` and every host in the region already
has those layers cached; only a 153-byte layer is ours. Off Runpod, budget for a full 10.9 GB transfer.

**Provenance continuity.** The committed pin list hashes to `4ecaf87f44e8915d71e3fc74b1a003000a2c8ade41c03ea72cefc6ea5773bffe`,
which is the `pins.model.hash` in all five run manifests published so far (`first-20260911T205622Z`, the
rehearsal, and the three runs of decisive attempt 1), each of which also recorded `cache_integrity_after_run:
verified`. Those pins are therefore regenerable from source retroactively, and the model is verified by content:
where a pod got the weights (network volume or Hugging Face) is an operational detail the manifest records
separately, and the hash is what decides.

## Parallel pods, one target each (decisive runs, 2026-09-12)

A smoke is `run.sh smoke <probe> --target t --control c --workload w [-n N]` (added 2026-09-22; before that the
smokes had no invocation in this repo at all, see the freeze notes). It runs one cell with this file's environment,
takes the bench lock, names the served model in the run id and is never signed. The usage line in `run.sh` is the
owner of the argument list; this sentence does not repeat it.

Three pods, one target each, no shared storage. Per pod: `sideload.sh root@<ip> <port>` (repo bundle + scripts to
`/root`), `bash /root/localize.sh`, `WORK=/opt/mark bash /opt/mark/platform-setup.sh` (which runs `model.sh`), then
every `run.sh` call with `WORK=/opt/mark RESULTS=/root/results`. Pods never share a checkout, a Python
environment, a vLLM environment or a model cache. Run ids carry the target
(`oss-agent-controls-v1-langgraph-ref-<stamp>`), so `bench consistency` cites them together.

Pods are created from the pinned digest with `MARK_IMAGE_REF` set to that line, `PUBLIC_KEY` set to the laptop
key, `22/tcp` exposed, `allowedCudaVersions` 13.x (the digest's first-boot vLLM install is the PyPI cu130 wheel),
and the container command `/start.sh`: the pinned build's own command is `/bin/bash` (fixed in the Dockerfile
after that build; the next digest carries `/start.sh` itself).

## Storage (Task 1.2)

| path | volume | holds |
| --- | --- | --- |
| `/workspace/hf` | network volume | the pinned model snapshot + `model-sha256.txt` (per-file) + `model-hash.txt` (hash of the sorted list); root-writable, runner read-only |
| `/workspace/targets` | network volume | target checkouts at their registry SHAs (when a target is run from source) |
| `/workspace/mark` | network volume | the repo, cloned from the git bundle |
| `/root/runs/<run_id>` | container disk | harness spans, mock calls, one directory per scenario (agent spans, MCP spans, stdout/stderr, result), the ledger, results.json, manifest.json |
| `/workspace/results/<run_id>` | network volume | exported run bundles (never overwritten; export verifies the ledger first) |
| `/etc/platform/secrets` | container disk | the manifest signing key (bundle key) and the canary; root only; wiped by teardown |

## Keys: the pod never holds one (founder decision, session 2 review)

Earlier in the day `sideload.sh` shipped the bundle key to `/etc/platform/secrets/manifest.key` so the pod
could sign manifests and anchor to Rekor. That was the first credential a pod had ever held, and it broke the
cleanest property of the design: a compromised pod could have signed as the founder for the key's lifetime.
Now the pod closes a run with `manifest.unsigned.json` and the results hash, `run.sh export` verifies and
copies the bundle to the results volume, `pod/fetch.sh` brings it to the laptop, and `platform run sign`
signs it there with the run-manifest key, verifies, anchors the chain root in Rekor and renders the report.
The signed manifest carries `signed_on: laptop`. `MARK_SIGN_ON_POD=1` still allows pod-side signing for a
rehearsal, and such a manifest says `signed_on: pod` so a reader knows the key was online.

## Offline mode (Task 1.3)

The pod never holds a GitHub credential and cannot reach huggingface.co (its CDN answers Runpod egress with 429).
`pod/sideload.sh` ships, over scp: the repo as a git bundle, `setup.sh`, the bundle-signing key (to
`/etc/platform/secrets/manifest.key`, root only) and the model from the laptop's Hugging Face cache with a
per-file SHA-256 list that is re-checked on the pod (`sha256sum -c`). Model: `Qwen/Qwen2.5-7B-Instruct-AWQ`
(Apache-2.0) at revision `b25037543e9394b818fdfca67ab2a00ecc7dd641`, 5.6 GB. One scp stream from the laptop is
about 1 MB/s; split and parallelise for anything larger.

```bash
# laptop
FETCH=1 bash packages/platform/pod/sideload.sh root@<pod-ip> <ssh-port>   # first time: also downloads the model here
bash packages/platform/pod/sideload.sh root@<pod-ip> <ssh-port>           # later: code + key + model
MODELS=0 bash packages/platform/pod/sideload.sh root@<pod-ip> <ssh-port>  # code only
# pod
bash /workspace/platform-setup.sh
bash /workspace/mark/packages/platform/pod/run.sh services     # collector, tempo, vLLM (waits for /v1/models)
bash /workspace/mark/packages/platform/pod/run.sh env-test     # pytest tests/env
bash /workspace/mark/packages/platform/pod/run.sh first        # benchmarks/first-session.yaml, signed manifest
bash /workspace/mark/packages/platform/pod/teardown.sh         # export bundles, wipe /root/runs + secrets, check
```

SSH: the pod is created with `PUBLIC_KEY` set to the laptop's key (or patched in and restarted; the public port
changes on restart, re-read the pod). Long jobs: `nohup setsid`, the ssh session will drop.

## Kernel capabilities of the live pod (Task 1.4, measured 2026-09-11 on `t4x9i3ysc580ak`, Runpod EU-RO-1)

`pytest tests/env/test_isolation.py` records the result in `$MARK_RUNS/isolation-capabilities.json` — the run
directory, never the checkout, since 2026-09-22; it was written into the repo until then, which left the tree dirty
and would have blocked the matrix, because a bench run refuses a dirty checkout. The same probe
(`mark_platform.isolation.probe`) is what goes into every run manifest as `environment.isolation_capabilities`,
computed there rather than read from that file.

| check | result |
| --- | --- |
| `capsh --print` (Current) | cap_chown, cap_dac_override, cap_fowner, cap_fsetid, cap_kill, cap_setgid, cap_setuid, cap_setpcap, cap_net_bind_service, cap_net_raw, cap_sys_chroot, cap_mknod, cap_audit_write, cap_setfcap; **no cap_sys_admin, no cap_net_admin** |
| seccomp | mode 2 (filter), 1 filter; NoNewPrivs 0; CapEff `a80425fb` |
| `unshare -U` / `-n` / `-p -f` | all EPERM (`/proc/sys/user/max_user_namespaces` 2147483647, `unprivileged_userns_clone` 1: the seccomp filter, not the sysctl, is what blocks it) |
| `bwrap` (with or without `--unshare-user`) | fails: "No permissions to create new namespace" |
| `iptables -L` | "Permission denied (you must be root)" as root: no CAP_NET_ADMIN |
| `runuser -u runner` | works |
| interactive `bash` as root | `~/.bashrc` sources `/etc/rp_environment`, which re-exports the image's stock PATH: a tmux pane (OpenHands' terminal) does not see the venv's bin directory. `setup.sh` links `markcall` into `/usr/local/bin`. (H100 rehearsal, 2026-09-12) |
| container | `/.dockerenv`; cgroup `/docker/<id>` |
| `/workspace/hf` writable by `runner` | **yes**: the network volume is a FUSE mount that does not enforce POSIX modes (`chmod 755` has no effect). File modes cannot protect the model cache on this pod; every run re-verifies the served snapshot's per-file SHA-256 list at close and records `model_cache_integrity` (`verified` / `changed` → run failed / `not_checked`) in the manifest. `tests/env` records the fact (xfail), it does not pretend. |

Decision: **Tier B**. `runuser` as the `runner` uid, `prlimit` (256 processes, 4096 files, 16 GiB address space),
an ephemeral working directory, an allowlisted environment (no secrets), every service on localhost, and
HTTP(S)_PROXY to the harness's userspace allowlist proxy (`mark_platform.egress_proxy`; vLLM, mock world,
collector only; every denied attempt is logged into the run). Runs are labelled `egress_control=best_effort`
in the manifest, results.json and the report; the sentence in `docs/CONSTITUTION.md` accompanies them.

## Sandbox (Task 3.2), honestly

`pod/sandbox.sh --probe` reports which layers the pod can provide; `sandbox.sh -- <cmd>` runs the agent
process under the strongest one and writes the layer used to `MARK_SANDBOX_REPORT`, which the manifest
records. Layers: bubblewrap (user + pid namespaces, read-only system and checkout, tmpfs work dir, secrets
directory masked, model cache read-only, runs as `runner`); firejail; or plain `sudo -u runner`. A Runpod
container may not allow user namespaces or iptables; then the manifest says `sandbox: user` and the
external-reach test in `tests/env` is an xfail with the observed result, not a pass.

## tests/env

Run on the pod after `run.sh services`. Asserts: Python 3.12 + GPU visible; vLLM serves the pinned model and
answers; the collector accepts a span and writes it to the file store; postgres answers; `runner` cannot read
the canary or write into `/workspace/hf` and sees no secret-like environment; the sandbox layer is reported and
escape attempts (read the canary, write the model cache) fail; the toolkit kill switch calls back; the
LangGraph reference agent completes `wl.trivial` against vLLM with its trace propagated across the MCP
subprocess; OpenHands launches and can be paused; calibration is within 5 ms.
