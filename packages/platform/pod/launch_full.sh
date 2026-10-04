#!/usr/bin/env bash
# Launch attempt 4's full run on five machines, one model each (founder ruling 2026-09-23). Laptop side.
#   bash packages/platform/pod/launch_full.sh <machines file> <commit>
# The machines file has exactly five lines, "name target model host port":
#   scripted         scripted       Qwen/Qwen3-32B-FP8            <ip> <port>
#   langgraph-cap    langgraph-ref  Qwen/Qwen3-32B-FP8            <ip> <port>
#   langgraph-small  langgraph-ref  Qwen/Qwen2.5-7B-Instruct-AWQ  <ip> <port>
#   openhands-cap    openhands-sdk  Qwen/Qwen3-32B-FP8            <ip> <port>
#   openhands-small  openhands-sdk  Qwen/Qwen2.5-7B-Instruct-AWQ  <ip> <port>
# The machines are created beforehand from the pinned image digest; this script never creates, stops or pays for one.
# It refuses a file that is not exactly these five target x model pairs, a commit that is not the freeze tag's
# (FREEZE_TAG, default attempt4-freeze-4; REHEARSAL=1 skips only that check and says so in every machine's log), and a
# machine on which a pass is already running. Per machine: the code as a git bundle (sideload.sh), full_run.sh taken
# from the commit itself and checked on arrival, then full_run.sh started in its own session.
set -euo pipefail
FILE=${1:?usage: launch_full.sh <machines file> <commit>}; COMMIT=${2:?commit}
ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
FULL=$(git -C "$ROOT" rev-parse "$COMMIT^{commit}")
if [ "${REHEARSAL:-0}" = 1 ]; then
  echo "REHEARSAL: the freeze-tag check is skipped; nothing this launches is attempt 4's run"
else
  TAG=${FREEZE_TAG:-attempt4-freeze-4}
  [ "$(git -C "$ROOT" rev-parse "$TAG^{commit}" 2>/dev/null)" = "$FULL" ] || { echo "REFUSED: $COMMIT is not the commit of $TAG"; exit 1; }
fi
mapfile -t LINES < <(grep -vE '^\s*(#|$)' "$FILE")
[ ${#LINES[@]} = 5 ] || { echo "REFUSED: ${#LINES[@]} machines listed, not 5"; exit 1; }
PAIRS=$(for l in "${LINES[@]}"; do set -- $l; [ "$2" = scripted ] && echo "scripted" || echo "$2 $3"; done | sort | tr '\n' ';')
WANT=$(printf '%s\n' "langgraph-ref Qwen/Qwen2.5-7B-Instruct-AWQ" "langgraph-ref Qwen/Qwen3-32B-FP8" "openhands-sdk Qwen/Qwen2.5-7B-Instruct-AWQ" "openhands-sdk Qwen/Qwen3-32B-FP8" "scripted" | sort | tr '\n' ';')
[ "$PAIRS" = "$WANT" ] || { echo "REFUSED: the machines are not the five target x model pairs (got: $PAIRS)"; exit 1; }
for l in "${LINES[@]}"; do set -- $l; case "$3" in Qwen/Qwen3-32B-FP8|Qwen/Qwen2.5-7B-Instruct-AWQ) ;; *) echo "REFUSED: $1 serves $3, not a pinned arm"; exit 1;; esac; done

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git -C "$ROOT" show "$FULL:packages/platform/pod/full_run.sh" > "$tmp/full_run.sh"
WANT_SHA=$(sha256sum "$tmp/full_run.sh" | cut -d' ' -f1)
SSHOPT="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30"
for l in "${LINES[@]}"; do
  set -- $l; NAME=$1; TARGET=$2; MODEL=$3; HOST=$4; PORT=$5
  echo "== $NAME: $TARGET on $MODEL at $HOST:$PORT"
  if ssh -n $SSHOPT -p "$PORT" "root@$HOST" 'pgrep -f "^bash /root/(full_run|practice)\.sh" >/dev/null'; then echo "REFUSED: a pass is already running on $NAME"; exit 1; fi
  bash "$ROOT/packages/platform/pod/sideload.sh" "root@$HOST" "$PORT" | tail -1
  scp -q $SSHOPT -P "$PORT" "$tmp/full_run.sh" "root@$HOST:/root/full_run.sh"
  GOT=$(ssh -n $SSHOPT -p "$PORT" "root@$HOST" "sha256sum /root/full_run.sh | cut -d' ' -f1")
  [ "$GOT" = "$WANT_SHA" ] || { echo "REFUSED: full_run.sh arrived as $GOT, expected $WANT_SHA"; exit 1; }
  NOTE=""; [ "${REHEARSAL:-0}" = 1 ] && NOTE="echo '[$(date -u +%H:%M:%S)] REHEARSAL: not attempt 4 run' >> /root/full-$NAME.log;"
  ssh -n $SSHOPT -p "$PORT" "root@$HOST" "$NOTE nohup setsid bash /root/full_run.sh $NAME $TARGET $MODEL $FULL < /dev/null > /root/full-$NAME.out 2>&1 & disown"
  sleep 5
  ssh -n $SSHOPT -p "$PORT" "root@$HOST" "grep -m1 'full run: machine' /root/full-$NAME.log" || { echo "REFUSED: $NAME wrote no first line"; exit 1; }
done
echo "launched: 5 machines on $FULL"
