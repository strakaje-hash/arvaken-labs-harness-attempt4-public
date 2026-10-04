#!/usr/bin/env bash
# Per-pod working directory. Everything a pod runs from lives on its OWN container disk ($WORK), so pods never
# share a checkout, a Python environment, a vLLM environment or a model cache, and file modes are enforced (a
# network volume is FUSE and enforces none).
#
#   bash /root/localize.sh          # then: WORK=/opt/mark bash /opt/mark/platform-setup.sh
#
# The network volume is OPTIONAL and, since 2026-09-12, unused: it stalled under three pods during the first
# decisive attempt and then blocked pod creation outright (a pod mounting it never started). The repo arrives by
# scp from the laptop, and `pod/model.sh` provisions the model from Hugging Face at the pinned revision and
# verifies it against the list committed in the repo. Exported bundles go to $RESULTS (default /root/results) and
# are fetched to the laptop, where the signing key lives.
set -euo pipefail
WORK=${WORK:-/opt/mark}
SRC=${SRC:-/root}          # where sideload/scp put mark.bundle and platform-setup.sh
mkdir -p "$WORK" "$WORK/hf" "$WORK/targets" "${RESULTS:-/root/results}"
for f in mark.bundle platform-setup.sh; do
  if [ -f "$SRC/$f" ]; then cp "$SRC/$f" "$WORK/$f"
  elif [ -f "/workspace/$f" ]; then timeout 60 cp "/workspace/$f" "$WORK/$f" || { echo "the network volume did not answer for $f"; exit 1; }
  else echo "no $f in $SRC or /workspace: ship it from the laptop first"; exit 1; fi
done
echo "localized into $WORK. next:  WORK=$WORK bash $WORK/platform-setup.sh   (setup.sh runs pod/model.sh)"
