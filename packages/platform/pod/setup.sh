#!/usr/bin/env bash
# Pod bootstrap for the Probe Runtime (Task 1.1). Idempotent. Run as root on a pod created from the pinned base
# image (see Dockerfile). Offline mode: the repo arrives as /workspace/mark.bundle (pod/sideload.sh); models and
# targets are on the network volume. Nothing here needs a GitHub or Hugging Face credential.
#   bash /workspace/platform-setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
WORK=${WORK:-/workspace}
RUNS=${RUNS:-/root/runs}
# package caches on THIS pod's disk: the Runpod base image points UV_CACHE_DIR and PIP_CACHE_DIR at /workspace,
# and three pods sharing one network volume then contend for the same wheel locks (torch download timed out on
# the OpenHands pod, decisive run 2026-09-12)
export UV_CACHE_DIR="$WORK/.cache/uv" PIP_CACHE_DIR="$WORK/.cache/pip"
# the isolation probe's cache-writability check uses HF_HOME; without this it touched the network volume and
# hung in a FUSE wait for 24 minutes when the volume stalled under three pods (2026-09-12)
export WORK HF_HOME="$WORK/hf"
OTELCOL_VERSION=${OTELCOL_VERSION:-0.128.0}
TEMPO_VERSION=${TEMPO_VERSION:-2.8.1}
VLLM_VERSION=${VLLM_VERSION:-0.29.0}

echo "== base image"
cat /etc/os-release | head -2
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || echo "no GPU visible"
# Record what the pod was created from: MARK_IMAGE_REF is set by the creator in the pod's env (the digest line
# from packages/platform/pod/IMAGE for a pinned pod, or the tag for a rehearsal); the manifest records that and
# nothing else. Runpod does not expose the image reference itself.
IMAGE_REF=${MARK_IMAGE_REF:-${RUNPOD_IMAGE:-${IMAGE:-unknown}}}
mkdir -p "$RUNS" /etc/platform
echo "$IMAGE_REF" > /etc/platform/image-ref

# The tools come from the image, verified there against their publishers' checksums and signatures (pod/Dockerfile,
# attempt 4, 2026-09-21). This script installs NONE of them. Until that rewrite it fetched node and uv by piping
# unpinned installers into a shell and extracted two release tarballs nobody verified -- guarded by `command -v`, so
# they only fired on a pod that was not created from the image, which is exactly the pod whose provenance nobody can
# state. A missing tool now stops the run and names the image the pod should have been made from; it never downloads
# a replacement. A pod that was not created from the pinned image is not the instrument.
echo "== tools from the image (installed and verified at build time, never here)"
missing=""
# capsh (libcap2-bin) and unshare/runuser (util-linux) are what the isolation probe MEASURES; ninja (ninja-build)
# is what vLLM's JIT execs from PATH; ps (procps) is what the process-tree reader uses. A pod missing one of these
# does not fail at boot, it fails later as a wrong measurement, which is worse.
for t in node pnpm uv otelcol-contrib tempo git curl jq bwrap firejail iptables sudo capsh unshare runuser ninja ps; do
  command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done
if [ -n "$missing" ]; then
  cat >&2 <<EOF
FATAL: this pod is missing:$missing

setup.sh does not install them. They are built into the platform-runtime image, each verified against its
publisher's checksum or signature; fetching replacements here would produce a machine no manifest describes.

Create the pod from the pinned image in packages/platform/pod/IMAGE:
  $(cat "$REPO_DIR/packages/platform/pod/IMAGE" 2>/dev/null || echo "(pod/IMAGE not readable from $REPO_DIR)")

This pod reports its image as: $IMAGE_REF
EOF
  exit 1
fi
echo "   node $(node -v), pnpm $(pnpm -v), uv $(uv --version | cut -d' ' -f2), otelcol-contrib and tempo present"

echo "== pod environment file: no injected credentials"
# Runpod writes the pod's environment into /etc/rp_environment, sourced by every login shell, and injects
# RUNPOD_API_KEY (an account-format key) into it. tests/env caught it in the runner's login environment on the
# decisive-run pods (2026-09-12). Secret-shaped variables are removed and the file is root-only; nothing on the
# pod needs them (the pod never calls the Runpod API).
if [ -f /etc/rp_environment ]; then
  sed -i -E '/^export [A-Z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*=/d' /etc/rp_environment
  chmod 600 /etc/rp_environment
fi
unset RUNPOD_API_KEY 2>/dev/null || true

