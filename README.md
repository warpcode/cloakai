# cloakai

Define an agent once, use it from many AI harnesses.

`cloakai` compiles a single portable plugin into whatever configuration format each
client wants — agy, opencode, Claude Code, VS Code, OpenHands — and later runs those
agents inside isolated containers.

## Why

Every harness invents its own configuration format. opencode wants `opencode.json` with an
`mcp` key. agy wants `mcp_config.json` with `serverUrl`. VS Code wants a namespace directory
read in place. Claude Code wants its manifest at `.claude-plugin/plugin.json`. Each is a
different shape for the same agent, and keeping them in sync by hand does not survive
contact with a real change.

So: one source, four translation rules, no per-client special-casing in the source. Editing
one file changes every target.

## The `cloakai` CLI

Two shapes, and the difference is only which entrypoint they use.

```bash
cloakai agents                      # list what you can invoke
cloakai prompt big-pickle "what is 1+1"   # one task, then destroyed
cloakai agents dev "review this"    # the same thing, spelled out
cloakai mcp dev                     # long-lived: stdio until stdin closes, then destroyed
cloakai show dev                    # one agent's catalogue entry, as JSON
cloakai doctor                      # is anything actually runnable
```

### big-pickle

The free model, and the one agent that needs no credential at all:

```bash
cloakai prompt big-pickle "what is 1+1"
```

It runs `opencode run --model opencode/big-pickle` inside a container and needs no
key, because `opencode.ai/zen` serves that model without authentication.

Nothing needs to be running first. There is no proxy, no stack, and no managed
network — the agent calls `opencode.ai/zen` directly, so it gets the default bridge.

Isolation is per-container and that is the whole of it:

| | |
|---|---|
| read-only root filesystem | nothing persists; writes go only to tmpfs |
| all capabilities dropped | `CapEff=0` |
| no Docker socket | an agent cannot start a sibling container |
| no host mounts | a default invocation sees none of your files |
| resource limits | cpus, memory, pids |

**The network is not isolated.** Agents reach the internet, because their model
lives there. There is no `internal`/`egress` split and no single container holding
the only route out. An agent that declares `"network"` in its `agent.json` is
attached to the network you name; none do today.

### Two shapes

`agents` is ephemeral. The docker run carries `--rm`, so destruction is not a
cleanup step that can be forgotten — the container cannot outlive the command.

`mcp` is long-lived but still disposable. It serves stdio until stdin closes and is
destroyed then. Every caller spawns **its own** container, so two clients using the
same agent are isolated from each other by having separate containers.

There is deliberately no `cloakai serve` and no shared daemon. A CLI is
per-invocation, so every shape it offers is ephemeral by construction; anything
long-running would need something to hold it, and nothing here does that on
purpose.

The CLI holds no isolation policy. Every flag comes from `dist/agents.json`,
generated from each agent's `agent.json`, so it cannot drift from what the plugin
declared — and it never mounts a host directory, in either shape.

```bash
./scripts/compile.sh          # writes dist/agents.json
PYTHONPATH=. python3 -m cli.main doctor
```

## Quick start

```bash
# one-time
./scripts/build.sh            # compile, then build each agent image

# daily
cloakai prompt big-pickle "what is 1+1"
cloakai agents
./scripts/test.sh             # compiler, CLI, isolation
```

There is no stack to start. Nothing needs to be running for any command here.

## Using an image directly

An image is the product. There is no gateway and nothing in an image refers to one,
so every agent is usable with nothing else running:

```bash
# the dev agent, as an MCP server over stdio
docker run --rm -i cloakai/dev:latest mcp
```

That is a complete MCP server on stdio — no port, no service, nothing to start
first. Any MCP client can spawn it:

```bash
opencode mcp add cloakai -- docker run --rm -i cloakai/dev:latest mcp

# agy needs -- because the args begin with -
agy mcp add cloakai -- docker run --rm -i cloakai/dev:latest mcp
```

`./scripts/compile.sh` writes these entries into `clients/opencode.json` and agy's
`mcp_config.json` already, in each client's own shape.

### The two runtimes, and two different credential variables

