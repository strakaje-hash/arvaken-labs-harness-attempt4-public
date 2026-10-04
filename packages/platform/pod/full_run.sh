#!/usr/bin/env bash
# Attempt 4's full run on ONE machine of five, each serving exactly one model for the whole run (founder ruling 2026-09-23).
#   bash /root/full_run.sh <machine> <target> <model repo> <commit>
# Shipped by launch_full.sh from the commit itself; after checkout it checks that it IS the committed copy. Every step logs
# to /root/full-<machine>.log, and the last line is DONE <run id>, or STOPPED with where and why:
#   0. host record -- GPU, driver, CUDA, pod id, data center, image, CPUs, kernel -> /root/runs/host-<machine>.json
#   1. environment and checkout on <commit>; this machine's model only, verified against its pin list
#   2. serve the model, pinned
#   3. the attribution test alone: exactly 3 passed, nothing skipped, failed, errored or deselected
#   4. the environment test under the recorded deviation (taskset -c 18-21), every process's affinity read from /proc
#   5. calibration certified on THIS machine: three clean runs on its own (attempt 3's rule), else stop and replace it
#   6. the full run, under the stop-rule watch: the committed checker reads every cell as the ledger records it; a stop
#      ends this machine's run (SIGTERM, which closes clean) and the other machines carry on
#   7. export, and a checksum of the bundle to fetch
set -u
MACHINE=${1:?usage: full_run.sh <machine> <target> <model repo> <commit>}; TARGET=${2:?target}; MODEL=${3:?model repo}; COMMIT=${4:?commit}
case "$MODEL" in
  Qwen/Qwen3-32B-FP8) REV=aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df;;
  Qwen/Qwen2.5-7B-Instruct-AWQ) REV=b25037543e9394b818fdfca67ab2a00ecc7dd641;;
  *) echo "unknown model $MODEL: not one of the two pinned arms"; exit 2;;
