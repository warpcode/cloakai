# Phase 1 — Portable plugin + compiler

**Issue:** [#13](https://github.com/warpcode/cloakai/issues/13) · **Epic:** [#11](https://github.com/warpcode/cloakai/issues/11)

The thesis test. One source, five targets, one edited file.

---

## Verdict

**The thesis holds.** Editing `plugins/dev/io.github.warpcode.cloakai/agent.json` — and nothing
else — changes the output of all five targets, and no other file needs to be touched. That is
asserted as a test (`test_editing_agent_json_changes_every_target`), not just demonstrated once.

Two bugs were found *by running the thing*, not by reading it. Both are recorded below because
both are the exact failure mode this phase exists to prevent, reached from an unexpected
direction.

---

## What was built

| Piece | Where | Notes |
|---|---|---|
| Source plugin | `plugins/dev/` | Two skills, an MCP server, one `agent.json` |
| Compiler | `compiler/cloakai_compiler/` | Python, stdlib only, ~700 lines |
| Per-client table | `compiler/cloakai_compiler/clients.py` | Adding a client is a row |
| Validation | `compiler/cloakai_compiler/validate.py` | 30+ checks, all fail loudly |
| Tests | `compiler/tests/` | 42 tests |
| Schemas | `schemas/` | Five, vendored, never fetched at load time |
| Scripts | `scripts/{compile,install,test}.sh` | |

`./scripts/test.sh` → **13 passed, 0 failed, 2 expected skips.**

---

## The two bugs

### 1. agy validated a plugin that loaded nothing

The first compiler emitted agy agents with **no frontmatter at all**, reasoning that since agy's
schema is permissive, emitting fewer fields was safer. It passed `agy plugin validate` cleanly:

```
  [ok]    ./dist/dev/google.antigravity
          ✔ skills      : 2 processed
          ✔ agents      : 1 processed
          ✔ mcpServers  : 1 processed
```

and then `agy agents` listed **nothing**.

agy registers an agent by its frontmatter `name`. With no frontmatter the file validates and
loads zero agents — the precise "plugin that half-works" failure Task 4 was written to prevent,
arrived at from the opposite direction: I was trying too hard to be safe.

This is also the second time in two phases that a green `validate` was not evidence of working
behaviour. Treat a validator as a shape check, never a load test.

Fixed by mapping `agent.json` onto agy's documented fields through the clients table, and
covered by three tests: frontmatter present, unset optionals omitted, set optionals mapped.

### 2. The linter's own version check was wrong

The first implementation compared `mcp.json`'s `$schema` against `plugin.json`'s `$schema` as
strings. The spec requires the **version** to match — the two canonical URLs legitimately
differ (`plugin.schema.json` vs `mcp.schema.json`).

The linter rejected its own valid source plugin on the first run, which is how it was caught.
Now compares extracted versions, and the check is tested directly since it is unreachable via a
real build until 1.1.0 exists.

---

## Schemas

Five, all vendored under `schemas/`:

| Schema | Source | Fetched? |
|---|---|---|
| `agent-plugins-plugin-1.0.0.json` | agent-plugins.org | yes |
| `agent-plugins-mcp-1.0.0.json` | agent-plugins.org | yes |
| `antigravity-plugin.json` | docs, transcribed | **no — 404** |
| `antigravity-agent-frontmatter.json` | docs, transcribed | no schema published |
| `antigravity-rule-frontmatter.json` | docs, transcribed | no schema published |

**`https://antigravity.google/schemas/v1/plugin.json` returns HTTP 404.** It is published as a
`$schema` value and an editor-autocomplete aid, but nothing is served there. The file in
`schemas/` is transcribed verbatim from the documentation's own "Full JSON Schema" block, and
its description says so. The two frontmatter schemas have no published JSON Schema at all, so
they are transcribed and annotated with the Phase 0 finding that agy does not enforce them.

---

## Acceptance criteria

- [x] `plugins/dev/` authored and validates against the vendored schemas
- [x] `compiler/` implements all four rules, no per-client special-casing in the source
- [x] `scripts/compile.sh` regenerates `dist/` and `clients/` deterministically
- [x] Running the compiler twice produces an empty diff
- [x] All Task 4 lint failures exit non-zero naming the offending file
- [x] Rule 1 output is byte-identical to source
- [x] `google.antigravity/plugin.json` contains only `$schema`, `name`, `description`
- [x] `google.antigravity/mcp_config.json` uses `serverUrl`, no `url` or `httpUrl`
- [x] `google.antigravity/agents/*.md` frontmatter matches the Check 5 schema
- [x] `clients/opencode.json` uses `mcp` + `"type": "remote"`, no `"sse"`
- [x] `scripts/install.sh` installs into agy (verified live: `agy agents` lists `dev`)
- [x] `scripts/test.sh` confirms skills load in every target tested
- [x] `dist/` committed, no absolute host paths
- [x] Editing `agent.json` alone changes all five outputs — **asserted as a test**

---

## Verified live

```console
$ agy plugin validate ./dist/dev/google.antigravity
  [ok]    ./dist/dev/google.antigravity
          ✔ skills      : 2 processed
          ✔ agents      : 1 processed
          ✔ mcpServers  : 1 processed

$ ./scripts/install.sh && agy agents
dev
```

Install is idempotent: a second run reports `agy: dev already installed` and changes nothing.

---

## Not verified, and why

- **`tools/list` on every client.** The MCP endpoint is `cloakai-gateway:4483`, which does not
  exist until Phase 3. `scripts/test.sh` reports this as an expected skip, distinguished from a
  defect. Phase 0 established that no client drops a non-loopback `http://` entry, so the
  failure mode here will be connection-refused, not silent.
- **VS Code and OpenHands loading.** Both are namespace targets and the output is structurally
  correct, but VS Code's surface is GUI-only and OpenHands was not installed. Their namespaces
  follow the documented `componentPaths` layout; confirm on first real use.
- **Claude Code loading.** Manifest placement (`.claude-plugin/plugin.json`, not the root) is
  correct per its documented layout, but no Claude Code install was available to test against.

None of these are the same as a defect. All three are "the client was not present", and each is
one command away from checking once the client is.

---

## For Phase 2

The compiler already emits everything a container needs to know — `image`, `entrypoints`,
`limits`, `isolation`, `mcp_servers` — in `agent.json`, and nothing downstream reads it yet.
Phase 2 should consume that file rather than re-deriving it, so a runtime change still means
editing exactly one file.

Two things Phase 0 established that Phase 2 must honour: `serve mcp` needs
`--insecure-no-auth` on a non-loopback listen, and the isolation flag set needs **four** tmpfs
mounts, not one.
