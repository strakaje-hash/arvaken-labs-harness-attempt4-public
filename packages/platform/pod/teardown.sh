#!/usr/bin/env bash
# Pod teardown (Task 1.2): export every run bundle under /root/runs to /workspace/results after verifying its
# ledger, then wipe the container-disk run data and check that nothing sensitive remains. Exit 1 if the check
# fails. Run before stopping or terminating the pod.
#   bash /workspace/mark/packages/platform/pod/teardown.sh
set -euo pipefail
WORK=${WORK:-/workspace}
RUNS=${RUNS:-/root/runs}
export PATH="$HOME/.local/bin:$PATH"
cd "$WORK/mark"

echo "== export run bundles"
shopt -s nullglob
for d in "$RUNS"/*/; do
  run=$(basename "$d")
  [ -d "$d/ledger" ] || { echo "skip $run (no ledger)"; continue; }
  if [ -d "$WORK/results/$run" ]; then echo "already exported: $run"; continue; fi
  uv run platform run export --run-dir "$d" --dest "$WORK/results" || { echo "EXPORT FAILED for $run: ledger does not verify; NOT wiping"; exit 1; }
done

echo "== stop services"
pkill -f "vllm serve" || true
pkill -f otelcol-contrib || true
pkill -f "tempo -config" || true
service postgresql stop >/dev/null 2>&1 || true

echo "== wipe container-disk run data"
rm -rf "$RUNS"
rm -rf /etc/platform/secrets
rm -rf /home/runner/.cache /tmp/mark-*

echo "== check: nothing sensitive remains outside the results volume"
left=$( { find / -xdev \( -name "manifest.key" -o -name "*.key" -path "*/platform/*" -o -name canary.txt -o -name ".env" \) 2>/dev/null | grep -v "^$WORK/mark/node_modules" || true; } )
if [ -n "$left" ]; then echo "STILL PRESENT:"; echo "$left"; exit 1; fi
if [ -d "$RUNS" ]; then echo "STILL PRESENT: $RUNS"; exit 1; fi
# exported bundles must not carry secrets either
if grep -rl "BEGIN PRIVATE KEY\|canary-" "$WORK/results" 2>/dev/null | head -1 | grep -q .; then echo "secret material inside an exported bundle"; exit 1; fi
echo "   clean. results: $(ls "$WORK/results" 2>/dev/null | tr '\n' ' ')"
echo "now stop or terminate the pod."
