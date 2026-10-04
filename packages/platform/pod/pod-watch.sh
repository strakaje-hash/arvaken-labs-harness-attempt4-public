#!/usr/bin/env bash
# Laptop side: watch a chain on a pod until it exits (fix A6, 2026-09-14).
#   HOST=<ip> PORT=<port> CHAIN=/root/smoke-chain.sh LOG=/root/smoke-chain.log bash packages/platform/pod/pod-watch.sh
# State comes from the pod's process table and the chain's own log, never from anything this watcher writes (a monitor
# must not read its own output). The subject is named from the same source the poll reads: every poll takes the pod's id
# from the pod itself (RUNPOD_POD_ID in PID 1's environment, read by exact name), so no message can name a pod captured at
# launch that no longer exists; on attempt 2b a watcher started with one pod's id kept naming it after that pod was gone.
# Billing bound: BOUND_S (3 h by default), then it stops and says which pod needs a decision.
# watch.sh is a different watcher (marker files written by the run) and stays.
set -u
HOST=${HOST:?}; PORT=${PORT:?}; CHAIN=${CHAIN:-/root/smoke-chain.sh}; LOG=${LOG:-/root/smoke-chain.log}
BOUND_S=${BOUND_S:-10800}; POLL_S=${POLL_S:-120}
end=$(( $(date +%s) + BOUND_S ))
last=""; pod="(not yet read)"
while :; do
  out=$(ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 -o StrictHostKeyChecking=accept-new root@"$HOST" -p "$PORT" \
    "tr '\\0' '\\n' < /proc/1/environ | sed -n 's/^RUNPOD_POD_ID=//p' | head -n 1; if pgrep -f \"[b]ash $CHAIN\" >/dev/null; then echo CHAIN_RUNNING; else echo CHAIN_GONE; fi; tail -n 1 $LOG 2>/dev/null" 2>&1)
  polled_pod=$(printf '%s\n' "$out" | sed -n '1p')
  state=$(printf '%s\n' "$out" | sed -n '2p')
  line=$(printf '%s\n' "$out" | sed -n '3p')
  case "$state" in
    CHAIN_GONE)
      pod=${polled_pod:-$pod}
      echo "$(date -u +%H:%MZ) pod $pod: chain process gone"
      ssh -o ConnectTimeout=20 root@"$HOST" -p "$PORT" "cat $LOG"
      exit 0 ;;
    CHAIN_RUNNING)
      pod=${polled_pod:-$pod}
      if [ "$line" != "$last" ]; then echo "$(date -u +%H:%MZ) pod $pod running: $line"; last=$line; fi ;;
    *) echo "$(date -u +%H:%MZ) ssh check failed (last pod seen: $pod): $out" ;;
  esac
  if [ "$(date +%s)" -ge "$end" ]; then
    echo "$(date -u +%H:%MZ) BILLING BOUND: chain still running after $((BOUND_S / 60)) min on pod $pod (as the pod reported it at the last poll); it needs a decision"
    exit 3
  fi
  sleep "$POLL_S"
done
