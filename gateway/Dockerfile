# The gateway is infrastructure, not an agent, so it is built here rather than
# supplied by a plugin. Agents bring their own Dockerfiles; this one is the thing
# that starts them.
#
# It is the ONLY image in the stack that holds the Docker socket. Agent containers
# run with --cap-drop=ALL and no socket, so an agent cannot start a sibling — which
# is the isolation model's real boundary, not the network.
FROM python:3.12-slim

# docker-cli, NOT docker.io. On Debian slim `docker.io` installs only docker-init
# (tini) and no client at all, so the gateway started and then failed every call
# with FileNotFoundError: 'docker'. `docker-cli` is the client package.
#
# No build tools: this image runs the gateway, it does not compile anything.
RUN apt-get update \
 && apt-get install -y --no-install-recommends docker-cli ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && docker --version

RUN pip install --no-cache-dir "mcp>=1.2"

WORKDIR /srv
COPY gateway/ /srv/gateway/
COPY dist/gateway.json /srv/dist/gateway.json

ENV CLOAKAI_MANIFEST=/srv/dist/gateway.json \
    CLOAKAI_NETWORK=cloakai-internal \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "gateway.server"]
