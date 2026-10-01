# The big-pickle agent image: opencode inside a container, on the free model.
#
# Structurally identical to dev.Dockerfile — same docker-agent base, same opencode
# install, same isolation flags. The difference is entirely in what agent.json
# declares: the model (opencode/big-pickle, which needs no credential) and the
# network (egress, because opencode.ai/zen is not the internal proxy).
FROM docker/docker-agent:latest AS docker-agent

FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git ripgrep python3 nodejs npm \
 && rm -rf /var/lib/apt/lists/*

COPY --from=docker-agent /docker-agent /usr/local/bin/docker-agent

# The binary is MOVED out of /root/.opencode and that directory removed, for the
# same reason as dev.Dockerfile: the agent writes a .gitignore into its own install
# directory on startup, and under --read-only that fails with
# "FileSystem.writeFile (/root/.opencode/.gitignore)". Leaving the binary in place
# and symlinking to it does not help — the write is to the directory, not the file.
RUN curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path \
    && mv /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && rm -rf /root/.opencode

ENV XDG_DATA_HOME=/root/.local/share \
    XDG_CONFIG_HOME=/root/.config \
    XDG_CACHE_HOME=/root/.cache \
    OPENCODE_DISABLE_AUTOUPDATE=1

ARG PLUGIN=big-pickle
COPY dist/${PLUGIN} /agent
COPY agents/cloakai-entrypoint.sh /usr/local/bin/cloakai-entrypoint

WORKDIR /agent
ENTRYPOINT ["/usr/local/bin/cloakai-entrypoint"]
