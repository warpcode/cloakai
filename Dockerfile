# syntax=docker/dockerfile:1
# ==============================================================================
# cloakai root image: llama.cpp router (Vulkan passthrough) + LiteLLM mode proxy
# ------------------------------------------------------------------------------
# One resident copy of every model in models.ini, many request-time aliases:
# six-rung reasoning ladder per thinking family:
#     <group>-fast    thinking off (budget 0, effort none, enable_thinking false)
#     <group>-low     budget 256   (effort low, enable_thinking true)
#     <group>-medium  budget 2048  (effort medium, enable_thinking true)
#     <group>-high    budget 4096  (effort high, enable_thinking true)
#     <group>-vhigh   budget 8192  (effort high, enable_thinking true)
#     <group>-max     budget 16384 (effort high, enable_thinking true)
# Configurations for thinking and effort levels are injected by LiteLLM via
# extra_body routing to the SAME underlying model in llama-server (openai/<group>).
# Switching between effort levels (e.g. low vs medium) never unloads or duplicates models.
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
      libgomp1 \
      libvulkan1 \
      mesa-vulkan-drivers \
      vulkan-tools \
 && rm -rf /var/lib/apt/lists/*

# Latest llama.cpp server binary (official Ubuntu Vulkan x64 release asset).
# The release asset can be tar.gz or zip and contains shared libraries alongside binaries.
RUN set -eux; \
    if [ "${LLAMA_TAG}" = "latest" ]; then \
        api_url="https://api.github.com/repos/ggml-org/llama.cpp/releases"; \
        url="$(curl -fsSL "$api_url" \
          | jq -r '[.[] | select(.assets | length > 5)] | .[0].assets[].browser_download_url \
                   | select(test("ubuntu.*vulkan.*x64.*\\.(tar\\.gz|zip)$"))' \
          | head -n 1)"; \
    else \
        api_url="https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/${LLAMA_TAG}"; \
        url="$(curl -fsSL "$api_url" \
          | jq -r '.assets[].browser_download_url \
                   | select(test("ubuntu.*vulkan.*x64.*\\.(tar\\.gz|zip)$"))' \
          | head -n 1)"; \
    fi; \
    test -n "$url"; \
    echo "llama.cpp asset: ${url}"; \
    mkdir -p /tmp/llama; \
    if echo "$url" | grep -q '\.tar\.gz$'; then \
        curl -fsSL "$url" | tar -xz -C /tmp/llama; \
    else \
        curl -fsSL "$url" -o /tmp/llama.zip; \
        unzip -q /tmp/llama.zip -d /tmp/llama; \
        rm -f /tmp/llama.zip; \
    fi; \
    find /tmp/llama -name "llama-server" -exec install -m 0755 {} /usr/local/bin/llama-server \; ; \
    find /tmp/llama -name "*.so*" -exec cp -a {} /usr/local/lib/ \; ; \
    find /tmp/llama -name "*.so*" -exec cp -a {} /usr/local/bin/ \; ; \
    ldconfig; \
    rm -rf /tmp/llama; \
    llama-server --version; \
    vulkaninfo --summary | head -n 40 || true

# LiteLLM proxy: mode aliasing + per-request extra_body injection.
# prisma is required by LiteLLM's auth exception handler; without it, 401s crash as 500s.
RUN pip install --no-cache-dir "litellm[proxy]" prisma

# Fleet recipe + generator + shepherd (see infra/llama-server/).
WORKDIR /app
COPY models.ini /app/models.ini
COPY infra/llama-server/make_litellm_config.py /app/make_litellm_config.py
COPY infra/llama-server/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/make_litellm_config.py

# HF/GGUF cache lives on a bind mount so model blobs survive rebuilds and are
# shared with any host-side llama-server usage of the same cache directory.
ENV GGML_BACKEND_PATH=/usr/local/lib/libggml-vulkan.so \
    LLAMA_CACHE=/models \
    HF_HOME=/models \
    MODELS_PRESET=/app/models.ini \
    MODELS_MAX=1

RUN mkdir -p /models

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:8080/health/liveliness" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
# Extra llama-server args can be appended via `docker ... <args>` / compose `command:`.
CMD []
