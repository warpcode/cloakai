# An MCP client, for tests only.
#
# NOT part of the product. The product is the agent images; this exists only so a
# test can speak MCP to one of them the way a real client would. It carries no
# agent, no policy, and nothing an image depends on.
#
# It is a separate image from the agent images because those are deliberately
# minimal and this needs a Python MCP client. It used to be the gateway's image,
# which is the one thing that is now gone: the gateway was optional and removing
# it should not have removed the ability to test that it is unnecessary.
FROM python:3.12-slim

# docker-cli, not docker.io: on Debian slim `docker.io` installs only docker-init
# and no client at all, so the probe started and then failed with
# FileNotFoundError: 'docker'.
#
# The CLI plus the mounted socket is what lets this container spawn the agent
# image, which is the thing under test.
RUN apt-get update \
 && apt-get install -y --no-install-recommends docker-cli ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "mcp>=2"

WORKDIR /srv
COPY tests/ /srv/tests/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python", "-m", "tests.direct_use"]