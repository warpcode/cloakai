# agents/

The container that wraps the agent. Not the agent's configuration — that lives in
[`../plugins/dev/`](../plugins/dev/), and it is compiled into
[`../dist/dev/`](../dist/dev/) before the image is built.

## Layout

```
agents/
├── dev.Dockerfile              the image: two modes off one config
├── cloakai-entrypoint.sh       run | mcp | shell
├── cloakai-healthcheck.sh      a real MCP probe for compose
└── isolation-flags             THE flag set, shared by run.sh and the tests
```

## `dev.Dockerfile`

Two modes off one image, so there is no per-mode image to maintain:

| Mode | What it does |
|---|---|
| `run` | The harness CLI, with your project directory mounted. The daily path. |
| `mcp` | `docker agent serve mcp` over HTTP, exposing the agent as one MCP tool. |
| `shell` | A shell, for debugging. |

`run` mode is what `./scripts/run.sh` uses. `mcp` mode is what a gateway would reach, and what
`docker compose up` starts as a supervised service.

## `isolation-flags`

The isolation posture, as a shell word list. It is read by **both** `scripts/run.sh` and
`scripts/isolation-tests.sh`, so the container you run and the container that is tested cannot drift
apart. Every flag carries the reason it exists in a comment beside it.

The set is not cargo-culted. Each flag was derived by escalating one at a time and reading the next
failure — see [`../docs/verification/phase-0.md`](../docs/verification/phase-0.md) Check 3. The four
`--tmpfs` mounts are each required, and each was found only by reading the next `EROFS`.

## Two things that are deliberately absent

**`--privileged`.** Root-equivalent on the host.

**`/var/run/docker.sock`.** Lets the agent start sibling containers, which defeats the entire
isolation model. A test asserts neither appears anywhere in the repository.

## The image ships generated output

The Dockerfile copies `dist/dev`, not `plugins/dev`. That way the image and a locally installed
plugin are guaranteed to be the same thing, because both come from the compiler.

`ARG PLUGIN` selects a different tree if you build one: `PLUGIN=other ./scripts/build.sh`.
