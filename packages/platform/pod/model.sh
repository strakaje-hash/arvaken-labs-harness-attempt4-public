#!/usr/bin/env bash
# Provision a pod's model cache and PROVE it is the pinned snapshot, then leave the pod offline for the run.
#
#   bash packages/platform/pod/model.sh            # into $WORK/hf, verified against the list in the repo
#
# Two sources, one proof. If a network volume already holds the snapshot it is copied from there; otherwise it is
# downloaded from Hugging Face at the pinned revision. Either way the per-file SHA-256 list committed at
# benchmarks/model-pins/ is what decides, and its own sha256 is the `pins.model.hash` every manifest records: a
# pod cannot run on weights that are not byte-identical to the ones the published runs used.
#
# History (2026-09-12): the model lived only on a Runpod network volume. That volume stalled under three pods and
# then blocked pod creation entirely (a pod mounting it never started), so the decisive run was held up by the one
# component that held the pin. Hugging Face turned out to be reachable from these pods (the 429 recorded in
# docs/POD.md was another region, months earlier), which removes the volume from the path and gives a reproducer
# the same one command we use.
set -euo pipefail
WORK=${WORK:-/opt/mark}
REPO_DIR=${REPO_DIR:-$WORK/mark}
MODEL_REPO=${MODEL_REPO:-Qwen/Qwen2.5-7B-Instruct-AWQ}
MODEL_REV=${MODEL_REV:-b25037543e9394b818fdfca67ab2a00ecc7dd641}
VOL=${VOL:-/workspace}
PIN="$REPO_DIR/benchmarks/model-pins/$(basename "$MODEL_REPO")-$MODEL_REV.sha256"
HUB="$WORK/hf/hub"
CACHE_DIR="models--${MODEL_REPO//\//--}"
SNAP="$HUB/$CACHE_DIR/snapshots/$MODEL_REV"

[ -f "$PIN" ] || { echo "no pin list at $PIN: the model hash has no in-repo source of truth, refusing"; exit 1; }
# freeze-3: when the pod names the model it will serve, the repository provisioned must be that served name's
source "$REPO_DIR/packages/platform/pod/model-env.sh"
if [ -n "${MARK_LLM_MODEL:-}" ]; then
  WANT=$(model_repo_for "$MARK_LLM_MODEL" || true)
  [ "$WANT" = "$MODEL_REPO" ] || { echo "MARK_LLM_MODEL $MARK_LLM_MODEL is served from ${WANT:-no pinned repository}, not $MODEL_REPO: refusing"; exit 1; }
fi
mkdir -p "$HUB" "$WORK/hf"

if [ ! -d "$SNAP" ] && timeout 20 test -d "$VOL/hf/hub/$CACHE_DIR/snapshots/$MODEL_REV" 2>/dev/null; then
  echo "== model from the network volume"
  timeout 900 cp -r "$VOL/hf/hub/$CACHE_DIR" "$HUB/" || { echo "volume copy failed or stalled; falling back to Hugging Face"; rm -rf "$HUB/$CACHE_DIR"; }
fi
if [ ! -d "$SNAP" ]; then
  echo "== model from Hugging Face at the pinned revision (offline mode is set AFTER this step)"
  # Freeze-2 (2026-09-14): download exactly the files the pin list names. gpt-oss-120b's repository is 195.8 GB with two
  # more copies of the weights (metal/, original/); vLLM serves the 26 pinned files (65.3 GB), and anything else would be
  # downloaded only to be refused by the check below.
  source "$REPO_DIR/packages/platform/pod/model-env.sh"
  PREFIX=$(model_files_prefix "$WORK" "$MODEL_REPO")
  pinned_paths "$PIN" > "$PREFIX-pinned-paths.txt"
  HF_HUB_OFFLINE=0 HF_HOME="$WORK/hf" "$REPO_DIR/.venv/bin/python" - "$MODEL_REPO" "$MODEL_REV" "$PREFIX-pinned-paths.txt" <<'PY'
import sys
from huggingface_hub import snapshot_download
paths = [line.strip() for line in open(sys.argv[3], encoding="utf-8") if line.strip()]
p = snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], allow_patterns=paths, max_workers=8)
print("   downloaded", len(paths), "pinned files to", p)
PY
fi
[ -d "$SNAP" ] || { echo "no snapshot at $SNAP"; exit 1; }

echo "== verifying every file against $PIN"
( cd "$SNAP" && sha256sum -c --quiet "$PIN" ) || { echo "MODEL VERIFY FAILED: the cache is not the pinned snapshot"; exit 1; }
EXTRA=$(cd "$SNAP" && { find . -type f -o -type l; } | sort | comm -23 - <(cut -d'*' -f2 "$PIN" | sort) | head -5)
[ -z "$EXTRA" ] || { echo "MODEL VERIFY FAILED: files present that the pin does not list: $EXTRA"; exit 1; }
HASH=$(sha256sum "$PIN" | cut -d' ' -f1)
# named by the model: two models on one pod must not share these, and the hash below reaches the signed manifest
PREFIX=$(model_files_prefix "$WORK" "$MODEL_REPO")
cp "$PIN" "$PREFIX-model-sha256.txt"
echo "$HASH" > "$PREFIX-model-hash.txt"
chmod -R go-w "$WORK/hf"
echo "model verified: $MODEL_REPO@$MODEL_REV, $(wc -l < "$PIN") files, hash $HASH"
