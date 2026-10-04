#!/usr/bin/env bash
# Close one machine of attempt 4's full run (founder ruling 2026-09-23). Laptop side.
#   bash packages/platform/pod/close_full.sh <host> <port> <machine>
# 1. read the machine's last log line: DONE <run>, or STOPPED (a stopped machine's run is fetched as a record of the stop,
#    never as a result: nothing from it counts until its cells rerun on a fresh machine);
# 2. fetch the bundle and its checksum; the checksum is recomputed here and must equal the pod's;
# 3. unpack into runs/ and verify the ledger;
# 4. print the laptop close steps, in the order the tools enforce.
# The machine itself is stopped or terminated only after this prints "fetched and verified".
set -euo pipefail
HOST=${1:?usage: close_full.sh <host> <port> <machine>}; PORT=${2:?}; NAME=${3:?}
ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
SSH="ssh -n -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -p $PORT root@$HOST"
LAST=$($SSH "tail -1 /root/full-$NAME.log")
echo "$NAME: $LAST"
case "$LAST" in
  *"] DONE "*) RUN=${LAST##* DONE } ;;
  *STOPPED*)
    RUN=$($SSH "grep -o 'full run attempt4-agent-controls-[^ ,]*' /root/full-$NAME.log | tail -1 | cut -d' ' -f3")
    [ -n "$RUN" ] || { echo "stopped before its run opened: nothing to fetch but the log"; $SSH "cat /root/full-$NAME.log" > "$ROOT/runs/full-$NAME.stopped.log"; exit 0; }
    $SSH "cd /root && tar -czf $RUN.stopped.tgz -C /root/runs $RUN host-$NAME.json && sha256sum $RUN.stopped.tgz > $RUN.stopped.tgz.sha256"
    RUN=$RUN.stopped ;;
  *) echo "REFUSED: $NAME has not finished (last line above)"; exit 1 ;;
esac
mkdir -p "$ROOT/runs"
scp -q -o StrictHostKeyChecking=accept-new -P "$PORT" "root@$HOST:/root/$RUN.tgz" "root@$HOST:/root/$RUN.tgz.sha256" "$ROOT/runs/"
$SSH "cat /root/full-$NAME.log" > "$ROOT/runs/full-$NAME.log"
( cd "$ROOT/runs" && sha256sum -c "$RUN.tgz.sha256" ) || { echo "REFUSED: the checksum computed here differs from the pod's"; exit 1; }
tar -xzf "$ROOT/runs/$RUN.tgz" -C "$ROOT/runs/"
case "$RUN" in
  *.stopped) echo "fetched and verified: runs/${RUN%.stopped} (STOPPED on the stop rule: a record, not a result)"; exit 0 ;;
esac
( cd "$ROOT" && uv run mark-ledger verify "runs/$RUN/ledger" "$RUN" ) | python -c "import sys,json; d=json.load(sys.stdin); print('ledger ok=', d['ok'], 'records', d['records']); sys.exit(0 if d['ok'] else 1)"
echo "fetched and verified: runs/$RUN"
cat <<NEXT
Close steps for $RUN, in the order the tools enforce (platform run sign refuses a bundle without the first two):
  once all five machines are fetched:  uv run platform bench consistency runs/<the five runs>
  1. uv run platform run redecide --run-dir runs/$RUN
  2. uv run platform bench timeline --run-dir runs/$RUN --verdict pass     (every decisive pass, read by a person against the
     probe's question and against none), then  uv run platform run pass-sample-record --run-dir runs/$RUN --review <review.json>
  3. the founder signs and anchors, on the founder's machine and word:
     uv run platform run sign --run-dir runs/$RUN --key <run-manifest key> --cert <its cert> --anchor rekor
NEXT
