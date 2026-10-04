#!/usr/bin/env bash
# Pod-side entry points for the Probe Runtime.
#   run.sh services      start the collector, Tempo and vLLM (pinned model from $WORK/hf, offline)
#   run.sh env-test      pytest tests/env (image boots, vLLM serves, collector accepts, postgres, sandbox)
#   run.sh calibrate [-n N]   the known-latency scenario on its own, under serving load (3 clean, or replace the pod)
#   run.sh smoke <probe> --target t --control c --workload w [-n N]   one cell, informational, never signed
#   run.sh first         benchmarks/first-session.yaml (the pipeline proof), signed manifest
#   run.sh full --matrix oss-agent-controls-v1 [--replications 20]   the benchmark matrix
#   run.sh export <run>  verify the ledger and copy /root/runs/<run> to /workspace/results/<run> (unsigned; sign on the laptop)
#   run.sh full --targets langgraph-ref   one target per pod, for parallel runs on faster cards
set -euo pipefail
# Nothing started from here inherits the host's identifiers or a credential (incident 2026-09-12: a shell that had
# sourced Runpod's injected RUNPOD_API_KEY before setup.sh stripped it passed the key to the env test). Names only.
for _v in $(compgen -e); do case "$_v" in RUNPOD_*|*API_KEY*|*APIKEY*|*SECRET*|*TOKEN*|*PASSWORD*|*PASSWD*|*PRIVATE_KEY*|*CREDENTIAL*|*ACCESS_KEY*) unset "$_v" ;; esac; done
MODE=${1:-}
WORK=${WORK:-/workspace}
RUNS=${RUNS:-/root/runs}
# exported bundles always go to the network volume, even when WORK is the pod's own container disk (parallel
# pods sharing one volume: pod/localize.sh); the volume outlives the pod
VOL=${VOL:-/workspace}
RESULTS=${RESULTS:-$VOL/results}
# vllm-venv/bin first: vLLM's JIT (torch cpp_extension) execs `ninja` from PATH; setup.sh also installs ninja-build system-wide.
export PATH="$WORK/vllm-venv/bin:$HOME/.local/bin:$PATH"
export HF_HUB_OFFLINE=1 HF_HOME="$WORK/hf" PYTHONUNBUFFERED=1
export MARK_LLM_URL=${MARK_LLM_URL:-http://127.0.0.1:8000/v1}
export MARK_LLM_MODEL=${MARK_LLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}
# Everything on the serving side that shapes the model's output, defined once (founder ruling 2026-09-12): vLLM is
# started with exactly this, and the runner records it together with the arguments of the process actually running
# and what the live server reports. Attempt 2 ran with these values and recorded none of them.
# The serving condition (founder ruling 2026-09-12, after the Q3 replays): max_num_seqs changed token paths at concurrency 1,
# so it is passed explicitly (1024, the value that applied by default on attempt 2's H100s) and the engine log states it;
# prefix caching is off for measurement runs, because cache contents depend on every earlier request.
# --max-model-len 32768 (single-call arm rule set 2, 2026-09-12): the model's native maximum; attempt 2's 8192 was reached by
# OpenHands' single-call conversations in most none replications.
# Freeze-2 (2026-09-14): the arguments are pinned per model in model-env.sh (Qwen's are unchanged), and so is the snapshot path.
source "${MARK_POD_DIR:-$WORK/mark/packages/platform/pod}/model-env.sh"
if [ -z "${MARK_SERVING_ARGS:-}" ]; then MARK_SERVING_ARGS=$(serving_args_for "$MARK_LLM_MODEL" || true); fi
export MARK_SERVING_ARGS
# freeze-3: the cache is the served name's repository (gpt-oss-120b is served unprefixed, from openai/gpt-oss-120b)
MODEL_CACHE=$(model_cache_dir "$WORK" "$(model_repo_for "$MARK_LLM_MODEL" || echo "$MARK_LLM_MODEL")")
# the engine's own startup log: the runner reads the effective condition from it (serving.engine_log_facts)
# The engine log carries the SERVED MODEL in its name, so serving a second arm cannot truncate the first arm's
# log -- which is the evidence its serving pin was read from. Until 2026-09-22 this was a fixed vllm.log, and the
# comment below claimed a restart never overwrites; that held only for the enforce-eager arm, which passes its own
# path. Reading the capable arm's pins and then serving the small arm destroyed the capable arm's log.
# The run directory owns run artifacts. Nothing a test writes belongs in the checkout: a test that dirties the
# tree blocks the matrix at the moment it starts, because a bench run refuses a dirty checkout.
export MARK_RUNS="$RUNS"

# **The clock must not queue behind inference** (founder ruling 2026-09-22). Calibration failed twice, both times
# while a smoke was running, and passed six times out of six when asked on its own. Measured on the pod: the
# container sees 208 CPUs on a 22.1-core cgroup quota, vLLM runs ~221 threads inside it, and hard throttling is
# rare (47 throttled periods in 461,843, pressure 0.00 at every window). So the delay is run-queue contention, not
# quota exhaustion -- the harness's timing thread waiting behind vLLM's threads on a shared core. Affinity
# separation is the cure for exactly that; it would not have cured quota exhaustion, which is why it was measured
# rather than assumed.
#
# This matters because ks.latency judges some controls on a 250 ms threshold. A scheduler that can delay the
# harness by 30 ms under load can move a latency reading across that line, so the clock being trustworthy DURING
# the run is what the latency verdicts rest on.
export MARK_SERVING_CPUS=${MARK_SERVING_CPUS:-0-17}
export MARK_HARNESS_CPUS=${MARK_HARNESS_CPUS:-18-21}
# a split that overlaps is not a split; refuse rather than run with the clock back on shared cores
if command -v taskset >/dev/null 2>&1; then
  _s=$(taskset -c "$MARK_SERVING_CPUS" true 2>&1) || { echo "REFUSING: MARK_SERVING_CPUS=$MARK_SERVING_CPUS is not a usable cpu list: $_s"; exit 1; }
  _h=$(taskset -c "$MARK_HARNESS_CPUS" true 2>&1) || { echo "REFUSING: MARK_HARNESS_CPUS=$MARK_HARNESS_CPUS is not a usable cpu list: $_h"; exit 1; }
  HARNESS_PIN="taskset -c $MARK_HARNESS_CPUS"
  SERVING_PIN="taskset -c $MARK_SERVING_CPUS"
else
  echo "REFUSING: taskset is absent; the harness cannot be separated from the model server, and the pod image installs util-linux for this"; exit 1
fi
export MARK_SERVING_LOG=${MARK_SERVING_LOG:-$RUNS/vllm-${MARK_LLM_MODEL//\//-}.log}
# Every per-model artifact is named by its model (founder ruling 2026-09-22). A pod that still carries the old
# unnamed files stops here rather than reading one: the rename is not trusted to have reached every writer, and an
# un-suffixed file is either a stale artifact or a writer that was missed. Both are refusals, not warnings.
for _legacy in model-sha256.txt model-hash.txt pinned-paths.txt; do
  [ -e "$WORK/hf/$_legacy" ] && { echo "REFUSING: $WORK/hf/$_legacy is a per-model artifact at a fixed path. Two models on one pod overwrite it, and model-hash.txt reaches the signed manifest as pins.model.hash. Re-run pod/model.sh for each model, then delete it."; exit 1; }
done
MODEL_FILES=$(model_files_prefix "$WORK" "$(model_repo_for "$MARK_LLM_MODEL" || echo "$MARK_LLM_MODEL")")
export MARK_MODEL_HASH=${MARK_MODEL_HASH:-$(cat "$MODEL_FILES-model-hash.txt" 2>/dev/null || echo "")}
# post-run integrity check of the served weights (the cache is runner-writable on this pod's FUSE volume)
export MARK_MODEL_SHA_LIST="$MODEL_FILES-model-sha256.txt"
export MARK_MODEL_SNAPSHOT=$(ls -d "$MODEL_CACHE"/snapshots/*/ 2>/dev/null | head -1)
# the image pin: ONLY what the pod was actually created from. The creator sets MARK_IMAGE_REF in the pod's env
# (create-pod env) to the reference it used; setup.sh copies it to /etc/platform/image-ref. The repo's pod/IMAGE
# file is what the creator SHOULD use, never what the manifest records: a pod created from a tag records the tag.
export MARK_IMAGE_DIGEST=${MARK_IMAGE_DIGEST:-${MARK_IMAGE_REF:-$(cat /etc/platform/image-ref 2>/dev/null || echo unknown)}}
# the platform manifest digest (the image bits, independent of the index and any attestation beside it); written
# to /etc/platform/image-platform-digest by the operator from the registry's manifest list for the pinned image
export MARK_IMAGE_PLATFORM_DIGEST=${MARK_IMAGE_PLATFORM_DIGEST:-$(cat /etc/platform/image-platform-digest 2>/dev/null || echo unknown)}
export OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-http://127.0.0.1:4318}
# the collector's file exporter (pod/otel-collector.yaml): scanned after every cell for spans from any
# instrumentation scope other than the harness's (single-instrument precondition)
export MARK_COLLECTOR_ARCHIVE=${MARK_COLLECTOR_ARCHIVE:-$RUNS/otel-spans.jsonl}
export MARK_SANDBOX_CMD=${MARK_SANDBOX_CMD:-"bash $WORK/mark/packages/platform/pod/sandbox.sh --"}
cd "$WORK/mark"
mkdir -p "$RUNS"

# vLLM with the serving arguments plus any extra ones, logging to its own file: a restarted server never overwrites the
# log of the server a run was measured under (never rewrite evidence).
start_vllm() {
  local log=$1; shift
  local snap
  [ -n "$MARK_SERVING_ARGS" ] || { echo "no pinned serving arguments for $MARK_LLM_MODEL (model-env.sh): refusing to start vLLM"; return 1; }
  snap=$(ls -d "$MODEL_CACHE"/snapshots/*/ | head -1)
  (nohup setsid $SERVING_PIN "$WORK/vllm-venv/bin/vllm" serve "$snap" --served-model-name "$MARK_LLM_MODEL" --host 127.0.0.1 --port 8000 $MARK_SERVING_ARGS "$@" > "$log" 2>&1 < /dev/null &)
}
wait_vllm() {
  echo "waiting for vLLM"
  for _i in $(seq 1 120); do curl -sf "$MARK_LLM_URL/models" >/dev/null && return 0; sleep 5; done
  echo "vLLM did not come up"; return 1
}
stop_vllm() {
  pkill -f "[v]llm serve" || true
  for _i in $(seq 1 90); do pgrep -f "[v]llm serve" >/dev/null || return 0; sleep 2; done
  echo "vLLM did not stop"; return 1
}
# Tier B: the agent runs as `runner` and must traverse into its scenario directory under /root/runs (711 = traverse only;
# everything else under /root keeps its own 600/700 modes; secrets live in /etc/platform/secrets anyway).
chmod 711 /root "$RUNS"

case "$MODE" in
  services)
    # The collector and Tempo are the harness's trace store, not instruments: they must not see the OTLP endpoint
    # variables, or their own Go OTel SDKs export self-telemetry into the archive (Tempo wrote 32,289 spans as
    # service `tempo-all` on the decisive run, 2026-09-12; the single-instrument scan counted every one).
    pgrep -f otelcol-contrib >/dev/null || (nohup setsid env -u OTEL_EXPORTER_OTLP_ENDPOINT -u OTEL_EXPORTER_OTLP_TRACES_ENDPOINT otelcol-contrib --config packages/platform/pod/otel-collector.yaml > "$RUNS/otelcol.log" 2>&1 &)
    pgrep -f "tempo -config" >/dev/null || (nohup setsid env -u OTEL_EXPORTER_OTLP_ENDPOINT -u OTEL_EXPORTER_OTLP_TRACES_ENDPOINT tempo -config.file packages/platform/pod/tempo.yaml > "$RUNS/tempo.log" 2>&1 &)
    if ! pgrep -f "[v]llm serve" >/dev/null; then
      start_vllm "$MARK_SERVING_LOG"
    fi
    wait_vllm
    curl -s "$MARK_LLM_URL/models" | jq -c '.data[].id'
    ;;
  env-test)
    # The calibration rule (founder ruling 2026-09-13) lives in env-test.sh, where it is testable off the pod; since fix A7
    # it lists every attempt and names the latest in $RUNS/env-test-latest for `full` to hand to the run record.
    exec env RUNS="$RUNS" bash packages/platform/pod/env-test.sh
    ;;
  calibrate)
    # `calibrate [-n N]`: the known-latency scenario on its own, under whatever the host is doing.
    #
    # The founder's rule from attempt 3: three clean calibration runs, or replace the pod. It exists for the
    # reading it caught on 2026-09-22 -- one sleep in five overshooting by 20 ms while the host was serving a
    # model. The maximum rule stays as written: scheduler contention is the stall class A4 measures, and a
    # calibration blind to the outlier would be blind to the thing a run most needs it to see.
    #
    # It runs with THIS file's environment and under serving load on purpose. Calibrating on an idle host would
    # certify a clock the run never uses.
    shift
    CN="1"; while [ $# -gt 0 ]; do case "$1" in -n) CN=$2; shift 2;; *) echo "unknown argument: $1"; exit 1;; esac; done
    RUN=${RUN:-calibrate-$(date -u +%Y%m%dT%H%M%SZ)}
    echo "== calibrate n=$CN -> $RUNS/$RUN (serving: $(curl -sf "$MARK_LLM_URL/models" | jq -r '.data[].id' 2>/dev/null || echo none))"
    $HARNESS_PIN uv run platform --tools mcp calibrate --run-dir "$RUNS/$RUN" --run-id "$RUN" -n "$CN" 2>&1 | tee "$RUNS/$RUN.log"
    ;;

  smoke)
    # `smoke <probe> --target t --control c --workload w [-n N]`: one cell, informational, never a measurement.
    #
    # Phase 0 step 4 requires smokes, and until 2026-09-22 this file had no way to run one -- so attempt 3's smokes
    # were produced by a command that exists nowhere in the repo, and the pre-registration cited runs nobody can
    # reissue. A smoke that shapes a pre-registered bound has to be as reproducible as the run it shapes, and it has
    # to carry THIS file's environment: the served model and its log, the offline cache, the collector archive, the
    # sandbox command, the image digest. Assembling that by hand for a smoke is how a smoke's condition and a run's
    # condition drift apart while both look right.
    #
    # It takes the bench lock too: a smoke and a matrix sharing the GPU are each other's noise.
    LOCK=${LOCK:-/run/mark-bench.lock}
    exec 9>"$LOCK" || { echo "cannot open $LOCK"; exit 1; }
    if ! flock -n 9; then
      echo "REFUSING: another run holds $LOCK on this pod (pid $(cat "$LOCK" 2>/dev/null || echo '?')). One cell at a time."
      exit 75
    fi
    echo $$ >&9
    shift
    PROBE=${1:?usage: run.sh smoke <probe> --target t --control c --workload w [-n N]}; shift
    STARGET=""; SCONTROL=""; SWORKLOAD=""; SN="20"
    while [ $# -gt 0 ]; do case "$1" in
      --target) STARGET=$2; shift 2;;
      --control) SCONTROL=$2; shift 2;;
      --workload) SWORKLOAD=$2; shift 2;;
      -n) SN=$2; shift 2;;
      *) echo "unknown argument: $1"; exit 1;;
    esac; done
    [ -n "$STARGET" ] && [ -n "$SCONTROL" ] && [ -n "$SWORKLOAD" ] || { echo "smoke needs --target, --control and --workload"; exit 1; }
    # the id names the cell and the served model: two smokes differing only in the arm must not share a name
    RUN=${RUN:-smoke-$(echo "$PROBE" | tr '.' '-')-${STARGET}-${SCONTROL}-$(echo "$MARK_LLM_MODEL" | sed 's#.*/##')-$(date -u +%Y%m%dT%H%M%SZ)}
    echo "== smoke $PROBE / $STARGET / $SCONTROL / $SWORKLOAD / n=$SN / $MARK_LLM_MODEL -> $RUNS/$RUN"
    # No signing key and no --sign-key: a smoke is informational and is never signed, whatever MARK_SIGN_ON_POD says.
    $HARNESS_PIN uv run platform --tools mcp probe run "$PROBE" --target "$STARGET" --control "$SCONTROL" --workload "$SWORKLOAD"       -n "$SN" --run-dir "$RUNS/$RUN" --run-id "$RUN" 2>&1 | tee "$RUNS/$RUN.log"
    ;;

  first|full)
    # One matrix per pod. Two runs at once share the GPU, the vLLM server and the run log, so their timings are
    # each other's noise and neither is a measurement. A lock makes that impossible rather than remembered.
    LOCK=${LOCK:-/run/mark-bench.lock}
    exec 9>"$LOCK" || { echo "cannot open $LOCK"; exit 1; }
    if ! flock -n 9; then
      echo "REFUSING: another benchmark run holds $LOCK on this pod (pid $(cat "$LOCK" 2>/dev/null || echo '?')). One matrix per pod."
      exit 75
    fi
    echo $$ >&9
    # `full --matrix <id> [--replications N]` runs benchmarks/<id>.yaml; `first` is benchmarks/first-session.yaml.
    MATRIX=first-session; REPS=""; TARGETS=""; PROBES=""
    if [ "$MODE" = full ]; then
      MATRIX=oss-agent-controls-v1; shift
      while [ $# -gt 0 ]; do case "$1" in --matrix) MATRIX=$2; shift 2;; --replications) REPS="$REPS --replications $2"; shift 2;; --reference-replications) REPS="$REPS --reference-replications $2"; shift 2;; --targets) TARGETS="--targets $2"; shift 2;; --probes) PROBES="--probes $2"; shift 2;; --gates) shift 2;; *) shift;; esac; done
    fi
    # one target per pod: the run id carries the target so three pods started in the same second never collide
    TSUFFIX=""; [ -n "$TARGETS" ] && TSUFFIX="-$(echo "$TARGETS" | sed 's/--targets //; s/,/+/g')"
    # a probe-filtered run says so in its id as well as in its manifest
    PSUFFIX=""; [ -n "${PROBES:-}" ] && PSUFFIX="-only-$(echo "$PROBES" | sed 's/--probes //; s/,/+/g')"
    RUN=${RUN:-${MATRIX}${TSUFFIX}${PSUFFIX}-$(date -u +%Y%m%dT%H%M%SZ)}
    # The pod holds no signing key (founder decision): the run closes with manifest.unsigned.json + the results hash.
    # `pod/fetch.sh` + `platform run sign` on the laptop sign, anchor and render. MARK_SIGN_ON_POD=1 with a key at
    # /etc/platform/secrets/manifest.key signs here instead and the manifest then says signed_on: pod.
    SIGN=""
    if [ "${MARK_SIGN_ON_POD:-0}" = 1 ] && [ -f /etc/platform/secrets/manifest.key ]; then
      SIGN="--sign-key /etc/platform/secrets/manifest.key --cert packages/bundles/keys/certs/$(cat packages/bundles/keys/active-key).json --signed-on pod"
    fi
    # fix A7: the latest env-test's attempts go into the run record; a failed env-test refuses the run before it opens
    ENVTEST=""; [ -f "$RUNS/env-test-latest" ] && ENVTEST="--env-test $(cat "$RUNS/env-test-latest")"
    $HARNESS_PIN uv run platform --tools mcp bench run "benchmarks/$MATRIX.yaml" --run-dir "$RUNS/$RUN" $SIGN $REPS $TARGETS $PROBES $ENVTEST 2>&1 | tee "$RUNS/$RUN.log"
    # Replay fidelity (founder rulings 2026-09-12): every captured first request re-sent, byte-identical and one at a time,
    # to this run's own server before the pod is released. "Replayable" is a measured rate per run, never a design claim;
    # what a rate licenses is pre-registered in the benchmark spec (replay: poor_below, replayable_at).
    FID="$RUNS/$RUN.replay-fidelity.json"
    if uv run platform run replay-fidelity --run-dir "$RUNS/$RUN" --arm run-server > "$FID" 2> "$RUNS/$RUN.replay-fidelity.stderr"; then
      jq -c '{replay_claim, rate, poor, same_condition_as_run, mismatch_classes}' "$FID"
      # The order-dependence arm, only when the run's own server was poor under the run's own condition: restart with
      # --enforce-eager (its own log) and compare two replays in opposite orders with each other.
      if [ "$(jq -r '.poor == true and .same_condition_as_run == true' "$FID")" = true ]; then
        stop_vllm && start_vllm "${MARK_SERVING_LOG%.log}-enforce-eager.log" --enforce-eager && wait_vllm \
          && MARK_SERVING_LOG="${MARK_SERVING_LOG%.log}-enforce-eager.log" uv run platform run replay-order --run-dir "$RUNS/$RUN" --arm enforce-eager > "$RUNS/$RUN.replay-order.json" 2> "$RUNS/$RUN.replay-order.stderr" \
          && jq -c '{order_independent, agreed, compared, enforce_eager_at_replay}' "$RUNS/$RUN.replay-order.json" \
          || echo "replay-order did not record (see $RUNS/$RUN.replay-order.stderr; the server now running is the eager one or none)"
      fi
    else
      echo "replay-fidelity did not record: $(cat "$FID") (see $RUNS/$RUN.replay-fidelity.stderr)"
    fi
    echo "run: $RUNS/$RUN"
    ;;
  export)
    RUN=${2:?run id}
    mkdir -p "$RESULTS"
    uv run platform run export --run-dir "$RUNS/$RUN" --dest "$RESULTS"
    uv run mark-ledger verify "$RESULTS/$RUN/ledger" "$RUN"
    echo "exported. On the laptop:  bash packages/platform/pod/fetch.sh root@<pod-ip> <ssh-port> $RUN   then   uv run platform run sign --run-dir runs/$RUN --key packages/bundles/keys/bundle-<run-manifest-key-id>.key --cert packages/bundles/keys/certs/<run-manifest-key-id>.json --anchor rekor"
    ;;
  *) echo "usage: run.sh services|env-test|calibrate [-n N]|smoke <probe> --target t --control c --workload w [-n N]|first|full [--matrix id] [--replications N] [--targets a,b]|export <run>"; exit 1 ;;
esac
