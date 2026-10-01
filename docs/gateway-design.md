# Gateway

Every agent is a tool. The gateway exposes the agents, starts the right
container when a tool is called, and passes the prompt through. That is the
whole of it.

**The gateway is optional and nothing below is a dependency of the tool.** An
image is usable on its own with `docker run --rm <image> mcp`, and every shipped
client config is generated as stdio for exactly that reason: a client that spawns
the container itself needs no service, no port, and no gateway. This document
describes what the gateway does *if* you run one — either as an MCP proxy or as
dynamic container instantiation. `scripts/check-direct-use.sh` verifies the
gateway-free path, and `scripts/test.sh` runs it, because that claim was true for
several turns while nothing tested it.

```
   client model
        |  MCP: tools/list -> [dev, ...]
        v
   +------------------+     1. read dist/gateway.json
   |     gateway      |     2. mint a scoped proxy key
   |                  |     3. docker run <flags> <image> run <prompt>
   |  holds the      |     4. return what the agent printed
   |  docker socket  |
   +------------------+
        |  starts, one per call
        v
   +------------------+
   |  agent container|   its own Dockerfile, its own tools,
   |  no socket      |   no host mounts, --rm on exit
   +------------------+
```

## Three operations

| File | Does |
|---|---|
| `gateway/dispatch.py` | The logic: list agents as tools, build a `docker run`, run it, return output. |
| `gateway/server.py` | The wire format. Publishes one tool per agent. No logic. |
| `gateway/Dockerfile` | The gateway's own image. The only one in the stack with the Docker socket. |

## The gateway holds no policy

Everything it needs is generated into `dist/gateway.json` from each agent's
`agent.json`:

| Field | Used for |
|---|---|
| `description` | The tool description, verbatim — this is what a client model reads to choose an agent. |
| `image` | The container to start. Each agent supplies its own Dockerfile. |
| `entrypoint` | Which mode to run it in. |
| `flags` | The complete isolation and resource flag list, passed through unexamined. |
| `env` | The agent's own runtime variables, with `{{key}}`, `{{base_url}}` and `{{model}}` substituted. |

So an agent brings its own tooling, its own dependencies and its own
configuration. The gateway does not know what `OPENCODE_CONFIG_CONTENT` means
and never needs to: it substitutes three placeholders and passes the result on.

Adding an agent means adding a plugin directory and recompiling. There is no
gateway code change and no gateway-side per-agent branch.

## What the gateway deliberately does not do

- **No host mounts.** A call mounts nothing, so two calls cannot collide over a
  working directory. An agent that needs files gets them with the tools in its own
  image. Isolation test 5b asserts this.
- **No caller-supplied environment.** The prompt is the only input a caller
  controls. The one credential the gateway handles is a key it mints itself, budgeted
  and scoped to the call — a caller-supplied key would let one agent spend
  another's budget.
- **No caller-chosen image or flags.** Those come from the manifest, so a caller
  cannot ask for a container the plugin did not describe.
- **No socket in agent containers.** The gateway has the Docker socket; the agents
  it starts do not, which is what stops an agent starting a sibling. That is the
  real isolation boundary — not the network.

## Reaping

Every call is labelled `cloakai.call=1`, `cloakai.started=<epoch>` and
`cloakai.instance=<agent>`. `scripts/cloakai-reap.py` removes any labelled
container past the maximum call lifetime, and `--rm` handles the normal case.
The labels are the entire contract; if a call container loses them, it leaks.

## Verified

- `./scripts/test.sh` → **17 passed, 0 failed, 2 expected skips**, which includes
  23 dispatcher tests asserting the command line the manifest really produces.
- `gateway/tests/smoke_mcp.py` starts the gateway over stdio as a real MCP
  server, calls `tools/list`, calls `dev`, and checks the answer. It is not a
  mock:

  ```
  tools/list -> ['dev']
  tools/call dev -> PONG
  ```

Run it with:

```bash
docker run --rm --entrypoint python \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD/dist:/srv/dist:ro" -v "$PWD/gateway:/srv/gateway:ro" \
  -e CLOAKAI_NETWORK=cloakai-internal \
  cloakai/gateway:latest python -m gateway.tests.smoke_mcp
```

## Open

- Concurrency. Calls are independent; nothing caps how many run at once. Decide a
  limit before this is exposed to anyone else.
- A second agent, so the multi-agent path is exercised rather than assumed. The
  dev agent is the only one implemented.
