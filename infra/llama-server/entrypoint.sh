#!/usr/bin/env bash
# ==============================================================================
# cloakai container shepherd: one llama-server router (Vulkan) + LiteLLM mode
# proxy. The router loads each models.ini section on first request and keeps it
# resident; LiteLLM serves <group>-low/medium/high aliases that hit the same
# process with per-request overrides, so models are never duplicated/unloaded.
# ==============================================================================
set -euo pipefail

MODELS_PRESET="${MODELS_PRESET:-/app/models.ini}"
LLAMA_PORT="${LLAMA_PORT:-8082}"
LITELLM_PORT="${LITELLM_PORT:-8080}"
MODELS_MAX="${MODELS_MAX:-1}"
API_BASE="http://127.0.0.1:${LLAMA_PORT}"

export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-cloak-local}"

python3 /app/make_litellm_config.py \
    --models-preset "${MODELS_PRESET}" \
    --api-base "${API_BASE}" \
    --output /tmp/litellm.yaml

# Vulkan passthrough sanity check: fail fast (mirror of run.sh's guard) if the
# device did not make it into the container.
if ! llama-server --list-devices 2>/dev/null | grep -q "Vulkan"; then
    echo "[cloakai] ERROR: no Vulkan device visible - is /dev/dri passed" >&2
    echo "[cloakai] through and this user in the ${RENDER_GID:-render} group?" >&2
    exit 1
fi

echo "[cloakai] llama-server router on :${LLAMA_PORT} (max ${MODELS_MAX} resident)"
llama-server \
    --models-preset "${MODELS_PRESET}" \
    --models-max "${MODELS_MAX}" \
    --host 0.0.0.0 \
    --port "${LLAMA_PORT}" \
    --no-webui \
    "$@" &
llama_pid=$!

echo "[cloakai] waiting for router health..."
ready=""
for _ in $(seq 1 120); do
    if curl -fsS "${API_BASE}/health" >/dev/null 2>&1; then
        ready=1
        break
    fi
    if ! kill -0 "${llama_pid}" 2>/dev/null; then
        echo "[cloakai] ERROR: llama-server exited during startup" >&2
        exit 1
    fi
    sleep 1
done
test -n "${ready}"

echo "[cloakai] LiteLLM mode proxy on :${LITELLM_PORT} (key: ${LITELLM_MASTER_KEY})"
litellm \
    --config /tmp/litellm.yaml \
    --host 0.0.0.0 \
    --port "${LITELLM_PORT}" &
litellm_pid=$!

shutdown() {
    kill -TERM "${llama_pid}" "${litellm_pid}" 2>/dev/null || true
}
trap shutdown TERM INT

# First of the two to die takes the container down (init:true reaps orphans).
wait -n "${llama_pid}" "${litellm_pid}"
status=$?
shutdown
wait 2>/dev/null || true
exit "${status}"