echo "== runner user (non-root probe execution)"
id runner >/dev/null 2>&1 || useradd --create-home --shell /bin/bash runner
# Secrets and model write paths are root-only; the runner can read models, never write them.
mkdir -p /etc/platform/secrets && chmod 700 /etc/platform/secrets
echo "canary-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')" > /etc/platform/secrets/canary.txt
chmod 600 /etc/platform/secrets/canary.txt
mkdir -p "$WORK/hf" "$WORK/targets" "$WORK/results"
chmod 755 "$WORK/hf" "$WORK/targets"; chmod 700 "$WORK/results"

echo "== checkout"
if [ -f "$WORK/mark.bundle" ]; then
  if [ -d "$WORK/mark/.git" ]; then
    git -C "$WORK/mark" fetch -q "$WORK/mark.bundle" main && git -C "$WORK/mark" reset -q --hard FETCH_HEAD
  else
    git clone -q -b main "$WORK/mark.bundle" "$WORK/mark"
  fi
else
  echo "no $WORK/mark.bundle: run pod/sideload.sh from the laptop first"; exit 1
fi
cd "$WORK/mark"
git config user.name platform-pod; git config user.email platform@pod.invalid
# The runner needs to read the checkout and the locked environment, never write them.
chown -R root:root "$WORK/mark"; chmod -R o+rX "$WORK/mark"
# `markcall` (the instrumented tool boundary for shell-executing agents) on the stock PATH: the Runpod image's
# root .bashrc re-exports PATH from /etc/rp_environment, so an interactive shell (OpenHands' tmux pane) loses the
# venv's bin directory. /usr/local/bin is on every shell's PATH, root or runner.
ln -sfn "$WORK/mark/.venv/bin/markcall" /usr/local/bin/markcall

echo "== python environment (locked; pod extras)"
uv sync --frozen --extra agents --extra agt --extra openhands
echo "== vLLM (own environment: it pins its own torch)"
if [ ! -x "$WORK/vllm-venv/bin/vllm" ]; then
  rm -rf "$WORK/vllm-venv"   # a half-made environment from an interrupted install is replaced, not reused (uv refuses an existing dir)
  uv venv "$WORK/vllm-venv" --python 3.12 -q
  # The PyPI wheel of this vLLM version carries a CUDA 13 torch and refuses a CUDA 12.x driver ("driver too old",
  # H100 host with driver 570 on 2026-09-12). On such a host the SAME version's +cu129 release wheel with a cu129
  # torch is installed instead; the manifest records the driver and the engine version either way.
  HOST_CUDA=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1)
  if [ -n "$HOST_CUDA" ] && [ "$HOST_CUDA" -lt 580 ]; then
    echo "   driver $HOST_CUDA.x (< CUDA 13): installing vllm ${VLLM_VERSION} +cu129 with a cu129 torch"
    VIRTUAL_ENV="$WORK/vllm-venv" uv pip install -q --torch-backend=cu129 "vllm @ https://github.com/vllm-project/vllm/releases/download/v${VLLM_VERSION}/vllm-${VLLM_VERSION}+cu129-cp38-abi3-manylinux_2_28_x86_64.whl"
  else
    VIRTUAL_ENV="$WORK/vllm-venv" uv pip install -q "vllm==${VLLM_VERSION}"
  fi
fi
"$WORK/vllm-venv/bin/vllm" --version

echo "== postgres (single node, for the ledger's future SQL index; the file ledger is authoritative in this pass)"
service postgresql start >/dev/null 2>&1 || true
su postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='platform'\"" | grep -q 1 || su postgres -c "psql -c \"CREATE ROLE platform LOGIN PASSWORD 'platform'\"" >/dev/null
su postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='platform'\"" | grep -q 1 || su postgres -c "createdb -O platform platform" >/dev/null

echo "== model: pinned snapshot, verified against the list in the repo"
bash "$WORK/mark/packages/platform/pod/model.sh"

echo "== sandbox capability probe"
bash "$WORK/mark/packages/platform/pod/sandbox.sh" --probe || true

echo
echo "ready. next:"
echo "  bash $WORK/mark/packages/platform/pod/run.sh services     # collector, tempo, vLLM"
echo "  bash $WORK/mark/packages/platform/pod/run.sh env-test     # pytest tests/env"
echo "  bash $WORK/mark/packages/platform/pod/run.sh first        # calibration + ks.latency/ks.completeness"
