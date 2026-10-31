# syntax=docker/dockerfile:1
# ==============================================================================
# cloakai root image: llama.cpp router (Vulkan passthrough) + LiteLLM mode proxy
# ------------------------------------------------------------------------------
# One resident copy of every model in models.ini, many request-time aliases:
#     <group>-low     thinking off (fast lane)
#     <group>-medium  thinking on, vendor default effort
#     <group>-high    thinking on, high effort
# Modes are injectedLiteLLM as per-request fields routed to the SAME
# llama-server process, so the core LLM is never duplicated or unloaded.
#
# Container run (see docker-compose.yml):
#   docker compose up --build
#   RENDER_GID=$(stat -c %g /dev/dri/renderD128) docker compose up --build
#
# Health / probing:
#   curl http://localhost:8080/health/liveliness
#   curl -fsS http://localhost:8082/health   (router, internal)
#
# NOTE: the run.sh thermal guard (boost/EPP/OD clock/timeout_period sysfs
# writes) stays HOST-side; a container cannot (and should not) reparent the
# desktop session's hardware policy. Keep running ./run.sh guard rails on the
# host box if you want them while the server lives in Docker.
# ==============================================================================

# python slim base: LiteLLM proxy needs pip; distro is Debian bookworm so the
# official llama.cpp Ubuntu Vulkan x64 release asset's glibc/ABI assumptions hold.
FROM python:3.12-slim-bookworm

# Pin with a tag name if reproducibility is ever needed, e.g. b11968.
# "latest" follows the newest ggml-org/llama.cpp GitHub release.
ARG LLAMA_TAG=latest

# Vulkan runtime: loader + RADV (AMD APU iGPU passthrough via /dev/dri).
# vulkan-tools ships `vulkaninfo` for in-container GPU sanity checks.
# jq drives the GitHub release-asset lookup without brittle grep chains.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      jq \
      unzip \
      libvulkan1 \
      mesa-vulkan-drivers \
      vulkan-tools \
 && rm -rf /var/lib/apt/lists/*

# Latest llama.cpp server binary (official Ubuntu Vulkan x64 release asset).
# The release zip layout has changed before, so resolve the binary by find().
RUN set -eux; \
    url="$(curl -fsSL "https://api.github.com/repos/ggml-org/llama.cpp/releases/${LLAMA_TAG}" \
      | jq -r '[.assets[].browser_download_url] \
               | map(select(test("ubuntu.*vulkan.*x64.*\\.zip$"))) \
               | .[0] // empty')"; \
    test -n "$url"; \
    echo "llama.cpp asset: ${url}"; \
    curl -fsSL "$url" -o /tmp/llama.zip; \
    unzip -q /tmp/llama.zip -d /tmp/llama; \
    install -m 0755 "$(find /tmp/llama -type f -name llama-server -print -quit)" \
        /usr/local/bin/llama-server; \
    rm -rf /tmp/llama /tmp/llama.zip; \
    llama-server --version; \
    vulkaninfo --summary | head -n 40 || true

# LiteLLM proxy: mode aliasing + per-request extra_body injection.
RUN pip install --no-cache-dir "litellm[proxy]"

# Fleet recipe + generator + shepherd (see infra/llama-server/).
WORKDIR /app
COPY models.ini /app/models.ini
COPY infra/llama-server/make_litellm_config.py /app/make_litellm_config.py
COPY infra/llama-server/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/make_litellm_config.py

# HF/GGUF cache lives on a bind mount so model blobs survive rebuilds and are
# shared with any host-side llama-server usage of the same cache directory.
ENV LLAMA_CACHE=/data/llama-cache \
    MODELS_PRESET=/app/models.ini \
    LLAMA_PORT=8082 \
    LITELLM_PORT=8080 \
    MODELS_MAX=1

EXPOSE 8080 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${LITELLM_PORT}/health/liveliness" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
# Extra llama-server args can be appended via `docker ... <args>` / compose `command:`.
CMD []
