# Gateway design

**Issue:** [#17](https://github.com/warpcode/cloakai/issues/17) · **Epic:** [#11](https://github.com/warpcode/cloakai/issues/11)
**Decision:** [Config 2 — all traffic routes through the gateway](gateway-verification.md#decision-2026-09-30--config-2-all-traffic-goes-through-the-gateway)

No gateway code yet. This is the design, with the load-bearing parts measured.

---

## The one property everything rests on

The hop counter only works if the gateway can tell a client from an agent. Under Config 2 it can, and
**the mechanism is the kernel, not a header.**

The gateway is dual-homed and binds **one listener per interface address** — not `0.0.0.0`:

```
┌─ cloakai-egress ─────────────────────────────────────────────────────────┐
│   remote clients ────▶ ┌──────────────────────────────────────┐           │
│                        │ GATEWAY                              │           │
│                        │  :4483 CLIENT  bound to <egress-ip> │           │
│                        └──────────────────────────────────────┘           │
└───────────────────────────────────────────────────────────────────────────┘
┌─ internal: true ─────────────────────────────────────────────────────────┐
│   agents ────────────▶ ┌──────────────────────────────────────┐           │
│                        │ GATEWAY                              │           │
│                        │  :4484 AGENT   bound to <internal-ip> │           │
│                        │  holds the docker socket             │           │
│                        └──────────────┬───────────────────────┘           │
└───────────────────────────────────────┼───────────────────────────────────┘
                                        │ spawns, never serves
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
             ┌─────────────┐                         ┌─────────────┐
             │   agent A   │                         │   agent B   │
             └─────────────┘                         └─────────────┘
```

Measured, binding each listener to its own address:

| Probing from | client listener `:4483` | agent listener `:4484` |
|---|---|---|
| `cloakai-egress` | **reachable** | **unreachable** |
| `cloakai-internal` | **unreachable** | **reachable** |

Symmetric and strict. A container on `internal` cannot open the client listener, and a client on
`egress` cannot open the agent listener. **Neither can forge the other's provenance**, because there is
no route to the socket, not because a rule declines them.

For contrast, binding to `0.0.0.0` makes **both** listeners reachable from **both** networks. That
was the first thing tried, and it is why the binding is to a specific address. This is the sharpest
version of the property that has been tested in this project: a security boundary that exists only
because a socket is bound to one address.

---

## Call provenance

**Provenance is determined by arrival interface, never by a request header.**

```
client → :4483   ⇒  origin=client, depth=0
agent  → :4484   ⇒  origin=agent,  depth=parent+1
```

Consequences, each of which is the whole reason for the design:

- **Inbound provenance headers are stripped, not read.** A client claiming `depth: 0` is already depth
  0. An agent claiming `depth: 0` lands on the agent listener and is recorded as `parent+1`. A header
  cannot lower a caller's depth, because the header is not consulted.
- **The hop limit is enforceable** because the gateway observes every call in the system. This was
  impossible under direct peer calls, which is what Config 2 was chosen to fix.
- **The audit trail is a by-product** rather than a separate mechanism: origin and depth are known at
  the moment of routing, before any container exists.

---

## Static tool manifest

MCP clients hold a session to the server. If the server were a container that died per call, the
session would die with it and `tools/list` would flicker as containers came and went.

So the gateway is **long-lived and owns the manifest**, and the manifest describes tools that no running
process is currently serving. It is read from the compiled plugin, never from live discovery — which is
also why ToolHive's discover-and-health-filter model was rejected.

**Source of truth:** the compiled plugin (`dist/<plugin>/agent.yaml`), which the compiler already
generates from `agent.json`.

**Shape**, derived from the container's verified `mcp` mode:

```jsonc
{
  "version": 1,
  "maxHops": 3,
  "maxConcurrentCalls": 3,
  "agents": [
    {
      "name": "dev",
      "image": "cloakai/dev:latest",
      "tool": {
        "name": "dev",
        "description": "Development agent. Reviews changes for correctness…",
        "inputSchema": {
          "type": "object",
          "properties": { "message": { "type": "string" } },
          "required": ["message"],
          "additionalProperties": false
        },
        "outputSchema": {
          "type": "object",
          "properties": { "response": { "type": "string" } },
          "required": ["response"],
          "additionalProperties": false
        }
      }
    }
  ]
}
```

Those schemas are copied from what the container actually serves, so the manifest cannot drift from
the agent it describes — verified by the conformance check that reads `tools/list` from a live
container.

**The compiler should emit this file** (`dist/<plugin>/gateway.json`) for the same reason `agent.yaml`
is generated: a runtime that re-derives it can drift from the plugin.

---

## Per-call container lifecycle

One `tools/call` is one container. That is natural here rather than awkward: the container's `mcp` mode
exposes the agent as a single tool taking `{message}` and returning `{response}`, so a call *is* an
agent invocation.

```
tools/call  "dev" {message}
      │
      ├─ 1. check hop depth            ── refuse if depth > maxHops
      ├─ 2. acquire concurrency slot   ── queue; never reject (Q3)
      ├─ 3. docker create + start       ── isolation flags from agents/isolation-flags
      ├─ 4. wait for the listener      ── bounded; the cost is measured (Q4: ~190ms)
      ├─ 5. proxy the call over :8081  ── SSE-framed, stateless
      ├─ 6. collect + release slot
      └─ 7. docker rm -f               ── AND the reaper, because step 7 can be skipped
```

**Step 7 is the reaper's reason to exist.** If the gateway dies between step 3 and step 7, the
container survives — measured, not assumed. Every container carries `cloakai.call`,
`cloakai.started`, `cloakai.instance`, and `scripts/cloakai-reap.py` kills anything older than the
maximum call lifetime. The reaper must not live in the gateway, because the gateway dying is exactly
the case it exists to handle.

---

## Concurrency

**Memory binds before CPU**, measured: 12 concurrent containers put a 4-core host at load 4.92, but at
`--memory=4g` each, a 16 GB host sustains roughly **3**.

- A **global cap**, derived from available memory, not core count.
- **Queue, not reject.** A rejection invites a model's retry storm; a bounded queue degrades. The queue
  must itself be bounded, or it becomes the unbounded resource the cap was meant to prevent.
- The cap is a **memory-safety property**, not a throughput knob. Treating it as throughput is how it
  stops being enforced.

---

## What the gateway must never do

| Rule | Why |
|---|---|
| Execute agent code in its own process | It holds the Docker socket. Running agent code there makes it the highest-value target in the system. |
| Trust a provenance header | Provenance is arrival interface. A trusted header is forgeable by the thing being bounded. |
| Discover tools from running containers | Causes `tools/list` to flicker, which breaks client sessions. |
| Listen on `0.0.0.0` | Makes both listeners reachable from both networks — measured, destroys the provenance boundary. |
| Put the reaper in-process | The gateway dying is the case the reaper exists for. |
| Open a peer-to-peer route | The decision is that agents do not talk directly. The gateway must not create the path it exists to prevent. |

---

## Test plan

The suite's standard is that a test must be able to fail, and that falsifiability is demonstrated —
not asserted. Every row below has a named way to break it.

| # | Assertion | Broken by |
|---|---|---|
| 1 | Agents cannot open the client listener | bind to `0.0.0.0` instead of the egress address |
| 2 | Clients cannot open the agent listener | same |
| 3 | A forged `depth: 0` header does not lower a caller's depth | read the header |
| 4 | A call past `maxHops` is refused | remove the depth check |
| 5 | `tools/list` is identical with zero and many calls running | discover from live containers |
| 6 | A client session survives a call | per-call listener |
| 7 | Concurrency never exceeds the cap | remove the semaphore |
| 8 | A call at the cap queues rather than erroring | reject instead of queue |
| 9 | A gateway killed mid-call leaves no container past the lifetime | remove the reaper |
| 10 | A spawned container carries the full isolation flag set | drop a flag |
| 11 | The gateway's own process never runs agent code | — |
| 12 | No peer-to-peer network path exists | — |

Test 1 and 2 are the ones to write first. They are cheap, and they are the property the entire
security argument rests on.

---

## Open before implementation

- **`maxHops` default.** 3 in the sketch above is a placeholder. The measurement that informs it: the
  agent loop is a tool call, so depth equals nesting of agent invocations, not network hops.
- **Manifest freshness.** If the plugin changes, does the gateway reload, refuse, or keep serving the
  old manifest? A stale manifest that still answers `tools/list` is the same failure shape as a
  silently-skipped skill.
- **Which `image` the gateway spawns.** `agent.json` carries it, so this follows from consuming
  `dist/<plugin>` rather than re-deriving it.
