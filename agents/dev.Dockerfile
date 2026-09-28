# cloakai agent image — two modes off one config, no bespoke code from us.
#
#   run   the harness CLI, with the project directory mounted. The local dev path.
#   mcp   docker agent serve mcp --http, one MCP tool per agent in /agent/agent.yaml
#
# Two Phase 0 findings are baked in here. Both would have broken the build:
#
#   1. The docker-agent image is on Docker Hub, not ghcr.io — ghcr returns 401.
#   2. opencode's installer produces a GLIBC binary, so the base must be glibc.
#      Its /docker-agent binary is statically linked and runs fine on Debian; the
#      reverse (opencode on Alpine) fails with "Error relocating ... symbol not found".
#
# The opencode binary is relocated out of /root/.opencode/ because the agent writes a
# .gitignore into its own data directory at startup, which fails under --read-only.
#
# We ship the GENERATED dist/dev, not plugins/dev, so the image and a locally
# installed plugin are guaranteed identical.

FROM docker/docker-agent:latest AS docker-agent

FROM debian:bookworm-slim

# ripgrep because harnesses expect it; git because agents use it; the rest is TLS
# and a shell. Nothing here reaches the network at run time — the container is on
# an `internal: true` network and egress is granted by adding a network, not a flag.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git ripgrep procps \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-agent /docker-agent /usr/local/bin/docker-agent
RUN chmod +x /usr/local/bin/docker-agent

# The installer needs bash and must not touch shell config inside a container.
RUN curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path \
    && mv /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && rm -rf /root/.opencode

# These make the writable paths land in the four tmpfs mounts scripts/run.sh sets,
# rather than in an unwritable image layer. See docs/verification/phase-0.md Check 3.
ENV XDG_DATA_HOME=/root/.local/share \
    XDG_CONFIG_HOME=/root/.config \
    XDG_CACHE_HOME=/root/.cache \
    OPENCODE_DISABLE_AUTOUPDATE=1

# The compiled plugin. This is what clients consume.
COPY dist/dev /agent

# One dispatcher for both modes, so there is no second image to maintain.
COPY agents/cloakai-entrypoint.sh /usr/local/bin/cloakai-entrypoint
RUN chmod +x /usr/local/bin/cloakai-entrypoint

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/cloakai-entrypoint"]
