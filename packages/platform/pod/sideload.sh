#!/usr/bin/env bash
# Laptop side (Task 1.3). Ships to the pod over scp: the repo as a git bundle and the pod scripts. No key ever
# ships: signing and anchoring happen on the laptop after `pod/fetch.sh`.
#   bash packages/platform/pod/sideload.sh root@<pod-ip> <ssh-port>              # code (default DEST=/root)
#   MODELS=1 ... also ships this machine's model cache (only needed where Hugging Face is unreachable from the pod)
#   FETCH=1  ... first downloads the model into this machine's HF cache
#
# The model normally does NOT travel this way: `pod/model.sh` fetches the pinned revision on the pod and verifies
# it against benchmarks/model-pins/, which is faster and is what a reproducer does. DEST defaults to the pod's
# container disk (/root), not the network volume: the volume stalled under three pods and then blocked pod
# creation entirely (2026-09-12, docs/POD.md).
set -euo pipefail
HOST=${1:?usage: sideload.sh root@pod-ip ssh-port}; PORT=${2:?usage: sideload.sh root@pod-ip ssh-port}
DEST=${DEST:-/root}; WORK=${WORK:-$DEST}; MODELS=${MODELS:-0}; FETCH=${FETCH:-0}; KEYS=${KEYS:-1}
MODEL_REPO=${MODEL_REPO:-Qwen/Qwen2.5-7B-Instruct-AWQ}
MODEL_REV=${MODEL_REV:-b25037543e9394b818fdfca67ab2a00ecc7dd641}
HUB=${HF_HOME:-$HOME/.cache/huggingface}/hub
ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
# accept-new, not "no": every new pod has a fresh host key and its address came from the authenticated Runpod API,
# so the first connection is trust-on-first-use; a CHANGED key for an address we already know still refuses.
SSHOPT="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30"
SSH="ssh $SSHOPT -p $PORT $HOST"; SCP="scp -q $SSHOPT -P $PORT"
CACHE_DIR="models--${MODEL_REPO//\//--}"

if [ "$FETCH" = 1 ]; then
  echo "== fetching $MODEL_REPO@$MODEL_REV into $HUB"
  uv run --python 3.12 --with "huggingface_hub>=0.27" python - "$MODEL_REPO" "$MODEL_REV" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1], revision=sys.argv[2])
PY
fi

echo "== repo bundle + pod scripts -> $HOST:$WORK"
tmp=$(mktemp -d)
# every local branch, not only main: in phase 0 the pods check out the phase-0 branch (founder ruling 2026-09-23)
git -C "$ROOT" bundle create -q "$tmp/mark.bundle" --branches
$SSH "mkdir -p $WORK $WORK/hf/hub $WORK/results" || { echo "the destination did not answer (a stalled network volume does this): pick DEST=/root"; exit 1; }
$SCP "$tmp/mark.bundle" "$HOST:$WORK/"
# ship the COMMITTED setup.sh (LF), never the Windows working copy (CRLF broke `set -o pipefail` on 2026-09-12)
git -C "$ROOT" show main:packages/platform/pod/setup.sh > "$tmp/platform-setup.sh"
$SCP "$tmp/platform-setup.sh" "$HOST:$WORK/platform-setup.sh"
git -C "$ROOT" show main:packages/platform/pod/localize.sh > "$tmp/localize.sh"
$SCP "$tmp/localize.sh" "$HOST:$WORK/localize.sh"
rm -rf "$tmp"

# No signing key is ever shipped (founder decision, session 2 pre-flight review): the pod writes the UNSIGNED
# manifest and the results hash; `pod/fetch.sh` brings the bundle to the laptop and `platform run sign` signs
# and anchors it where the key lives. A compromised pod can therefore never sign as the founder.
if [ "$KEYS" = 1 ]; then echo "== keys: none shipped (signing happens on the laptop after fetch)"; fi

if [ "$MODELS" = 1 ]; then
  SNAP="$HUB/$CACHE_DIR/snapshots/$MODEL_REV"
  [ -d "$SNAP" ] || { echo "missing $SNAP (run with FETCH=1)"; exit 1; }
  echo "== model checksums"
  (cd "$SNAP" && find . -type f -o -type l | sort | while read -r f; do sha256sum "$f"; done) > /tmp/model-sha256.txt
  MODEL_HASH=$(sha256sum /tmp/model-sha256.txt | cut -d' ' -f1)
  echo "model hash (sha256 over the sorted per-file sha256 list): $MODEL_HASH"
  # One scp stream from a home uplink to an EU pod is ~0.9 MB/s (SSH window over a high RTT, not bandwidth), so the
  # tar is split into PARALLEL parts sent concurrently and joined on the pod; the per-file sha256 check below is the proof.
  PARALLEL=${PARALLEL:-6}
  echo "== model -> $HOST:$WORK/hf/hub/$CACHE_DIR ($PARALLEL parallel streams)"
  ex=()
  if [ -z "$(find "$HUB/$CACHE_DIR/snapshots" -type l | head -1)" ]; then ex+=(--exclude='*/blobs'); fi
  ptmp=$(mktemp -d)
  tar -C "$HUB" -cf "$ptmp/model.tar" "${ex[@]}" "$CACHE_DIR"
  total=$(stat -c %s "$ptmp/model.tar" 2>/dev/null || stat -f %z "$ptmp/model.tar")
  part=$(( total / PARALLEL + 1 ))
  split -b "$part" -d -a 2 "$ptmp/model.tar" "$ptmp/model.part."
  $SSH "rm -rf /root/sideload && mkdir -p /root/sideload"
  for f in "$ptmp"/model.part.*; do $SCP "$f" "$HOST:/root/sideload/" & done
  wait
  $SSH "cat /root/sideload/model.part.* > /root/sideload/model.tar && tar -C $WORK/hf/hub -xf /root/sideload/model.tar --no-same-owner && rm -rf /root/sideload"
  rm -rf "$ptmp"
  # named by the model on the pod, like every per-model artifact (founder ruling 2026-09-22)
  PREFIX="$WORK/hf/${MODEL_REPO//\//-}"
  $SCP /tmp/model-sha256.txt "$HOST:$PREFIX-model-sha256.txt"
  $SSH "echo $MODEL_HASH > $PREFIX-model-hash.txt && cd $WORK/hf/hub/$CACHE_DIR/snapshots/$MODEL_REV && sha256sum -c --quiet $PREFIX-model-sha256.txt && echo 'model verified on the pod'"
fi
echo
echo "shipped. On the pod:  bash $WORK/platform-setup.sh"