esac
SLUG=${MODEL##*/}
START_S=$(date +%s)   # the machine's time is counted from here against twice its estimate (founder ruling 2026-09-23)
L=/root/full-$MACHINE.log
say() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$L"; }
stop() { say "STOPPED: $*"; exit 1; }
cd /root
mkdir -p /root/runs

say "full run: machine $MACHINE, target $TARGET, model $MODEL@$REV, commit $COMMIT"
say "0/7 host record"
python3 - "$MACHINE" "$TARGET" "$MODEL" "$REV" "$COMMIT" > "/root/runs/host-$MACHINE.json" <<'PY' || stop "host record"
import json, os, platform, subprocess, sys
from datetime import datetime, timezone
def q(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"unreadable: {type(e).__name__}"
# RunPod sets the pod's identity in the container's own environment (PID 1); a process launched over ssh does not inherit
# it -- the rehearsal on 2026-09-23 recorded pod None, data center None. Read PID 1's first, this process's as the fallback.
try:
    pid1 = dict(kv.split("=", 1) for kv in open("/proc/1/environ", "rb").read().decode(errors="replace").split("\0") if "=" in kv)
except OSError:
    pid1 = {}
def env(k):
    return pid1.get(k) or os.environ.get(k)
gpu = q("nvidia-smi", "--query-gpu=name,driver_version,memory.total,serial", "--format=csv,noheader").split(", ")
print(json.dumps({"machine": sys.argv[1], "target": sys.argv[2], "model": sys.argv[3], "revision": sys.argv[4], "commit": sys.argv[5],
                  "recorded_at": datetime.now(timezone.utc).isoformat(), "pod_id": env("RUNPOD_POD_ID"),
                  "data_center": env("RUNPOD_DC_ID"), "image": env("MARK_IMAGE_REF"),
                  "identity_from": "the container's environment (PID 1)" if pid1.get("RUNPOD_POD_ID") else "this process's environment",
                  "gpu": gpu[0] if gpu else None, "driver": gpu[1] if len(gpu) > 1 else None, "gpu_memory": gpu[2] if len(gpu) > 2 else None,
                  "cuda": q("sh", "-c", "nvidia-smi | grep -o 'CUDA Version: [0-9.]*'"), "cpus_visible": os.cpu_count(),
                  "cgroup_cpu_max": q("cat", "/sys/fs/cgroup/cpu.max"), "kernel": platform.release(), "hostname": platform.node()}, indent=1))
PY
say "   $(python3 -c "import json; d=json.load(open('/root/runs/host-$MACHINE.json')); print(d['gpu'], '| driver', d['driver'], '|', d['cuda'], '| pod', d['pod_id'], '| dc', d['data_center'])")"

say "1/7 environment and checkout ($MODEL only)"
WORK=/root MARK_LLM_MODEL=$MODEL MODEL_REPO=$MODEL MODEL_REV=$REV bash /root/platform-setup.sh >> "$L" 2>&1 || stop "platform-setup"
grep "model verified: $MODEL@$REV" "$L" | tail -1 | sed 's/^/   /' | tee -a "$L" >/dev/null
cd /root/mark && git fetch -q origin && git checkout -q --detach "$COMMIT" && git clean -qfd || stop "checkout $COMMIT"
[ "$(git rev-parse HEAD)" = "$(git rev-parse "$COMMIT")" ] && [ -z "$(git status --porcelain)" ] || stop "HEAD is not $COMMIT, or the tree is dirty"
cmp -s /root/full_run.sh packages/platform/pod/full_run.sh || stop "this script differs from the committed full_run.sh"
# The cost rule's exception (founder ruling 2026-09-23): a machine that passes twice its own estimate is stopped and reported.
# The bound is the committed plan's, read here, never typed at launch; a machine the plan does not name is refused.
STOP_AT_H=$(python3 -c "import json; print(json.load(open('benchmarks/attempt4-run-plan.json'))['machines']['$MACHINE']['stop_at_h'])" 2>/dev/null) \
  || stop "benchmarks/attempt4-run-plan.json names no estimate for $MACHINE: the cost bound needs one"
say "   this machine stops itself at ${STOP_AT_H} h, twice its estimate"
cd /root
for f in model-sha256.txt model-hash.txt pinned-paths.txt; do rm -f "/root/hf/$f"; done

harness_env() {
  for _v in $(compgen -e); do case "$_v" in RUNPOD_*|*API_KEY*|*APIKEY*|*SECRET*|*TOKEN*|*PASSWORD*|*PASSWD*|*PRIVATE_KEY*|*CREDENTIAL*|*ACCESS_KEY*) unset "$_v" ;; esac; done
  export WORK=/root RUNS=/root/runs MARK_RUNS=/root/runs PATH="/root/vllm-venv/bin:$HOME/.local/bin:$PATH" HF_HUB_OFFLINE=1 HF_HOME=/root/hf PYTHONUNBUFFERED=1
  export MARK_LLM_URL=http://127.0.0.1:8000/v1 MARK_LLM_MODEL=$MODEL MARK_SERVING_LOG=/root/runs/vllm-${MODEL//\//-}.log
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 MARK_COLLECTOR_ARCHIVE=/root/runs/otel-spans.jsonl MARK_SANDBOX_CMD="bash /root/mark/packages/platform/pod/sandbox.sh --"
}

say "2/7 serving $MODEL, pinned"
pkill -f "[v]llm serve"; for i in $(seq 1 45); do pgrep -f "[v]llm serve" >/dev/null || break; sleep 2; done
WORK=/root MARK_LLM_MODEL=$MODEL MODEL_REPO=$MODEL MODEL_REV=$REV bash /root/mark/packages/platform/pod/run.sh services > /root/services-$MACHINE.log 2>&1 || stop "run.sh services"
served=$(curl -sf http://127.0.0.1:8000/v1/models | jq -r '.data[].id')
[ "$served" = "$MODEL" ] || stop "serving '$served', expected $MODEL"
say "   serving $served on cores $(grep Cpus_allowed_list /proc/$(pgrep -f '[v]llm serve' | head -1)/status | awk '{print $2}')"

say "3/7 the attribution test alone (tests/env/test_attribution.py): exactly 3 passed"
ALOG=/root/runs/attribution-test-$MACHINE-$(date -u +%Y%m%dT%H%M%SZ).log
( harness_env; cd /root/mark && taskset -c 18-21 uv run pytest tests/env/test_attribution.py -v -rA -p no:cacheprovider ) > "$ALOG" 2>&1
ARC=$?
summary=$(grep -E "^=+ .*(passed|failed|error|skipped).* =+$" "$ALOG" | tail -1)
say "   $summary (exit $ARC; log $(basename "$ALOG"))"
if [ "$ARC" -ne 0 ] || ! echo "$summary" | grep -qE "(^|[^0-9])3 passed" || echo "$summary" | grep -qiE "skipped|failed|error|deselected"; then
  tail -60 "$ALOG" >> "$L"
  stop "the attribution test did not pass exactly three: $summary"
fi

say "4/7 environment test under the recorded deviation (taskset -c 18-21), affinity from /proc"
AFF=/root/runs/envtest-$MACHINE-affinity.txt; : > "$AFF"
taskset -c 18-21 env WORK=/root MARK_LLM_MODEL=$MODEL bash /root/mark/packages/platform/pod/run.sh env-test > /root/envtest-$MACHINE.log 2>&1 &
ROOT=$!
descend() { local p; for p in $(pgrep -P "$1"); do echo "$p"; descend "$p"; done; }
while kill -0 "$ROOT" 2>/dev/null; do
  for p in $ROOT $(descend "$ROOT"); do
    [ -r /proc/$p/status ] || continue
    c=$(grep Cpus_allowed_list /proc/$p/status 2>/dev/null | awk '{print $2}')
    [ -n "$c" ] || continue   # a process that exited between the two reads has no affinity to report: skipped, never a blank
    echo "$(date -u +%H:%M:%S) pid $p cpus $c :: $(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-70)" >> "$AFF"
  done
  sleep 2
done
wait "$ROOT"; RC=$?; echo "env-test exit: $RC" >> "$AFF"
cp "$AFF" "$(cat /root/runs/env-test-latest).affinity"
OUTSIDE=$(grep ' pid ' "$AFF" | awk '{print $5}' | grep -vx '18-21' | sort -u)
say "   env-test exit $RC | $(grep -E '[0-9]+ (passed|failed)' /root/envtest-$MACHINE.log | tail -1) | outside 18-21: ${OUTSIDE:-none}"
[ "$RC" = 0 ] && [ -z "$OUTSIDE" ] || stop "env-test failed or ran outside the declared cores"

say "5/7 calibration on this machine: three clean runs on its own"
CRUN=calibrate-$MACHINE-$(date -u +%Y%m%dT%H%M%SZ)
RUN=$CRUN WORK=/root MARK_LLM_MODEL=$MODEL bash /root/mark/packages/platform/pod/run.sh calibrate -n 3 > /root/runs/$CRUN.out 2>&1
CRC=$?
# each replication's CALIBRATION verdict and its worst sample -- not its run status, which read "ok ok ok" beside a failed
# calibration in the rehearsal of 2026-09-23 (a log line that reads as success for a failure)
say "   calibration exit $CRC: $(python3 -c "import json,re; t=open('/root/runs/$CRUN.out').read(); m=re.search(r'\{\s*\"ok\".*\}', t, re.S); d=json.loads(m.group(0)) if m else {}; print(' | '.join(('ok' if (r.get('calibration') or {}).get('ok') else 'FAILED') + ' max %.1f ms' % ((r.get('calibration') or {}).get('max_ms') or -1) for r in d.get('replications') or []) or 'no replications recorded')" 2>/dev/null || echo unreadable)"
[ "$CRC" = 0 ] || stop "calibration did not pass three of three on this machine: replace it with a fresh machine from the pinned image (pre-registration)"
if [ "${CHECKS_ONLY:-0}" = 1 ]; then
  say "DONE checks (CHECKS_ONLY: every check passed; the full run was not started)"
  exit 0
fi

RUN=attempt4-agent-controls-$TARGET-$SLUG-$(date -u +%Y%m%dT%H%M%SZ)
say "6/7 full run $RUN, under the stop-rule watch"
# setsid -w: its own process group (so a stop can end the whole run), and waited on (so `wait` below means the run is over)
( cd /root && RUN=$RUN WORK=/root MARK_LLM_MODEL=$MODEL setsid -w bash /root/mark/packages/platform/pod/run.sh full --matrix attempt4-agent-controls --targets "$TARGET" > "/root/$RUN.out" 2>&1 ) &
BENCH=$!
for i in $(seq 1 90); do [ -d "/root/runs/$RUN/ledger" ] && break; kill -0 "$BENCH" 2>/dev/null || break; sleep 2; done
[ -d "/root/runs/$RUN/ledger" ] || { tail -40 "/root/$RUN.out" >> "$L"; stop "the bench run did not open (see /root/$RUN.out)"; }
bench_pgid() { ps -o pgid= -p "$(pgrep -f "^bash /root/mark/packages/platform/pod/[r]un.sh full" | head -1)" 2>/dev/null | tr -d ' '; }
end_bench() {   # SIGTERM closes the run cleanly; bounded, then KILL; then nothing of the agents' user survives it
  local PG; PG=$(bench_pgid)
  [ -n "$PG" ] && kill -TERM -- "-$PG" 2>/dev/null
  for i in $(seq 1 60); do kill -0 "$BENCH" 2>/dev/null || break; sleep 2; done
  kill -0 "$BENCH" 2>/dev/null && [ -n "$PG" ] && kill -KILL -- "-$PG" 2>/dev/null
  pkill -KILL -u runner 2>/dev/null
}
# the cost bound: at twice the estimate, counted from this script's start, the timer ends the watch and the run -- whichever
# is still going, the replay step included -- and leaves a mark that says why
OVER=/root/full-$MACHINE.over; rm -f "$OVER"
REMAIN=$(python3 -c "import time; print(max(1, int($STOP_AT_H * 3600 - (time.time() - $START_S))))")
( sleep "$REMAIN"; touch "$OVER"; pkill -TERM -f "[f]ull_run_stop_watch.py /root/runs/$RUN"; PG=$(bench_pgid); [ -n "$PG" ] && kill -TERM -- "-$PG" ) &
TIMER=$!
COST_STOP="the cost bound: this machine passed twice its estimate (${STOP_AT_H} h), more likely stuck than slow -- stopped and reported (founder ruling 2026-09-23)"
( cd /root/mark && .venv/bin/python packages/platform/scripts/full_run_stop_watch.py "/root/runs/$RUN" ) 2>&1 | while IFS= read -r line; do say "   $line"; done
WRC=${PIPESTATUS[0]}
if [ "$WRC" != 0 ] || [ -f "$OVER" ]; then
  kill "$TIMER" 2>/dev/null
  end_bench
  [ -f "$OVER" ] && stop "$COST_STOP"
  # the stop rule: this machine's run ends here, cleanly, and nothing from it is counted until its cells rerun
  stop "the stop rule (watch exit $WRC) -- this machine's run ended; its cells rerun after the cause is fixed (a code fix is a new freeze and all five rerun)"
fi
wait "$BENCH"   # replay fidelity runs after results.json, inside run.sh full
kill "$TIMER" 2>/dev/null
[ -f "$OVER" ] && { end_bench; stop "$COST_STOP"; }
say "   run closed: $(tail -3 "/root/$RUN.out" | tr '\n' ' ' | cut -c1-200)"

say "7/7 export and checksum"
WORK=/root bash /root/mark/packages/platform/pod/run.sh export "$RUN" >> "$L" 2>&1 || stop "export"
# the bundle as exported, and the host record beside it as its own entry: nothing is added to the bundle's own files
tar -czf "/root/$RUN.tgz" -C /root/results "$RUN" -C /root/runs "host-$MACHINE.json" || stop "tar"
( cd /root && sha256sum "$RUN.tgz" > "$RUN.tgz.sha256" )
say "   $(cat "/root/$RUN.tgz.sha256")"
say "DONE $RUN"
