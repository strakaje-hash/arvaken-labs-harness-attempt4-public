#!/usr/bin/env bash
# The env-test with the calibration rule (founder ruling 2026-09-13). run.sh env-test runs it from the repo root:
#   RUNS=/root/runs bash packages/platform/pod/env-test.sh
# When calibration is the ONLY failure, the calibration test runs alone three times. 3/3 clean is a one-off host spike, and
# the full env-test runs once more; any repeat is a noisy host, and the pod is replaced. Never a blind retry: every log is
# kept under its own name.
# Fix A7 (2026-09-14): every attempt is listed in <prefix>.attempts (kind, exit code, log; tab-separated) and
# $RUNS/env-test-latest names the prefix, so `run.sh full` hands the attempts to the run record (bench run --env-test).
# On attempt 2b they stayed as logs outside the run directory and none reached the exported bundle.
set -euo pipefail
RUNS=${RUNS:?}
ETLOG="$RUNS/env-test-$(date -u +%Y%m%dT%H%M%SZ)"
ATTEMPTS="$ETLOG.attempts"
: > "$ATTEMPTS"
attempt() { printf '%s\t%s\t%s\n' "$1" "$2" "$(basename "$3")" >> "$ATTEMPTS"; }

if uv run pytest tests/env -q > "$ETLOG.log" 2>&1; then ET=0; else ET=$?; fi
attempt full "$ET" "$ETLOG.log"
tail -40 "$ETLOG.log"
if [ "$ET" -ne 0 ]; then
  failing=$(grep -E "^(FAILED|ERROR) " "$ETLOG.log" | sed 's/ - .*//' | sort -u || true)
  if [ "$failing" = "FAILED tests/env/test_pod_env.py::test_calibration_within_5ms" ]; then
    echo "calibration rule: calibration is the only failure; the calibration test alone, three times"
    clean=0
    for i in 1 2 3; do
      if uv run pytest tests/env/test_pod_env.py::test_calibration_within_5ms -q -p no:cacheprovider > "$ETLOG.calibration-$i.log" 2>&1; then c=0; else c=$?; fi
      attempt "calibration-alone-$i" "$c" "$ETLOG.calibration-$i.log"
      if [ "$c" -eq 0 ]; then clean=$((clean + 1)); fi
      echo "calibration alone, run $i: $(grep -E 'passed|failed' "$ETLOG.calibration-$i.log" | tail -1)"
    done
    if [ "$clean" -eq 3 ]; then
      echo "calibration rule: 3/3 clean, a one-off host spike; the full env-test once more"
      if uv run pytest tests/env -q > "$ETLOG.rerun.log" 2>&1; then ET=0; else ET=$?; fi
      attempt rerun "$ET" "$ETLOG.rerun.log"
      tail -5 "$ETLOG.rerun.log"
    else
      echo "calibration rule: calibration failed again in $((3 - clean)) of 3: a noisy host, replace the pod"
    fi
  fi
fi
printf '%s\n' "$ETLOG" > "$RUNS/env-test-latest"
echo "env-test attempts: $ATTEMPTS"
exit "$ET"
