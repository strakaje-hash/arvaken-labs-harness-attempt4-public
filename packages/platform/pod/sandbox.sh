#!/usr/bin/env bash
# Sandbox wrapper for the agent process (Task 3.2), tiered by the Task 1.4 capability record.
#   sandbox.sh --probe                      the capability record (JSON) via mark_platform.isolation
#   sandbox.sh -- <command...>              run the command as `runner` under the strongest available tier
# Tier A (user namespaces + CAP_NET_ADMIN): bubblewrap + iptables allowlist. Not available on the measured pod.
# Tier B (the measured pod: seccomp filter, no CLONE_NEWUSER, no CAP_NET_ADMIN): runuser as the runner uid,
#   prlimit (processes, open files, address space), an ephemeral working directory (the scenario dir the
#   harness created, writable by runner), no secrets in the environment (only an allowlist of variables is
#   passed), HTTP(S)_PROXY to the harness's allowlist proxy (MARK_EGRESS_PROXY). Best effort: an agent with a
#   shell can unset the proxy variables; the run is labelled egress_control=best_effort.
# The tier used is written to MARK_SANDBOX_REPORT so the manifest records it; a run never claims a tier it did not get.
set -euo pipefail
WORK=${WORK:-/workspace}
REPORT=${MARK_SANDBOX_REPORT:-}
PY=${MARK_PYTHON:-$WORK/mark/.venv/bin/python}

if [ "${1:-}" = "--probe" ]; then "$PY" -c "import json; from mark_platform.isolation import probe; print(json.dumps(probe()))"; exit 0; fi
[ "${1:-}" = "--" ] && shift
CAPS=$("$PY" -c "import json; from mark_platform.isolation import probe; print(json.dumps(probe()))")
TIER=$(echo "$CAPS" | "$PY" -c "import sys,json; print(json.load(sys.stdin)['tier'])")
[ -n "$REPORT" ] && echo "$CAPS" > "$REPORT"
export MARK_SANDBOX="tier-$TIER"
export MARK_EGRESS_CONTROL=$(echo "$CAPS" | "$PY" -c "import sys,json; print(json.load(sys.stdin)['egress_control'])")

# Environment allowlist: nothing secret-shaped reaches the agent. (The harness's own env holds no secrets either,
# but the rule is enforced here, not assumed.) Every MARK_* variable scenario.py hands the agent must be listed
# (tests/test_sandbox_env_allowlist.py): MARK_AWAIT_RESUME_S was not, so fix A1's wait never reached a sandboxed agent.
KEEP="PATH HOME LANG LC_ALL TERM PYTHONUNBUFFERED PYTHONPATH HF_HOME HF_HUB_OFFLINE MARK_SCENARIO_ID OTEL_TRACEPARENT OTEL_BAGGAGE OTEL_EXPORTER_OTLP_ENDPOINT MARK_MOCK_URL MARK_LLM_URL MARK_LLM_MODEL MARK_LLM_API_KEY MARK_WORKDIR MARK_TRACE_JSONL MARK_TRACE_JSONL_SHELL MARK_MOCK_TOKEN MARK_TOOLS MARK_CONTROL_CFG MARK_MAX_STEPS MARK_LINGER_S MARK_AWAIT_RESUME_S MARK_SANDBOX MARK_EGRESS_CONTROL MARK_ALLOW_FALLBACK_CLOCK MARK_TURN_FILE MARK_CHILDREN_FILE OPENHANDS_SUPPRESS_BANNER"
ENVARGS=()
for k in $KEEP; do v=${!k:-}; [ -n "$v" ] && ENVARGS+=("$k=$v"); done
if [ -n "${MARK_EGRESS_PROXY:-}" ]; then
  ENVARGS+=("HTTP_PROXY=$MARK_EGRESS_PROXY" "HTTPS_PROXY=$MARK_EGRESS_PROXY" "http_proxy=$MARK_EGRESS_PROXY" "https_proxy=$MARK_EGRESS_PROXY" "NO_PROXY=" "no_proxy=")
fi
ENVARGS+=("HOME=/home/runner")

case "$TIER" in
  A)
    RUID=$(id -u runner); RGID=$(id -g runner)
    exec env -i "${ENVARGS[@]}" bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --tmpfs /root --bind "$PWD" "$PWD" --tmpfs /etc/platform/secrets --ro-bind "$WORK/hf" "$WORK/hf" --unshare-user --unshare-pid --uid "$RUID" --gid "$RGID" --die-with-parent --new-session -- "$@"
    ;;
  B)
    # Drop to runner with a clean environment, THEN prlimit (256 processes, 4096 files, 16 GiB address space):
    # RLIMIT_NPROC is checked against the calling uid's task count at fork, and root on this pod runs vLLM's
    # hundreds of threads, so a limit applied before the uid switch fails with EAGAIN (measured 2026-09-11).
    exec runuser -u runner -- env -i "${ENVARGS[@]}" prlimit --nproc=256 --nofile=4096 --as=17179869184 "$@"
    ;;
  *)
    echo "no sandbox tier available: $CAPS" >&2; exit 97
    ;;
esac