| Mode | What runs | Credential |
|---|---|---|
| `run` | the `opencode` binary | `OPENAI_API_KEY` **and** `OPENCODE_CONFIG_CONTENT` |
| `mcp` | docker-agent's own loop | `OPENAI_API_KEY` |

`agent.yaml` declares `provider: openai`, so docker-agent reads the *OpenAI*
variable. Setting `OPENCODE_CONFIG_CONTENT` in `mcp` mode does nothing and you get
`HTTP 401: No api key passed in`.

To keep the key out of files entirely, the `bash -c` idiom avoids writing it
anywhere:

```bash
bash -c 'export OPENAI_API_KEY="$(...)"; exec docker run -i --rm \
  -e OPENAI_API_KEY cloakai/dev:latest mcp'
```

### Jules

The same contract, for a remote autonomous agent where each session is its own
sandbox. The credential is passed per invocation and is never baked into the image:

```bash
JULES_API_KEY=$(cloakenv get "kp://Personal/Credentials/Google - Main - Jules - Api Key:Password") \
  docker run --rm -i -e JULES_API_KEY cloakai/jules:latest mcp
```

Five tools, because a Jules session is not a `docker run` — it outlives the call
that created it and can park on a plan gate for 20–30 minutes:

```python
jules_start("describe your working directory")               # project-less sandbox
jules_start("add tests", source="github/warpcode/cloakai")   # repo-backed
jules_status("15497948316305709445")   # state, whether PARKED, PR, timeline
jules_send(id, "keep the existing assertions")
jules_approve_plan(id, "p-42")         # write: resumes the run
```

`jules_status` reports `COMPLETED` and whether the session is genuinely finished.
Jules sets `COMPLETED` when a runner goes **idle**, so a session waiting on an
unapproved plan looks identical to a finished one; only a `sessionCompleted`
marker in the timeline settles it.

### Checking it still holds

```bash
./scripts/check-direct-use.sh    # both images, over stdio, with nothing else running
```

`scripts/test.sh` runs it. It caught nothing at first, and that is the point — the
claim was verified by hand for several turns and by nothing automatic, which is
how `mcp.json` came to point at a service that was never running while every
test stayed green.

`./scripts/compile.sh` and `./scripts/install.sh` need no Docker at all. A plugin is a
directory, so you can install it and use the agent on your workstation with nothing else running.

## Running an agent in a container

`./scripts/run.sh` gives the agent your project directory and nothing else. Two things are worth
knowing before you use it.

