# Phase 0 — Verification results

**Issue:** [#12](https://github.com/warpcode/cloakai/issues/12) · **Epic:** [#11](https://github.com/warpcode/cloakai/issues/11)
**Date:** 2026-09-28 · **Host:** Linux 5.x, Docker 29.8.1, Compose v5.5.1

Every command below was run on the host. Reproduce with the files in
[`probes/`](probes/). No production code was written, and **no fix for any failing check was
implemented** — per the issue's non-goals.

---

## Summary

| # | Check | Verdict | Consequence |
|---|-------|---------|-------------|
| 1 | `docker agent serve mcp` non-interactive in a container | **PASS** (with corrections) | Container `mcp` mode is viable |
| 2 | Conformant clients load non-loopback `http://` MCP URL | **PARTIAL** | opencode + OpenHands pass; **VS Code untested** |
| 3 | Harness CLI under isolation flags | **PASS** | Needs **four** tmpfs mounts, not one |
| 4 | Model call from an `internal: true` network | **PASS** | Isolation claim holds |
| 5 | agy agents/rules frontmatter schema | **PASS** | Schema obtained; **epic's extra-fields claim is wrong** |

**Exit criteria from the issue:** Check 5 passed and Checks 1–4 attempted. Satisfied. Checks 1, 3, 4
and 5 all passed, so there is no blocking design decision. **Check 2 is the one open item** and it is
not fully resolved — see [Check 2](#check-2--do-conformant-clients-load-a-non-loopback-http-mcp-url).

---

## Check 1 — Does `docker agent serve mcp` run non-interactively inside a container?

**Verdict: PASS.** It starts without a TTY, serves MCP over HTTP, and does not exit when stdin closes.
Three corrections to the issue's assumptions are recorded below, all of which would have broken a
Phase 2 implementation.

### Corrections

**1. The image is on Docker Hub, not ghcr.io.**

```console
$ docker pull ghcr.io/docker/docker-agent:latest
Error response from daemon: Head "https://ghcr.io/v2/docker/docker-agent/manifests/latest": denied
```

ghcr returns 401/403 for this path — the repository is not published there. The published image is
`docker/docker-agent` on Docker Hub (122 tags, ~97k pulls, `latest` updated the same day):

```console
$ docker pull docker/docker-agent:latest
docker.io/docker/docker-agent:latest
$ docker run --rm docker/docker-agent:latest version
docker-agent version main
Commit: 502549c3bb4b2647ffc81fdd5f9ebec29b4dd42d
```

**2. There is no `--version` flag.** The subcommand form is `version`; `--version` is rejected:

```console
$ docker run --rm docker/docker-agent:latest --version
Error: unknown flag: --version
```

**3. `agent.yaml` needs `version: "16"` and a `models:` entry.** The minimal config that loads is in
[`probes/probe.yaml`](probes/probe.yaml):

```yaml
version: "16"
models:
  probe-model:
    provider: openai
    model: gpt-4o-mini
    base_url: http://openai-proxy:4000/v1
agents:
  probe:
    description: A minimal probe agent used to verify the docker agent MCP server.
    instruction: Reply with exactly PONG when asked to ping.
    model: probe-model
```

Getting here took four iterations against the parser, each of which is worth recording because they
are all plausible-looking mistakes:

| Attempted shape | Error |
|---|---|
| Top-level `name`/`description`/`model`/`instruction` | `unknown field "name"` |
| Nested under `agent:` | `unknown field "agent"` |
| `version: "0.1"` | `unsupported config version: 0.1 (valid versions: 0, 1, 10, …, 16)` |
| `agents:` as a **list** of objects | `sequence was used where mapping is expected` |
| `agents:` as a **map**, no `models:` | `model '' not found in configuration` |
| `models:` referencing `dmr/ai/qwen3` | `docker model runner is not available` |

**`agents:` is a mapping keyed by agent name, not a list.** The DMR path is a dead end inside a
container (DMR runs on the host), so the probe uses an explicit `models:` entry pointing at the
proxy from Check 4.

### `serve mcp` behaviour

```console
$ docker run --rm --network p0_internal -e OPENAI_API_KEY=sk-dummy -e TELEMETRY_ENABLED=false \
  -v "$PWD/probe.yaml:/agent.yaml:ro" \
  docker/docker-agent:latest \
  serve mcp /agent.yaml --http --listen 0.0.0.0:8081 --insecure-no-auth

Tool safety policy: restricted (source: serve default)
MCP HTTP server listening on http://[::]:8081
```

**Non-loopback `--listen` is refused without an explicit opt-in**, which the issue's command omitted:

```console
$ docker run --rm … serve mcp /agent.yaml --http --listen 0.0.0.0:8081
Error: non-loopback MCP HTTP listeners require --auth-token or --insecure-no-auth
```

Either `--auth-token <token>` or `--insecure-no-auth` is mandatory in a container, since the container
is reached by IP. **`--insecure-no-auth` on `internal` is the intended shape** — the network is the
capability grant, and the internal network has no route off-host. `--auth-token` is the option for the
future gateway.

### Endpoint and transport

`GET` on any path returns `405 Method Not Allowed`, so the path is not discoverable by probing:

```console
$ curl -s -o /dev/null -w "root=%{http_code}\n" http://172.19.0.3:8081/
root=405
$ curl -s -o /dev/null -w "mcp=%{http_code}\n"  http://172.19.0.3:8081/mcp
mcp=405
```

`POST` of an MCP `initialize` succeeds on **`/`, `/mcp`, and `/sse` alike** — the server does not
distinguish paths:

```console
$ curl -s -X POST http://172.19.0.3:8081/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"logging":{},"tools":{"listChanged":true}},"protocolVersion":"2024-11-05","serverInfo":{"name":"docker agent","version":"main"}}}
```

Responses are **SSE-framed even on POST** (`Accept: text/event-stream`), not plain JSON.

**No `Mcp-Session-Id` header is issued or required** — `tools/list` worked without one, so the server
is stateless per request. That is worth knowing for Phase 2: there is no session to correlate or to
lose on reconnect.

`tools/list` returns the agent as a single tool named after the agent:

```json
{"tools":[{"name":"probe","description":"A minimal probe agent used to verify the docker agent MCP server.",
  "inputSchema":{"type":"object","properties":{"message":{"type":"string","description":"the message to send to the agent"}},"required":["message"],"additionalProperties":false},
  "outputSchema":{"type":"object","properties":{"response":{"type":"string","description":"the response from the agent"}},"required":["response"],"additionalProperties":false}}]}
```

### Lifecycle

**Does not exit on stdin close.** With stdin at `/dev/null` the process kept serving until the timeout
killed it (exit `124`):

```console
$ timeout 25 docker run --rm -i … serve mcp /agent.yaml --http --listen 0.0.0.0:8081 --insecure-no-auth < /dev/null
Tool safety policy: restricted (source: serve default)
MCP HTTP server listening on http://[::]:8081
REAL_EXIT=124
```

This is the required behaviour for a long-lived MCP container, and it confirms the epic's
**⚠️ Verified limitation**: `serve mcp` is one long-running process hosting every agent in the config.
It does not spawn a container per call — which is precisely why cloakai wraps it.

---

## Check 2 — Do conformant clients load a non-loopback `http://` MCP URL?

**Verdict: PARTIAL.** opencode and OpenHands both connect to `http://172.19.0.3:8081/mcp`. **VS Code was
not tested.** See [What remains open](#what-remains-open-for-check-2) at the end.

The MCP server was left running on `p0_internal` at `172.19.0.3:8081` throughout, using
[`probes/plugin/mcp.json`](probes/plugin/mcp.json) as the throwaway non-loopback entry:

```json
{ "mcpServers": { "cloakai-probe": { "type": "http", "url": "http://172.19.0.3:8081/mcp" } } }
```

### opencode — PASS

opencode is a config-only client (compiler rule 4), not a namespace-based one, so this validates the
generated-config shape rather than the spec question:

```console
$ opencode mcp list
┌  MCP Servers
│
●  ✓ cloakai-probe connected
│      http://172.19.0.3:8081/mcp
│
└  1 server(s)
```

All three schemes side by side, from one container on `p0_internal`:

```
●  ✗ loopback-http      failed    SSE error: Unable to connect.
●  ✓ nonloopback-http   connected
●  ✗ nonloopback-https  failed    SSE error: Unable to connect.
```

The loopback failure is **expected and not a finding** — `localhost` inside the client container is
not the MCP container. `https` fails because the probe serves plain HTTP. The load-bearing result is
that **opencode applies no HTTPS requirement of its own**.

### OpenHands — PASS

OpenHands is the conformant client where the epic predicted silent rejection. It did not reject.
Two layers tested: config coercion, then a live connection.

Config coercion accepts a non-loopback `http` URL in both shapes:

```
non-loopback http    coerce-naked    -> ACCEPTED
non-loopback http    coerce-wrapped  -> ACCEPTED
```

A live `streamable-http` connection over the OpenHands SDK's own MCP client library:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://172.19.0.3:8081/mcp") as (r, w, _):
    async with ClientSession(r, w) as s:
        await s.initialize()
        res = await s.list_tools()
```

```
non-loopback http      -> OK 1 tools: ['probe']
loopback http          -> FAIL (unreachable, same container-isolation reason as above)
```

Tested against `openhands-ai 1.11.0` / `openhands-sdk 1.34.0`, installed in
`/tmp/opencode/ohvenv` from PyPI.

**So the epic's cited OpenHands failure mode did not reproduce on this version.** The documented
"`https` is required unless the host is a loopback address" wording may be a documentation-only
statement, may have been relaxed, or may apply to a config surface this test did not exercise. Treat
this as *good news with a caveat*, not as the constraint being abolished.

### VS Code — NOT TESTED

`code` 1.139.1 with `ms-azuretools.vscode-azure-github-copilot` 1.0.236 is installed, but the
`MCP: List Servers` view is GUI-only and there is no CLI surface that reports whether a plugin's MCP
entry was accepted. A throwaway plugin was staged under `~/vscode-p0/` but never loaded.

The remaining evidence would require reading the shipped extension bundle to see whether it enforces
the HTTPS rule. That was **not** done. The behaviour of VS Code against a non-loopback `http://` MCP
entry is therefore **unverified**, and the epic's "VS Code's behaviour is undocumented" still stands.

### What remains open for Check 2

VS Code is the one target whose `http://` behaviour is unknown, and it is a *functional* unknown, not a
paperwork one — the failure mode is a plugin that loads its skills but has no MCP tools. The decision
in #12 is to allow HTTP for now and treat the mitigation as a future issue, which stands. If VS Code
turns out to drop the entry, the mitigation order from the issue applies unchanged:

1. Make the gateway reachable as loopback from the client (SSH or local port-forward).
2. Ship a stdio shim in the plugin — `"command": "./bin/cloakai", "args": ["--gateway", "http://…"]` —
   so the URL lives in `args`, which the spec treats as an opaque string.

**Recommendation:** do not let Phase 1 block on this. Emit `http://`, and resolve the VS Code question
when the compiler's client matrix is first exercised against a real VS Code install.

---

## Check 3 — Does the harness CLI run under the intended isolation flags?

**Verdict: PASS.** The full flag set from the issue works, but it needs **four tmpfs mounts, not one**,
and the tmpfs paths are not obvious from the error order.

### The opencode image is on Docker Hub too, and needs a custom build

```console
$ docker pull ghcr.io/sst/opencode:latest
Error response from daemon: Head "https://ghcr.io/v2/sst/opencode:manifests/latest": denied
```

No official `sst/opencode` or `anomalyco/opencode` image is published. Docker Hub has only
third-party images, none official. [`probes/Dockerfile.oc`](probes/Dockerfile.oc) builds a minimal one
from `debian:bookworm-slim` plus `https://opencode.ai/install`, giving opencode 1.18.33.

Two details in that Dockerfile are load-bearing, and both were found by failure:

- **The binary must be moved out of `/root/.opencode/`.** The installer writes there, and the
  agent writes a `.gitignore` into its own data directory on startup. Under `--read-only` this fails
  with `EROFS: read-only file system, mkdir '/root/.opencode/.gitignore'`. The Dockerfile relocates the
  binary to `/usr/local/bin/opencode` and sets `XDG_DATA_HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME` so
  the writable paths land in the tmpfs mounts.
- **An `ENTRYPOINT` is required**; otherwise the container has no command to exec and
  `docker run … opencode-probe --version` fails with `executable file not found in $PATH`.

### The minimal working flag set

Starting from the issue's set and adding tmpfs paths as errors demanded them, in this order:

```console
$ docker run --rm -v "$PWD:/workspace" -w /workspace \
  --read-only \
  --tmpfs /tmp \
  --tmpfs /root/.local \
  --tmpfs /root/.config \
  --tmpfs /root/.cache \
  --cap-drop=ALL --security-opt no-new-privileges \
  --pids-limit=1024 --memory=4g \
  opencode-probe --version
1.18.33
```

Each additional tmpfs was required, in this sequence:

| Flag set | Result |
|---|---|
| `--read-only --tmpfs /tmp` | `EROFS … mkdir '/root/.local'` |
| `+ --tmpfs /root/.local` | `EROFS … mkdir '/root/.config'` |
| `+ --tmpfs /root/.config` | `EROFS … mkdir '/root/.cache'` |
| `+ --tmpfs /root/.cache` | **1.18.33** |

**Answers to the issue's specific questions:**

- **Does it need `--tmpfs /home/…`?** No. The image runs as `root`, and the three `XDG_*` tmpfs mounts
  cover what the agent writes.
- **Does it need a writable `HOME`?** Not a whole writable `$HOME` — three *subdirectories* suffice
  (`/root/.local`, `/root/.config`, `/root/.cache`), each isolated as tmpfs so nothing persists.
- **Does it need any capability dropped from `ALL`?** No. `cap-drop=ALL` works.
- **Does `--pids-limit=1024` suffice?** Yes, and so do much lower limits — see the matrix below.

### Escalation matrix — every row passes a real model call

`opencode run --auto "reply with exactly PONG"` against the Check 4 proxy:

```
PASS  | tmpfs only (no caps/limits)
PASS  | cap-drop=ALL
PASS  | cap-drop=ALL + no-new-privileges
PASS  | ALL + pids 1024 + mem 4g
PASS  | ALL + pids 256 + mem 4g
PASS  | ALL + pids 64 + mem 1g
PASS  | ALL + pids 64 + mem 512m
```

**No flag needed relaxing.** `--pids-limit=64 --memory=512m` is enough, which is far below the issue's
proposed 1024/4g — so the proposed limits are conservative rather than tight. Phase 2 can start from
the issue's values and tighten later without re-deriving anything.

The full call output:

```
> build · mock-model

PONG
EXIT=0
```

### One configuration gotcha

`OPENCODE_CONFIG_CONTENT` **must not contain `$schema`**, and the value must be a JSON object, not a
JSON string:

```console
Error: Configuration is invalid at OPENCODE_CONFIG_CONTENT
↳ Expected Config, got "{\n  \"$schema\": \"https://opencode.ai/config.json\", …"
```

Stripping `$schema` and passing the object directly works. Alternative paths that do **not** work under
`--read-only`: `OPENCODE_CONFIG=/tmp/oc.json` (the file is not in the tmpfs unless created there) and
bind-mounting a config file into `/root/.config/opencode/` (opencode wants to write a `.gitignore`
alongside it). `OPENCODE_CONFIG_CONTENT` is the only mechanism that works read-only.

---

## Check 4 — Can a model call complete from a container on an `internal: true` network?

**Verdict: PASS.** All four assertions in the issue hold, and so does the fifth — the "network
membership is the capability grant" claim.

Compose file in [`probes/net.yaml`](probes/net.yaml):

```yaml
services:
  probe:
    image: curlimages/curl:latest
    networks: [internal]
    entrypoint: ["sleep", "3600"]
  openai-proxy:
    build: { context: ., dockerfile: mock.Dockerfile }
    networks: [internal, egress]
networks:
  internal:
    internal: true
  egress: {}
```

[`probes/mock-server.py`](probes/mock-server.py) is a ~60-line OpenAI-compatible SSE server standing in for
litellm. It records every request, which is how the model call in Check 3 was confirmed to genuinely
reach the proxy rather than being served from somewhere else.

### The four assertions

```console
$ docker exec p0-probe-1 getent hosts openai-proxy
172.19.0.2        openai-proxy  openai-proxy          # 1. DNS by service name

$ docker exec p0-probe-1 curl -s -o /dev/null -w "%{http_code}\n" http://openai-proxy:4000/v1/models
200                                                          # 2. HTTP to proxy by name

$ docker exec p0-probe-1 curl -s -m 10 -o /dev/null -w "%{http_code}\n" https://example.com
000                                                          # 3. internet BLOCKED
FAILED (expected)

$ opencode-probe run --auto "reply with exactly PONG"
PONG                                                          # 4. model call succeeds
```

**Step 3 is the assertion that matters** and it genuinely fails — a real timeout, not a redirect or a
403. The container has no route off the `internal` network.

Proxy-side confirmation of the model call, with the request decoded:

```
[mock] call #1 stream=True model='mock-model' messages=3 tools=0
[mock] call #2 stream=True model='mock-model' messages=2 tools=10
```

The tool list arrives on the second call, confirming the MCP/tooling path is intact through the
proxy rather than bypassed.

### Network membership as the capability grant

A second container attached to `internal` **and** `egress` reaches the internet, while `probe` on
`internal` alone does not:

```console
$ docker run --rm --network p0_egress --network p0_internal \
  curlimages/curl:latest -s -m 10 -o /dev/null -w "web-container -> %{http_code}\n" https://example.com
web-container -> 200
```

Network membership:

```
p0-probe-1:         p0_internal
p0-openai-proxy-1:  p0_egress  p0_internal
```

**This is the epic's central isolation claim, proven.** Egress is granted by adding a network, not by
a policy — so a future web-capability container needs one extra line and the agent container needs
none. No `--read-only`-style belt-and-braces on the network.

---

## Check 5 — agy `agents/*.md` and `rules/*.md` frontmatter schema

**Verdict: PASS.** Both schemas obtained from the official docs, then confirmed empirically against
`agy` 1.2.12. The schema was **not** published as a JSON Schema, so this is transcribed from the docs
and verified by install.

### `agents/<name>.md` — the complete field list

From <https://antigravity.google/docs/subagents/>. Only `name` and `description` are required.

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | string | — | **Required.** Unique identifier |
| `description` | string | — | **Required.** Used by the planner to decide delegation |
| `tools` | string[] | `[]` | e.g. `view_file`, `replace_file_content`, `grep_search`, `run_command` |
| `mainAgent` | boolean | `true` | Whether selectable as the primary agent |
| `subagent` | boolean | `true` | Whether invocable via `invoke_subagent` |
| `model` | string | `inherit` | `inherit` \| `flash` \| `pro` |
| `commandExecutionPolicy` | string | `sandbox` | `off` \| `auto` \| `eager` \| `sandbox` |
| `mcpServers` | object[] | `[]` | Per-agent MCP servers |
| `skills` / `plugins` | string[] | `[]` | Skill paths or plugin dependencies |

### `rules/<name>.md` — the complete field list

From <https://antigravity.google/docs/rules/>. **Every file in a `rules/` directory must have
frontmatter with a valid `trigger`.**

| Field | Type | Required |
|---|---|---|
| `trigger` | string | **Yes.** `always_on` \| `model_decision` \| `glob` \| `manual` |
| `description` | string | Yes for `model_decision`; recommended for all |
| `globs` (or `glob`) | string | Yes for `glob`. Comma-separated; **quote patterns starting with `*`** or YAML reads them as an alias anchor |

**`AGENTS.md` and `GEMINI.md` use no frontmatter at all** — entire content is plain Markdown, always
active. A rule with an unrecognised `trigger` (e.g. camelCase `alwaysOn`) is **silently discarded**.

### Empirical confirmation

`agy plugin validate` reports each component independently, and **missing directories are not errors** —
this answers the issue's first sub-question directly:

```console
$ agy plugin validate ./p3     # plugin.json only, no agents/ or rules/
  [ok]    ./p3
          - skills      : skipped (not found)
          - agents      : skipped (not found)
          - commands    : skipped (not found)
          - mcpServers  : skipped (not found)
          - hooks       : skipped (not found)
```

`agy plugin install` stages to `~/.gemini/config/plugins/<name>/` (the docs say
`~/.gemini/antigravity-cli/plugins/`, and that second path did not exist on this host).

### ⚠️ The epic's "extra frontmatter properties" claim is WRONG

The epic (#11) states: *"⚠️ Known constraint: agy refuses to load agents/rules whose frontmatter
contains additional properties."* **This does not hold on agy 1.2.12.** Ten agents were built, each
with one candidate field, installed, and checked against `agy agents`:

```
agent-cep     (commandExecutionPolicy: sandbox)   LOADED
agent-color   (color: blue)                        LOADED   ← undocumented field
agent-mcp     (mcpServers: [])                     LOADED
agent-model   (model: pro)                         LOADED
agent-plug    (plugins: [])                        LOADED
agent-skills  (skills: [])                         LOADED
agent-sub     (subagent: true)                     LOADED
agent-tools   (tools: [view_file, grep_search])    LOADED
agent-ver     (version: 1)                         LOADED   ← undocumented field
                                              (agent-main NOT listed)
```

`color: blue` and `version: 1` are not in the documented schema, and both agents loaded. A separately
built agent with `totallyBogusField: hello` also loaded. **agy does not reject unknown frontmatter
keys in agent definitions.**

The one agent that did *not* appear in `agy agents` was the one with `mainAgent: false` — which is
correct behaviour, since that flag controls whether the agent is offered as a selectable primary agent
in `/agents`, not whether it parses. Confusing the two is easy and would have produced a false
positive here.

**Consequence for Phase 1:** the agy generator does **not** need a strict allowlist, and the "no file
shared between two frontmatter schemas" rule in the epic is less load-bearing than assumed — there is
only one agy schema to satisfy, and it is permissive. Still worth emitting only documented fields for
forward-compatibility, but an unknown key is not a build failure.

Rules behaved consistently: all five probe rules installed to disk without error, including one with
undocumented extras (`priority`, `scope`, `totallyBogus`) and one with camelCase `trigger: alwaysOn`.
Whether the last is *activated* is not observable from the CLI, so treat `trigger` correctness as
something to test against a real session in Phase 1 rather than something settled here.

### `hooks.json`

**Yes — the schema closely resembles the VS Code / Claude Code hook schema.** Same shape: a map of hook
name → event → array of `{matcher, hooks[{type, command, timeout}]}`, plus an optional `enabled: false`.
Events are `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`. Handlers receive JSON
on stdin and return JSON on stdout; only `type: "command"` is supported.

A Claude-style `hooks.json` with three hooks validated clean:

```console
$ agy plugin validate ./h1
  [ok]    ./h1
          ✔ hooks       : 3 processed
```

Two differences from Claude's schema worth noting for the compiler: the events are
`PreInvocation`/`PostInvocation` rather than `PrePromptUse`/etc., and the per-hook-key `enabled` flag
has no Claude equivalent. The rest maps directly.

### Terminal Sandbox — nesting concern resolved

From <https://antigravity.google/docs/sandbox/>: on Linux the sandbox is **kernel namespaces** (not a
VM or a Docker image), enabled via `--sandbox` or `enableTerminalSandbox` in
`~/.gemini/antigravity-cli/settings.json`.

**It does not assume it is the outermost sandbox, and nesting is safe** — it is a strictly inner
boundary, so cloakai's container remains the real isolation boundary and agy's sandbox only further
restricts. **Recommendation: do not pass `--sandbox`.** It would add a second layer whose failure modes
are undocumented, for no gain, since the container already provides stronger isolation. Revisit only if
a harness turns out to need the granular `read_file`/`write_file`/`unsandboxed(...)` permission model,
which the container cannot express.

---

## What this changes for the next phases

Nothing blocking. Phase 1 can proceed, with these corrections carried forward:

1. **Image references in the epic are wrong.** `docker/docker-agent` and the opencode image are both
   published on Docker Hub, not ghcr.io. Any Dockerfile or compose file in Phase 1–2 must use the
   correct registry. *(Epic correction, not a design change.)*
2. **`serve mcp` in a container needs `--insecure-no-auth` or `--auth-token`.** The issue's Check 1
   command omits both and fails outright.
3. **The isolation flag set needs four tmpfs mounts**, and the opencode image needs a purpose-built
   Dockerfile with the binary relocated out of `/root/.opencode/`. The published-image path is dead.
4. **agy frontmatter is permissive** — unknown keys do not break loading. The Phase 1 generator can be
   simpler than the epic assumes.
5. **Check 2 remains open for VS Code only.** opencode and OpenHands both accept non-loopback `http://`.
   Proceed with `http://`; the stdio-shim mitigation stays as the future-issue fallback.

No fix for any finding was implemented, per the issue's non-goals.
