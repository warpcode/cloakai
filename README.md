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

## Quick start

```bash
# one-time
./scripts/build.sh                              # compile + build the agent image
docker compose -f infra/compose.yml up -d       # the proxy and its credential store

# daily
./scripts/run.sh "review the diff on this branch"  # an isolated agent on your project
./scripts/test.sh                                 # compiler + isolation tests
./scripts/install.sh                             # install the generated trees into clients
```

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
| **Isolated** | The host filesystem, except the project directory you mounted. The internet. Any provider credential. Sibling containers. |
| **Available** | Your project directory, and the proxy on `internal`. |
| **Not shared** | Nothing persists between runs. There is no writable volume, so there is nothing for two invocations to collide over. |

Two concurrent runs of the same agent cannot see each other's files, because they are different
filesystems. That is structural rather than a mitigation — see [#14](https://github.com/warpcode/cloakai/issues/14)
and `scripts/isolation-tests.sh`.

## How this fits together

```
                     ┌──────────────────────┐
                     │      your host       │
                     └──────────────────────┘
        internal (no route off this machine)
   ┌────────────────────────────────────────────────┐
   │                                                │
   │   dev-agent ──► litellm ──┐                    │
   │      │              │      │                    │
   │      │          postgres    │                   │
   │      │            (keys)    │                   │
   │                        ┌────┘                   │
   │                        ▼                         │
   │                 (upstream: a model provider)     │
   └────────────────────────┼────────────────────────┘
                            │ only litellm is here
        egress (default bridge)
                            ▼
                        the internet
```

| Container | Networks | Capability |
|---|---|---|
| `dev-agent` | `internal` | Your project directory, and the proxy. Nothing else. |
| `litellm` | `internal` + `egress` | The only route off this machine. Holds provider credentials. |
| `postgres` | `internal` | The credential store. Cannot reach the internet, deliberately. |
| `mock-upstream` | `egress` | Test profile only — stands in for a model provider. |

**Who can leave the machine: only `litellm`.** That is the whole point. A container reaches the
internet by being on `egress`, and only `litellm` is. To give the agent a new capability — web
fetch, say — add a container on `internal` *and* `egress` that serves exactly that tool. Do not add a
second network; adding a container to the existing two is the entire extension mechanism.

The gateway — one endpoint that spawns a fresh container per call — is **not built yet.** Its seven
design questions were [answered by measurement](docs/gateway-verification.md), which produced a
[decision](docs/gateway-verification.md#decision-2026-09-30--config-2-all-traffic-goes-through-the-gateway):
**agents never talk directly to each other; all traffic is mediated by the gateway.** That is what
makes the hop limit enforceable at all. The [design](docs/gateway-design.md) rests on one measured
property — the gateway binds a separate listener per interface address, so a client cannot open the
agent listener and vice versa, and call provenance comes from the kernel rather than a forgeable
header.

### What is not automatic yet

**Nothing calls these containers.** There is no gateway, no per-call container spawning, no
scheduler, no queue. `dev-agent` runs because you asked for it, and you call it by hand. The next
phase ([#15](https://github.com/warpcode/cloakai/issues/15) is done, [#17](https://github.com/warpcode/cloakai/issues/17)
is the gateway) is what makes anything drive it.

Treat this phase as plumbing, not automation. What it does buy you: the isolation flags live in one
versioned file instead of someone's shell history, and the container survives `docker compose up`.

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
`infra/compose.yml` states the same flags in YAML. `scripts/check-isolation-parity.sh` fails if they
disagree, and `scripts/dev.sh test` runs it. Without that guard, a change to one could leave the
compose-managed container — the one a supervisor actually keeps alive — quietly less isolated than the
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
└── tests/                                52 tests

agents/                                   the container
├── dev.Dockerfile                        two modes off one image
├── cloakai-entrypoint.sh                 run | mcp | shell
└── isolation-flags                       shared by run.sh AND the isolation tests

infra/
├── compose.yml                           internal + egress networks, proxy, credential store
├── litellm.yaml                          virtual keys with per-key budgets
└── mock-upstream.py                      test profile only, so test 8 needs no provider key

schemas/                                  vendored JSON Schemas, never fetched at load time
dist/                                     GENERATED, committed (see #16 for moving it to CI)
clients/                                  GENERATED config files
docs/verification/phase-0.md              what was verified, and what disproved an assumption
docs/phase-1.md                           Phase 1 results
docs/phase-2.md                           Phase 2 results
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

Phases 0–2 are done: verification, the plugin compiler, and an isolated container runtime with a
proxy in front of it. Phase 3 (Compose integration and a stable gateway address) is next, so the
MCP endpoint in the generated plugins points at a hostname the gateway does not serve yet —
`tools/list` against the generated config will not resolve until then.

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