**The container cannot reach the internet.** Not a policy — a topology. It sits on a Docker network
created with `internal: true`, which has no route off the machine. So `curl`, `git clone` and
`npm install` do not work inside it. That is the design: the model call goes through a proxy on the
network, and everything else the agent might want becomes something you deploy as a *tool* rather
than something it has ambiently. It is fully reversible — give a capability container a second
network and it can reach out, with no architectural change (see [#19](https://github.com/warpcode/cloakai/issues/19)).

**The container holds a scoped virtual key, never a provider credential.** The proxy issues
per-key budgets, so a prompt injection cannot spend a real one. `scripts/isolation-tests.sh` test 8
mints its own single-model, one-dollar key to prove it.

### What is and is not isolated

| | |
|---|---|
| **Isolated** | The host filesystem. The internet. Any provider credential. Sibling containers. **Your project files — by default.** |
| **Available** | The proxy on `internal`. Nothing else, unless you opt in. |
| **Not shared** | Nothing persists between runs. There is no writable volume by default, so there is nothing for two invocations to collide over. |

### The workspace is opt-in

`./scripts/run.sh` mounts **no host directory**. An isolated invocation cannot see your files at all,
and two of them cannot interfere, because there is no shared path to interfere over.

Most agents need no files. When one genuinely does — code review, for instance — opt in:

```bash
./scripts/run.sh --workspace . "review the diff on this branch"
```

That is a deliberate, visible choice, and it is where the guarantee stops. Two invocations that both
opt into the same directory **will** share it, and will overwrite each other's files. Verified rather
than assumed — `scripts/isolation-tests.sh` test 5c mounts a workspace deliberately and confirms the
sharing appears, so test 5 is not passing for an unrelated reason.

If you want an agent to work on your files, opt in explicitly with
`--workspace`. Nothing mounts anything by default.

## How this fits together

```
        your host
   ┌────────────────────────────────────────────────┐
   │                                                │
   │   cloakai agents / cloakai mcp                 │
   │        │                                       │
   │        ▼                                       │
   │   ┌──────────────┐   docker run --rm           │
   │   │ agent        │──────────────────────────┐  │
   │   │ read-only fs │                          │  │
   │   │ no caps      │   reaches the model      │  │
   │   │ no socket    │──────────────────────────┘  │
   │   │ no mounts    │                             │
   │   └──────────────┘        container destroyed │
   │                               when it exits   │
   └────────────────────────────────────────────────┘
                            │
                            ▼
                 opencode.ai/zen  (or any endpoint
                                     the agent names)
```

There is no proxy and no network split. An agent container reaches the endpoint its
`agent.json` names, and is destroyed when it exits. The guarantees are all
per-container: read-only root filesystem, no capabilities, no Docker socket, no host
mounts, resource limits.

**The network is the one thing that is not isolated.** A prompt injection can reach
the internet, because the model is there. If that becomes a problem, an agent that
declares `"network"` in its `agent.json` is attached to the network you name, and you
create that network with `docker network create --internal ...`. Nothing in the code
assumes a boundary that is not declared.

### There is no gateway, and that is the design

A gateway was built and then removed. It worked — an MCP server that spawned a fresh container per
call — but it turned out to be unnecessary, because an image already speaks MCP over stdio and any
client can spawn it directly:

```bash
docker run --rm -i cloakai/dev:latest mcp
```

That removes the reason the gateway existed: there is nothing left for it to mediate. Its measured
[design work](docs/gateway-verification.md) is kept for the record — hop limits, two-listener
provenance — but nothing in the system depends on any of it, and `docs/gateway-design.md` was
deleted with the code.

Isolation does not rest on the gateway either. It rests on the container flags in
`agents/isolation-flags`: read-only rootfs, dropped capabilities, four tmpfs mounts, and no Docker
socket, so an agent cannot start a sibling. An agent image has no way to reach another agent even
if one wanted to.

### What is not automatic yet

**No scheduler, no queue, no concurrency cap.** Nothing coordinates how many containers run at once.
`scripts/cloakai-reap.py` exists to reap a per-call container that outlived its call, but nothing
currently creates `cloakai.call=1` containers, so it has nothing to reap — it is orphaned machinery
kept in case per-call spawning returns.

What this phase does buy you: the isolation flags live in one versioned file instead of someone's
shell history, every client gets its tools from a generated stdio entry, and both images are usable
with no service running.

### The dev stack

```bash
./scripts/dev.sh up       # bring everything up, WAIT for health, print state
./scripts/dev.sh logs     # tail it
./scripts/dev.sh shell    # exec into the agent container
./scripts/dev.sh test     # isolation parity + the full isolation suite
./scripts/dev.sh down     # tear down
```

`up` gates on health rather than firing and forgetting — a stack that takes 30 seconds to become
usable and says nothing is how ordering bugs stay invisible.

`dev-agent` publishes **no ports**. It is reachable as `dev-agent:8081` from `internal` and from
nowhere else. If you need to poke at it, publish to `127.0.0.1` and remove it afterwards.

### Two ways to run the agent, and they cannot drift

`agents/isolation-flags` is the source of truth for `docker run`. Compose cannot read a file, so
`scripts/check-isolation-parity.sh` fails if the generated catalogue and this file
disagree, and `scripts/dev.sh test` runs it. Without that guard, a change to one could leave the
spawned agent — the one a client actually runs — quietly less isolated than the
one you tested.

### The isolation flags live in one file

`agents/isolation-flags` is read by both `scripts/run.sh` and `scripts/isolation-tests.sh`, so the
container you run and the container that is tested cannot drift apart. Every flag in it is justified
in a comment next to it. If you add one, add the reason — the set was derived by escalating one
flag at a time, not guessed.

## Layout

```
plugins/dev/                              SOURCE — edit this
├── plugin.json                           manifest, per Agent Plugins 1.0.0
├── mcp.json                              MCP servers
├── skills/<name>/SKILL.md                identical in every target
└── io.github.warpcode.cloakai/
    └── agent.json                        the only file that changes when the runtime does

compiler/cloakai_compiler/
├── clients.py                            the per-client table
├── strategies.py                         one per output shape, dispatched from the table
├── compile.py                            the four rules
├── validate.py                           every lint check; all fail loudly
└── tests/                                64 tests

agents/                                   the container
├── dev.Dockerfile                        code review and release notes
├── big-pickle.Dockerfile                 the free model, no credential
├── jules.Dockerfile                      Jules as MCP over stdio
├── cloakai-entrypoint.sh                 run-cmd | mcp | mcp-http | shell
├── isolation-flags                       shared by run.sh AND the isolation tests
└── jules-mcp/                            the Jules adapter: client, server, verdict

cli/                                      the `cloakai` command
├── registry.py                           catalogue, argv, lifecycle
└── main.py                               agents | prompt | mcp | show | doctor

infra/conformance/                        real-client conformance harness

schemas/                                  vendored JSON Schemas, never fetched at load time
dist/                                     GENERATED, committed (see #16 for moving it to CI)
clients/                                  GENERATED config files
docs/verification/phase-0.md              what was verified, and what disproved an assumption
docs/phase-1.md .. phase-3.md             phase results, historical
```

## The four rules

| # | Client shape | Action |
|---|--------------|--------|
| 1 | Agent Plugins conformant | `plugin.json`, `skills/`, `mcp.json` copied **byte-for-byte** |
| 2 | Namespace + root manifest (VS Code, OpenHands) | Components generated in place inside their namespace |
| 3 | Needs its own manifest (agy, Claude Code) | A complete, independently installable tree |
| 4 | Config only (opencode, gemini-cli) | A few lines of MCP config |

The branch between rules 2 and 3 is one question: does the client recognise a root
`plugin.json` carrying the canonical `$schema`? Yes → rule 2. No → rule 3.

## Determinism

The compiler is deterministic by construction — sorted iteration, no timestamps, no absolute
paths, stable key order. `scripts/test.sh` compiles twice and compares hashes, so a
non-deterministic regression fails the build rather than producing an unreviewable diff.

## Linting is not optional

Task 4 of [#13](https://github.com/warpcode/cloakai/issues/13) exists because VS Code
**silently skips** skills with an invalid name, and because a `mcp.json` whose `$schema`
version drifts from `plugin.json` loads skills with no tools and no error. The compiler exits
non-zero on all of it. A plugin that refuses to build is better than one that half-works.

## Status

Phases 0–3 are done, and the gateway planned for the end of it was built and then removed once it
turned out to be unnecessary — every client spawns the container over stdio instead. What works
now:

- Two agent images, `cloakai/dev` and `cloakai/jules`, each usable with nothing else running.
- Generated stdio entries for opencode, agy, Claude Code, VS Code and OpenHands, in each client's
  own shape.
- Isolation asserted by `scripts/isolation-tests.sh`, including that a default invocation mounts no
  working directory at all.
- A model proxy that is the only container able to reach the internet.

Not done: concurrency limiting, and a second agent that is not Jules or `dev`.

`./scripts/test.sh` runs three layers: compiler correctness and determinism (fast, no Docker),
container isolation (needs the image and the network), and **client conformance** — a real,
pinned OpenHands loading the generated skills and calling `tools/list` against the running agent.

That third layer matters more than its size suggests. Every other check tests our output against our
own expectations, which cannot catch "the client silently dropped it" — the exact failure the whole
compiler exists to prevent. It is a skip, never a silent pass, when Docker or the network is absent.

**VS Code is the one client not covered.** A containerised test is possible in principle
(code-server ships the agent-plugins system) but its workbench is lazy-loaded and the chat subsystem
needs an authenticated Copilot session — the blocker is a signed-in session, not an install. It is
covered instead by static analysis of the shipped workbench bundle; see
[docs/verification/phase-0.md](docs/verification/phase-0.md) Check 2.

Issues: [#11 epic](https://github.com/warpcode/cloakai/issues/11) ·
[#12 Phase 0](https://github.com/warpcode/cloakai/issues/12) ·
[#13 Phase 1](https://github.com/warpcode/cloakai/issues/13) ·
[#14 Phase 2](https://github.com/warpcode/cloakai/issues/14) ·
[#15 Phase 3](https://github.com/warpcode/cloakai/issues/15)
