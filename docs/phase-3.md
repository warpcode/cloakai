# Phase 3 — Compose integration

**Issue:** [#15](https://github.com/warpcode/cloakai/issues/15) · **Epic:** [#11](https://github.com/warpcode/cloakai/issues/11)

---

## Verdict

`./scripts/dev.sh up` gives a healthy, isolated, DNS-reachable agent container, and the isolation
tests pass under compose exactly as they did under `docker run`. **17 assertions, 0 failures.**

```console
$ ./scripts/dev.sh test
isolation parity
  ✓ read_only matches --read-only
  ✓ cap_drop matches --cap-drop=ALL
  ✓ security_opt matches --security-opt no-new-privileges
  ✓ pids_limit matches --pids-limit=1024
  ✓ mem_limit matches --memory=4g (4294967296 bytes; compose: 4294967296 bytes)
  ✓ cpus matches --cpus=2
  ✓ tmpfs mounts match (4 of them)
  ✓ the agent is on internal ONLY
  ✓ no ports are published
  ✓ not privileged
  ✓ the word list contains no --privileged
  ✓ the word list mounts no docker socket

isolation
  ✓ 0.  the compose-managed agent is up and can reach the proxy
  ✓ 0b. the compose agent's root filesystem is read-only
  ✓ 0c. the compose agent has no Docker socket
  ✓ 0d. the compose agent has all capabilities dropped
  ✓ 1–9 (Phase 2's suite, unchanged)
```

**Nothing is automated yet.** No gateway, no per-call spawning, no scheduler. `dev-agent` runs
because you asked. This phase is plumbing — what it buys is that the isolation flags live in one
versioned file rather than someone's shell history, and that the container survives a supervisor.

---

## The gap this phase closed

Phase 2's isolation tests all used **throwaway containers** built from the same word list. That left
a real hole: the long-lived container a supervisor actually keeps running is a *different object*,
created by compose from YAML rather than from the word list. If compose silently dropped a flag,
every Phase 2 test would still pass.

Two things close it:

- **`scripts/check-isolation-parity.sh`** compares the compose service against
  `agents/isolation-flags` field by field. Verified falsifiable — removing `cap_drop` from the
  service turns it red.
- **Tests 0–0d** exercise the running compose container itself: a real model call, and the three
  isolation properties that matter, checked on the object that will actually be kept alive.

Parity of *values* is not proof of *applied* isolation, which is why both exist.

---

## Findings

### 1. Compose could not read the flags, so the flag set now exists twice

Compose has no way to read a file. `agents/isolation-flags` and the compose service therefore state
the same isolation separately, and nothing made them agree. The parity script is the guard, and it
has already earned its place: writing it caught **three of its own checks silently skipping**,
because the parser only understood `--flag value` and the file uses `--flag=value`. A check that
cannot run is not a check, so a missing flag is now reported as a failure rather than skipped.

### 2. `docker compose up --wait` was not waiting

`litellm` had no healthcheck, so `--wait` treated it as ready the moment it started. `dev.sh up`
would have reported success before the proxy could answer a request. Both `litellm` and
`mock-upstream` now have real probes, and `up` visibly gates on them.

### 3. A healthcheck that cannot detect a wedged service is worse than none

The issue's suggested `docker-agent --version` proves the binary runs, not that the service is
serving. Since `depends_on: service_healthy` gates on the result, a weak probe would have made the
whole stack gate on a lie. `cloakai-healthcheck.sh` performs a real MCP `initialize` round trip.

`run` mode has no equivalent probe — it is a one-shot process, not a service — so its check is only
that the container stays up. That asymmetry is real and is documented rather than papered over.

### 4. Resource limits: asserted rather than assumed

The issue flags that `deploy.resources.limits` may not apply, and asks for it to be checked once. It
does apply — test 9 proves it by allocating 400 MB under a 96 MB limit and confirming
`OOMKilled=true`, exit 137, with the host unaffected.

Getting there took four attempts, because a memory limit is only a real test if the allocation is
actually *retained*: `head | tr > /dev/null` discards as it goes and never allocates, and awk
string concatenation is quadratic enough to be unusable. The test now uses perl, and `--project-name`
pins the container name — compose otherwise derives it from the directory, and the test file lives
in a `mktemp` dir.

### 5. Two compose interpolation traps

Both eat shell syntax silently, and both cost time:

- **`$$` is required in a compose file.** A perl one-liner containing `$x` arrived as `-e "my  ="`.
- **`mem_limit` is reported in bytes** by `config --format json`, whatever spelling the YAML used.
  The parity check compares values, not strings.

---

## Decisions

**The project directory is `${AGENT_PROJECT:-..}`, not a fixed path.** The issue's three options
trade simplicity against flexibility; the env-var default keeps one compose file working across
projects and makes the mount point explicit at invocation. A hardcoded path in a compose file is
exactly the thing that quietly breaks someone six months later.

**No published ports.** `dev-agent` is reachable as `dev-agent:8081` from `internal` and nowhere
else — verified: `docker port` returns nothing and the host cannot reach it. A published port on a
network shared with an untrusted agent is a hole in the design, not a debugging convenience.

**`restart: unless-stopped`, on this service only.** Correct for a long-lived container. It would
mask crashes on a per-call container, and per-call containers are created with `--rm`.

**`down` does not use `--remove-orphans`.** It would remove containers belonging to other compose
projects that share the `cloakai-internal` network.

**The project directory is pinned via `--project-directory`, and every config mount is an absolute
path.** These are not independent. `--project-directory` also changes how *every* relative mount
resolves, so a relative `./litellm.yaml` started pointing at the repo root, docker created an empty
**directory** at that path, and litellm came up with an empty model list serving 400s. A missing mount
source does not fail a compose invocation — it silently becomes a directory, which is worse than a
hard error. `dev.sh` exports absolute config paths and refuses to start if either is not a real file.

---

## Acceptance criteria

- [x] `docker compose up` brings up litellm and the agent, and reports healthy — `dev.sh up` gates
      on health and prints per-service state
- [x] The agent is reachable by DNS name on `internal`; **no ports are published** — `tools/list`
      returns a non-empty list from a peer container, and the host cannot reach it
- [x] The `mcp` service serves a non-empty `tools/list` from inside the `internal` network
- [x] Resource limits demonstrated to apply — `OOMKilled=true`, exit 137, host unaffected (test 9)
- [x] The project directory mount strategy documented, and why it was chosen
- [x] `scripts/dev.sh` supports `up`, `logs`, `down`, `shell`, `test` (plus `ps`)
- [x] README documents the topology and states plainly that nothing is automated yet
- [x] A real client (OpenHands) loads the generated skills **and sees a non-empty `tools/list`**
- [x] `agents/README.md` rewritten — the "config file name is not fixed yet" ambiguity is gone, and
      the file now describes the container rather than re-asserting a resolved question
- [x] The Phase 2 isolation tests still pass unchanged under compose

---

## Not done, and not pretending

- **VS Code has still not loaded the generated plugin, and there is a concrete reason.** A
  containerised test is technically possible — code-server ships the agent-plugins system
  (`chat.pluginLocations`, `agentPluginsHome`, `componentPaths` are all present in its workbench) —
  but its workbench is lazy-loaded and only starts on a browser connection, and the chat subsystem
  that consumes the plugin needs the Copilot extension authenticated. **The blocker is a signed-in
  session, not an install**, so a container does not unblock it.
  OpenHands is now covered for real; see `scripts/conformance.sh`.
- **No gateway.** The client-facing MCP URL in the generated plugins points at
  `cloakai-gateway:4483`, which Phase 3 does not serve. The *container's* MCP endpoint works and is
  verified above; only the address a client would use is still aspirational.

---

## For the gateway

- `dev-agent` already serves MCP on a stable DNS name with a real healthcheck. A gateway needs
  little more than discovery plus a client.
- It should read `dist/dev/agent.yaml` (generated from `agent.json`) rather than re-deriving what an
  agent needs, or the one-file thesis breaks at the last step.
- The credential story is already correct end to end: the gateway can mint a scoped virtual key
  exactly as `run.sh` and the tests do, without ever handling a provider credential.
- Issue [#18](https://github.com/warpcode/cloakai/issues/18) becomes live here. Phase 0 established
  that no tested client enforces the non-loopback HTTPS rule, so `http://` is still fine and the
  stdio shim remains unused.
