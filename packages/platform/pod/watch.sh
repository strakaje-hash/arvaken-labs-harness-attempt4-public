#!/usr/bin/env bash
# Watch a long job on the pod and distinguish the four states a caller must never confuse. "No output yet" and
# "nothing is running" look identical in a log tail, and the second one is the dangerous one: the first decisive
# smoke was launched through an ssh stdin pipe, the script was lost when the session closed, and the empty log
# read as a pending result for minutes (2026-09-12). Silence from a watcher is evidence about the watcher.
#
#   bash pod/watch.sh /root/smokerun.log --marker 'SMOKE 5|SMOKE ABORTED' --proc smoke-run.sh
#
#   DONE    the marker appeared                     -> exit 0
#   GONE    no process matches and no marker        -> exit 2   (the silent absence: nothing will ever appear)
#   STALLED alive but the log has not grown         -> exit 3   (printed with how long it has been quiet)
#   RUNNING alive and growing                       -> keeps watching
#
# Exactly one of these is printed per state change, plus a heartbeat line every --beat intervals so a caller can
# see progress without tailing. The states are decided by the process table and the log size, never by the log
# alone: a job that writes nothing for an hour is RUNNING, and an empty log with no process is GONE.
set -uo pipefail
LOG=${1:?usage: watch.sh <log> [--marker REGEX] [--proc PATTERN] [--interval S] [--timeout S] [--stall N] [--beat N]}
shift
MARKER=""; PROC=""; INTERVAL=15; TIMEOUT=7200; STALL=40; BEAT=20
while [ $# -gt 0 ]; do
  case "$1" in
    --marker) MARKER=$2; shift 2;;
    --proc) PROC=$2; shift 2;;
    --interval) INTERVAL=$2; shift 2;;
    --timeout) TIMEOUT=$2; shift 2;;
    --stall) STALL=$2; shift 2;;
    --beat) BEAT=$2; shift 2;;
    *) echo "unknown option $1"; exit 64;;
  esac
done
size() { [ -f "$LOG" ] && wc -c < "$LOG" | tr -d ' ' || echo 0; }
# A missing pgrep must not read as a dead process: an absent tool is not evidence of absence, so the watcher
# refuses rather than reporting GONE (Git Bash on the authoring machine has no pgrep; the pod installs procps).
if [ -n "$PROC" ] && ! command -v pgrep > /dev/null; then
  echo "REFUSING: --proc given but pgrep is not on PATH, so liveness cannot be decided (install procps)"; exit 64
fi
# `pgrep -f PATTERN` matches this watcher too, because the pattern appears in ITS own command line: watching
# "smoke-run.sh" from `bash watch.sh ... --proc smoke-run.sh` made the watcher see itself and report RUNNING for a
# job that had finished (2026-09-12), which is the silent absence this script exists to prevent, one level up. Own
# pid, parent pid and any process whose command line mentions this script are excluded.
alive() {
  [ -z "$PROC" ] && return 0
  local pids p cmd
  pids=$(pgrep -f -- "$PROC" 2>/dev/null) || return 1
  for p in $pids; do
    [ "$p" = "$$" ] && continue
    [ "$p" = "$PPID" ] && continue
    cmd=$(ps -p "$p" -o args= 2>/dev/null) || continue    # ps, not /proc: no null bytes, and a pid that just exited is simply gone
    case "$cmd" in *watch.sh*) continue;; esac
    return 0
  done
  return 1
}
marked() { [ -n "$MARKER" ] && [ -f "$LOG" ] && grep -qE -- "$MARKER" "$LOG"; }

last=$(size); quiet=0; ticks=0; deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  if marked; then echo "DONE marker after $((ticks * INTERVAL))s, log $(size) bytes"; exit 0; fi
  if ! alive; then
    # A job may finish between the marker check and here; re-check before calling it an absence.
    sleep 2
    if marked; then echo "DONE marker after $((ticks * INTERVAL))s, log $(size) bytes"; exit 0; fi
    echo "GONE no process matches '$PROC' and no marker in $LOG ($(size) bytes): nothing is running, so nothing will appear"
    [ "$(size)" = 0 ] && echo "      the log is EMPTY: the job never wrote a line (a lost stdin script does this; run it from a file)"
    exit 2
  fi
  now=$(size)
  if [ "$now" = "$last" ]; then quiet=$((quiet + 1)); else quiet=0; last=$now; fi
  if [ "$quiet" -ge "$STALL" ]; then echo "STALLED alive but $LOG has not grown for $((quiet * INTERVAL))s ($now bytes)"; exit 3; fi
  ticks=$((ticks + 1))
  if [ $((ticks % BEAT)) = 0 ]; then echo "RUNNING $((ticks * INTERVAL))s, log $now bytes, quiet $((quiet * INTERVAL))s"; fi
  [ "$(date +%s)" -lt "$deadline" ] || { echo "TIMEOUT after ${TIMEOUT}s, alive, log $now bytes"; exit 4; }
  sleep "$INTERVAL"
done
