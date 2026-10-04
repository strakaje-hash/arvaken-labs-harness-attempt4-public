# Model pins

The per-file SHA-256 list of each pinned model snapshot, and the aggregate hash every run manifest records as
`pins.model.hash` (sha256 of the list file, byte-exact, `sha256sum -c` format with `*` binary markers).

| model | revision | files | aggregate hash |
| --- | --- | --- | --- |
| `Qwen/Qwen2.5-7B-Instruct-AWQ` (Apache-2.0) | `b25037543e9394b818fdfca67ab2a00ecc7dd641` | 12 | `4ecaf87f44e8915d71e3fc74b1a003000a2c8ade41c03ea72cefc6ea5773bffe` |
| `openai/gpt-oss-120b` (Apache-2.0) | `b5c939de8f754692c1647ca79fbf85e8c1e70f8a` | 26 | `5ef719e2c4577006fb47d863538bdf8d89be52125f16f98c7d7e61898a4c8f4b` |
| `Qwen/Qwen3-32B` (Apache-2.0) | `9216db5781bf21249d130ec9da846c4624c16137` | 27 | `ad7814b066feb4abe1493b5771752bf2f0394093090888ada31ff66b311276db` |
| `Qwen/Qwen3-32B-FP8` (Apache-2.0) | `aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df` | 17 | `07fb1e291d67e78131ad319927fbb45b25acff4bd64655cad74a8b0b31de450d` |

**gpt-oss-120b (attempt 3 freeze-2, 2026-09-14).** The list names the 26 files vLLM serves (65.3 GB); the repository's
`metal/` and `original/` copies of the weights are not pinned and not downloaded. It was built from Hugging Face's
metadata at the pinned revision, without downloading the weights. The large files' SHA-256 values are the LFS object
ids, and the 11 small files were downloaded and hashed. The pin is proven the first time the weights land: `pod/model.sh`
downloads exactly these files and refuses on any mismatch or any extra file.

**gpt-oss-120b was set aside for the capable arm (founder ruling 2026-09-14).** It was set aside for a serving-stack
limitation, not a model judgment: vLLM 0.29.0's Harmony parser raised HTTP 500 on the model's own output in multi-turn
agent conversations (benchmarks/runs/attempt-3-smokes/NOTES.md). Its pin stays, so a later arm can revisit it on a pinned
vLLM version and cite this record.

**Qwen3-32B (attempt 3 freeze-4, 2026-09-14), the capable arm's model.** The list pins the official bf16 checkpoint
(27 files, 65.54 GB), built the same way as gpt-oss-120b's, from Hugging Face metadata. The large files' SHA-256 values are
the LFS object ids, and the 12 small files were downloaded and hashed. Every file in the repository is pinned: Qwen publishes
no second copy of the weights. If the smoke shows the KV-cache headroom is too tight at 32,768 tokens, the fallback is the
official `Qwen/Qwen3-32B-FP8` checkpoint (34.34 GB), pinned and recorded as such. It is not the first choice.

**Qwen3-32B (FP8) (attempt 3 freeze-5, 2026-09-15), the capable arm's model.** The bf16 checkpoint did not start at 32,768
tokens on one H100: its 61.03 GiB of weights left 7.91 GiB of KV cache at memory utilization 0.95, one full request needs
8.0 GiB, and vLLM's estimated maximum context was 32,384 tokens. Per the founder's rule, the arm moves to the official FP8
checkpoint: a precision change, recorded as such, and "Qwen3-32B (FP8)" wherever the arm's model line appears.
- **The checkpoint:** block-wise FP8 (e4m3, 128×128 weight blocks, dynamic activations), 17 files, 34.34 GB, with the same
  chat template as bf16.
- **The pin:** built the same way, from Hugging Face metadata. The large files' SHA-256 values are the LFS object ids, and
  the small files were downloaded and hashed. Every file is pinned.

Until 2026-09-12 this list existed only on the laptop and on the pod's network volume, so the pin every
manifest cited had no in-repo source of truth. `pod/model.sh` provisions a pod's model cache from Hugging Face
at the pinned revision and verifies it against the list here before any run; a reproducer does the same with
one command and needs nothing from us.

## Continuity

The aggregate hash above is the `pins.model.hash` recorded in every run manifest published before this list was
committed (`first-20260911T205622Z`, the `oss-agent-controls-v1` rehearsal, and the three runs of decisive attempt 1),
so those pins are regenerable from source after the fact. A pod verifies the weights by content against this list
before any run; the source it fetched them from is an operational detail the manifest records separately.
