# Jules, exposed as an MCP server over stdio.
#
# Same contract as every other cloakai image:
#
#     docker run --rm -e JULES_API_KEY cloakai/jules mcp
#
# This one is deliberately NOT built on the agent base image. Jules is a remote
# API: there is no agent to run locally, so a 1GB docker-agent runtime and its
# model plumbing would be dead weight. It also needs the opposite network shape —
# it must reach jules.googleapis.com, where every agent image is cut off from the
# internet on cloakai-internal. That asymmetry is the point: an agent image is
# isolated *from* the network, a Jules image has to be able to *reach* one
# specific API and nothing else.
#
# The key is never baked in. It is passed per invocation, so the image itself
# holds no credential and can be pushed anywhere.
FROM python:3.12-slim

# curl only, for the healthcheck. No compiler: this image runs four HTTP calls.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "mcp>=2" \
 && useradd --create-home --shell /usr/sbin/nologin --uid 65532 jules

WORKDIR /srv
COPY agents/jules-mcp/ /srv/jules_mcp/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER jules

ENTRYPOINT ["python", "-m", "jules_mcp.server"]
