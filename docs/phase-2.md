# Phase 2 — Isolated container runtime

**Issue:** [#14](https://github.com/warpcode/cloakai/issues/14) · **Epic:** [#11](https://github.com/warpcode/cloakai/issues/11)
**Depends on:** [Phase 0](verification/phase-0.md) Checks 1, 3, 4 — and [Phase 1](phase-1.md)

---

## Verdict

**The isolation holds, and the tests can fail.** 11 assertions pass, and breaking the isolation
deliberately makes them fail. That second half is the part that matters: a suite that can only pass
manufactures false confidence, which is worse than having no tests.

```console
$ ./scripts/isolation-tests.sh
  ✓ 1. no Docker socket in the agent container
  ✓ 2. agent container cannot reach the internet (curl returned '000')
  ✓ 3. DNS resolves 'litellm' by compose service name
  ✓ 3b. agent reached litellm over HTTP by name (status 200)
  ✓ 4. capability container on internal+egress CAN reach the internet
  ✓ 5. two concurrent invocations cannot see each other's files
  ✓ 6. root filesystem is read-only
  ✓ 7. all capabilities dropped (CapEff=0)
  ✓ 8. a complete model call succeeds through the proxy, under full isolation
  ✓ 8b. the agent used a scoped virtual key, not the master key
  ✓ 8c. the agent config does not contain the master key

  11 passed, 0 failed, 0 skipped
```

The `mcp` mode serves a real `tools/list` with the agent as a tool, its description taken from
`agent.json`.

---

## Proof the tests can fail

Each of these was done deliberately, and each produced a specific failure:

| Regression introduced | Assertion that fired |
|---|---|
| Removed `--read-only` from the flag set | **6** — "root filesystem is writable" |
| Removed `--cap-drop=ALL` | **7** — "capabilities reduced to a non-default set (CapEff=0x…a80425fb)" |
| Pointed the agent at a non-internal network | **2** — "AGENT REACHED THE INTERNET (curl returned '200')" |

The third is the important one. It is the assertion the whole project rests on, and it genuinely
detects the breach rather than passing because the network happened to be down.

---

## What was built

| Piece | Where |
|---|---|
| Agent image, two modes off one config | `agents/dev.Dockerfile` + `agents/cloakai-entrypoint.sh` |
| Isolation flags, shared | `agents/isolation-flags` |
| Run script | `scripts/run.sh` |
| Network topology | `infra/compose.yml` |
| Proxy + credential store | `infra/litellm.yaml`, postgres service |
| Build script | `scripts/build.sh` |
| Isolation tests | `scripts/isolation-tests.sh` (wired into `scripts/test.sh`) |

`mcp` mode is `docker agent serve mcp /agent/agent.yaml -a dev --http --listen 0.0.0.0:8081
--insecure-no-auth`. No hand-written wrapper was needed, because Phase 0 Check 1 passed.

---

## The one-file thesis still holds

Phase 2 needed a docker-agent config to make `mcp` mode work, and it is *generated*, not committed
by hand: `dist/dev/agent.yaml` and `dist/dev/instructions.md` derive from `agent.json` via a new
`emit_runtime()` in the compiler. So changing the runtime still means editing one file.

Two things that had to be learned the hard way, both now documented in the generated file's header:

- **`instruction_file` is relative to the config file.** An absolute path is rejected outright with
  *"must be a local relative path inside the config directory"* — which is easy to hit, because the
  file lives at `/agent/agent.yaml` in the image and the natural thing to write is the absolute path.
- **`agent.json` gained a `model` block**, because the config's model must resolve to something.
  Without it the config is rejected with `model '' not found in configuration`.

---

## Findings from Phase 2

### 1. The issue's Dockerfile sketch would not have built

Three corrections, all of which are silent failures rather than errors:

- **`ghcr.io/docker/docker-agent` does not exist** (Phase 0). It is `docker/docker-agent` on Docker Hub.
- **`ENTRYPOINT ["docker-agent"]` is wrong** — the binary is at `/docker-agent`, and there is no
  `docker-agent` on `PATH` in that image.
- **The base must be glibc.** opencode's installer produces a GLIBC binary, so it cannot run on
  Alpine. The obvious move — use the docker-agent image as the base since it is already Alpine — fails
  with `Error relocating … _ZTVN10__cxxabiv117__class_type_infoE: symbol not found`. The reverse
  works: `/docker-agent` is statically linked, so it runs fine on `debian:bookworm-slim`.

The opencode installer also needs `bash` and `--no-modify-path`; without them it exits 2 inside a
container with no useful message.

### 2. The flag file was a trap until it was shared

The flags started duplicated between `run.sh` and the tests. The tests then used their own copy —
and test 6 passed on a container the tests had not actually isolated. Reading a line-per-flag file
into a shell array also had a second bug: `--tmpfs /tmp` became a *single* argument named
`--tmpfs /tmp`, which Docker rejects, silently dropping the whole flag set.

Both are why the flags now live in `agents/isolation-flags`, read by both scripts with per-line word
splitting, with the reason for every flag next to it. **The thing that is tested and the thing that
is used cannot drift apart.**

### 3. litellm needs Postgres, and fails in a way that looks like something else

LiteLLM authenticates every request against a key database. Without one it returns HTTP 400 with
`No connected db.`, which opencode surfaces as `AI_APICallError: No connected db.` — a message that
reads like a client bug and sent me looking in the wrong place entirely.

SQLite is not accepted: virtual keys and spend tracking require Postgres. That is not incidental
overhead — a scoped key with a spend cap is only meaningful if something durable records the spend.
So `postgres` is a service in the topology, on `internal` **only**: the agent can reach the
credential store, and the credential store cannot reach the internet.

Two further litellm gotchas, both silent:

- **`CONFIG_FILE_PATH` must be set explicitly.** Without it the image never reads the mounted
  config. The proxy starts, authenticates keys correctly, and then reports *no models at all* —
  surfacing as `Invalid model name passed in model=default` on the request, not as a config error.
- **Key aliases must be unique.** A hardcoded alias makes the test pass exactly once and fail on
  every run after, with a 400 reading `Key with alias ... already exists`. The alias now includes the
  PID and a timestamp.

### 4. Five defects found in review

All of them were invisible to the tests as written, which is the point of recording them.

- **`run.sh` mounted the wrong directory.** `PROJECT_DIR="${CLOAKAI_PROJECT:-$PWD}"` ran *after*
  `cd "$(dirname "$0")/.."`, so invoking the script from any other project mounted **cloakai's own
  source** into `/workspace` and ran the agent on the wrong tree. The caller's directory is now
  captured before the `cd`.
- **`run.sh` could not actually reach a model.** It passed an empty `OPENCODE_CONFIG_CONTENT` and
  nothing else, so a bare `./scripts/run.sh "prompt"` failed on missing credentials — the one way a
  user is likely to invoke it. It now mints a scoped virtual key and points the harness at the proxy,
  exactly as the isolation tests do, and says so on stderr.
- **Test 5 could pass by not running.** If either container failed to start, both `docker exec`
  calls returned empty, which is indistinguishable from isolation. It now asserts both containers
  are up and self-readable *before* checking cross-visibility, and reports "could not run" as a
  failure. Verified: pointing the flags at a nonexistent network turns it red.
- **`PLUGIN` was a build arg that did nothing.** The Dockerfile hardcoded `COPY dist/dev`. It is now
  `ARG PLUGIN=dev` + `COPY dist/${PLUGIN}`, so `PLUGIN=custom` builds that tree.
- **litellm raced postgres.** No `depends_on`, so the proxy could hit an unready database on first
  boot and produce the same misleading `No connected db.` as a genuinely missing one. Now
  `condition: service_healthy`, and compose visibly waits.

Plus two smaller ones: `yaml_scalar` claimed to escape newlines and did not (now `json.dumps`, which
is correct for YAML 1.2 double-quoted scalars, since they use JSON escapes), and `agent.json` had
drifted from the container entrypoint — it named `/agent.yaml`, which does not exist in the image,
and a `run` command the script never ran. Both are now consistent, and a test asserts
`agent.json` and `agents/cloakai-entrypoint.sh` agree, because nothing else would have caught it.
That test immediately found the second drift.

### 5. The credential story is now real, not asserted

Test 8 does not just check that a model call works. It mints a **scoped virtual key** — one model,
one dollar, 24 hours — and uses that. Test 8c asserts the master key does not appear in the agent's
config at all.

That is the whole point of the proxy: a prompt injection inside the container cannot spend a
provider credential, because the container never holds one.

---

## Test 8 and the mock upstream

There is no provider credential on this machine, so `infra/mock-upstream.py` stands in for one
behind the `test` compose profile. It is deliberately *behind* litellm, not a replacement for it:
opencode → litellm → Postgres key lookup → HTTP → response is entirely real. Only the final model
call is canned, and the response says so.

Point `OPENAI_API_BASE` at a real provider and nothing else changes.

---

## Not verified, and why

- **VS Code / OpenHands / Claude Code still have not loaded the generated plugin** (carried from
  Phase 1). Unchanged and still one command from checking.
- **The gateway does not exist.** The generated `mcp.json` points at `cloakai-gateway:4483`, which
  Phase 3 will serve. The container's own `mcp` mode works and is verified above; the *client-facing*
  URL is not yet resolvable, which is expected and is not a defect.
- **No gateway, no per-call container spawning.** Explicitly out of scope for this phase.

---

## For Phase 3

- The container already runs, is already isolated, and already serves MCP over HTTP with a stable
  in-network address. Phase 3's job is to give that a stable *name* clients can reach, which is
  mostly a Compose concern rather than a code one.
- `emit_runtime()` already emits everything the gateway needs to know about an agent, and the
  gateway should read it rather than re-deriving it — otherwise the one-file thesis breaks here.
- Check #18 (non-loopback HTTPS for the gateway URL) becomes live in Phase 3. Phase 0 established
  that no tested client enforces the rule, so `http://` is fine for now and the stdio shim stays
  unused.
