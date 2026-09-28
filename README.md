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
./scripts/compile.sh      # plugins/ -> dist/ and clients/
./scripts/install.sh      # install the generated trees
./scripts/test.sh         # unit tests, determinism, conformance
```

No Docker, no container, no gateway. A plugin is a directory; you can install it and use the
agent on your workstation immediately.

## Layout

```
plugins/dev/                              SOURCE — edit this
├── plugin.json                           manifest, per Agent Plugins 1.0.0
├── mcp.json                              MCP servers
├── skills/<name>/SKILL.md                identical in every target
└── io.github.warpcode.cloakai/
    └── agent.json                        the only file that changes when the runtime does

compiler/cloakai_compiler/
├── clients.py                            THE per-client table — adding a client is a row
├── compile.py                            the four rules
├── validate.py                           every lint check; all fail loudly
└── tests/                                42 tests

schemas/                                  vendored JSON Schemas, never fetched at load time
dist/                                     GENERATED, committed (see #16 for moving it to CI)
clients/                                  GENERATED config files
docs/verification/phase-0.md              what was verified, and what disproved an assumption
docs/phase-1.md                           Phase 1 results
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

Phase 0 (verification) and Phase 1 (plugin + compiler) are done. Containers, the gateway, and
live MCP tooling are Phase 2 and 3, so `tools/list` does not resolve yet — `scripts/test.sh`
reports that as an expected skip rather than a failure.

Issues: [#11 epic](https://github.com/warpcode/cloakai/issues/11) ·
[#12 Phase 0](https://github.com/warpcode/cloakai/issues/12) ·
[#13 Phase 1](https://github.com/warpcode/cloakai/issues/13) ·
[#14 Phase 2](https://github.com/warpcode/cloakai/issues/14) ·
[#15 Phase 3](https://github.com/warpcode/cloakai/issues/15)
