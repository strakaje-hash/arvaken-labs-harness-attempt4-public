#!/usr/bin/env bash
# Laptop side: bring an exported run bundle home, then sign and anchor it here, where the key lives.
#   bash packages/platform/pod/fetch.sh root@<pod-ip> <ssh-port> <run_id>
#   uv run platform run sign --run-dir runs/<run_id> --key packages/bundles/keys/bundle-<id>.key --cert packages/bundles/keys/certs/<id>.json --anchor rekor
set -euo pipefail
HOST=${1:?usage: fetch.sh root@pod-ip ssh-port run_id}; PORT=${2:?}; RUN=${3:?}
RESULTS=${RESULTS:-/root/results}   # exported bundles live on the pod's container disk (run.sh export); no shared storage since 2026-09-12
mkdir -p runs
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -p "$PORT" "$HOST" "cd $RESULTS && tar -czf - $RUN" > "runs/$RUN.tgz"
tar -xzf "runs/$RUN.tgz" -C runs/
echo "fetched runs/$RUN ($(du -sh "runs/$RUN" | cut -f1))"
uv run mark-ledger verify "runs/$RUN/ledger" "$RUN" | python -c "import sys,json; d=json.load(sys.stdin); print('ledger ok=', d['ok'], 'records', d['records'])"
echo "next: uv run platform run sign --run-dir runs/$RUN --key <run-manifest private key> --cert <its cert> --anchor rekor"
