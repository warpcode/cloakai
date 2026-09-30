# Gateway verification — the seven open questions, answered by measurement

**Issue:** [#17](https://github.com/warpcode/cloakai/issues/17) · **Epic:** [#11](https://github.com/warpcode/cloakai/issues/11)
**Method:** the same one Phase 0 used. Assume nothing, measure it. Host: 4 cores, 15.4 GB RAM, Docker 29.8.1.

No gateway code was written. This resolves the questions #17 says must be answered before building,
and **one of them turns out to be architecturally contradictory as written.**

---

## Summary

| # | Question | Answer | Confidence |
|---|---|---|---|
| 1 | Proxy or pipe? | **Proxy.** It is the only path already verified to work. | High — evidence exists |
| 2 | Timeouts and cancellation | **A container survives its gateway dying.** A reaper is mandatory and must live outside the gateway. **Built and tested.** | High — reproduced + tested |
| 3 | Concurrency limits | **Memory binds before CPU**, at roughly 3 concurrent with the real flags. Cap and queue. | High — measured |
| 4 | Cold-start cost | **Warm pool saves 119 ms/call (38%), payback after ~4 calls.** The claim is right. | High — measured |
| 5 | Hop limit | **DECIDED — implementable.** All traffic routes through the gateway, so the counter has something to count. | Decided |
| 6 | Agents and the gateway | **DECIDED — yes, by design.** The gateway is the only agent-to-agent route. | Decided |
| 7 | Auth | The primitive **already exists** and was verified in Phase 0. | High — prior evidence |

---

## Q1 — Result transport: proxy, not pipe

**Answer: proxy the container's `mcp` endpoint over `internal`.**

This is less a choice than a consequence of what has been verified. The container's `mcp` mode
serves real streamable-HTTP MCP today: Phase 3 confirmed an OpenHands client in a container reaching
`dev-agent` by DNS name and getting `dev` back from `tools/list`. That path is built, tested, and
falsifiable.

Piping would mean `opencode run` speaking MCP on stdout. It does not — it emits formatted text. So
piping is not a simplification of a working path; it is a new protocol surface with none of the
verification behind it, and it loses SSE framing, `tools/list` events, and tool-call events.

The cost of proxying is that the gateway must wait for a container to be listening before forwarding,
which adds the start latency measured in Q4 to every call. That is known and quantified, which is
better than an unmeasured alternative.

---

## Q2 — Timeouts and cancellation: containers outlive their gateway

**Answer: the default must be a hard timeout enforced by a reaper that does not live in the gateway.**

This was the most important thing to check, and the answer is the uncomfortable one:

```console
# start a per-call container, then destroy the thing that created it
$ docker create ... --label cloakai.test=orphan cloakai/dev shell -c "sleep 300"
$ docker start <cid>
# ...the gateway dies...
$ docker ps -q --filter "label=cloakai.test=orphan" | grep -q . && echo LEAK
RESULT: container SURVIVED the gateway dying  -> LEAK
```

**Docker has no ownership concept.** A container's lifetime is not tied to the process that created
it. `--rm` only cleans up when the container *exits*; it does nothing about a hung one. A gateway
that is OOM-killed, crashes, or is `docker kill`ed leaves behind a container holding up to 4 GB until
it finishes on its own — and an agent that hangs may never finish.

**Therefore: the reaper cannot be the gateway.** The gateway dying is precisely the case the reaper
exists to handle, so the thing that does the reaping must be something whose death is not the failure
mode. Options are a separate supervised process, or a sidecar.

The reaper is built and tested: `scripts/cloakai-reap.py`, with `scripts/test_reaper.py` proving the
behaviours that matter. Label every per-call container with `cloakai.call=1`,
`cloakai.started=<epoch>` and `cloakai.instance=<id>`, then `docker rm -f` anything carrying the label
whose **age** exceeds the maximum call lifetime. That lifetime *is* the timeout — there is no
separate mechanism, which is a simplification worth having.

**The age check is load-bearing, and it is the only thing that makes a cross-instance sweep safe.**
"Kill everything with our label on startup" is wrong: during a rolling restart a new instance would
kill a live peer's in-flight calls. Measured both ways:

```console
B startup sweep:  0 killed  (peer's call is young)
A's LIVE call survived    (correct)
... past max lifetime ...
B periodic sweep: 1 killed  (peer's call is now hung)
A's hung call was reaped (correct)
```

An instance id is recorded, but only so a log line can say whose call leaked. **Safety does not
depend on it** — age is the whole rule. Verified by removing the age check: the tests fail with "THE
REAPER KILLED A LIVE CALL — it is too eager".

**Run both modes.** `--once` at startup catches the overwhelmingly common case of a supervised
restart, and is nearly free because a gateway that just started cannot have many orphans. `--interval`
is what actually bounds the leak, because startup cleanup does nothing while the gateway is *down*,
and nothing at all if it is killed and never restarted. Two of the reaper's tests cover what makes it
safe rather than merely present: an unlabelled container is never considered, and one whose age cannot
be determined is spared rather than killed on a guess.

**Two consequences for the design:**

1. The timeout value bounds the *worst-case leak*, not just a slow call. A 10-minute timeout with 4 GB
   per container means 10 minutes × concurrency × 4 GB of worst-case exposure. This makes Q3's cap a
   memory-safety question, not a throughput one. The reaper's default is 900 s — generous enough
   that a legitimate long call is never reaped out from under the model, which is the failure mode
   that would make a reaper worse than none.
2. Client disconnect is **not** sufficient on its own. TCP close is observable, but a client that
   vanishes without closing must still be caught by the age sweep.

---

## Q3 — Concurrency: memory binds first

**Answer: cap globally, queue rather than error, and pick the cap from memory rather than CPU.**

Measured by starting N containers at once:

| Concurrent | Wall time to start all | Load average (4 cores) |
|---|---|---|
| 6 | 994 ms | 2.99 |
| 12 | 2297 ms | **4.92** |

At 12 the host is already oversubscribed on CPU. But the real flags are `--memory=4g --cpus=2`, and
the binding constraint is not CPU at all:

```
memory: 15.4 GB total
4 GB per container  →  3 concurrent containers before the host is at risk
2 cpus per container →  4 concurrent on 4 cores
```

**Memory binds at ~3, CPU at ~4.** So the cap should be derived from available memory, not core
count, and a fixed default of 3–4 for a 16 GB host is right.

**On the failure mode: queue, not error.** An error tells the model its call failed and invites a
retry storm; a bounded queue with a timeout degrades instead. The queue must be bounded, or it
becomes the unbounded resource the cap was meant to prevent.

Per-agent semaphores on top of a global cap are worth it, but only if there is more than one agent. With
one, the global cap is the whole policy.

---

## Q4 — Cold start: the warm pool claim is correct

**Answer: build it, but only if short calls matter. The saving is real and modest.**

The issue predicted that `docker create` is cheap because layers are cached, and that a warm pool
gives "most of the latency win" for ~30 lines. **Both halves check out**, with a caveat about what
was measured.

My first benchmark appeared to refute it — both paths ran `opencode --help` and were dominated by the
~900 ms the command takes, so the lifecycle difference was invisible. That was a confounded
benchmark, not a finding. With a trivial payload held constant:

| Path | Median |
|---|---|
| `bash -c true` (no container) | 2.9 ms |
| **`docker run --rm`** (per call) | **310.2 ms** |
| `docker create` + `rm` | 29.4 ms |
| `docker start` (container already created) | 183.5 ms |
| `create + start + rm` **at call time** | 311.9 ms — *saves nothing* |
| **`start` from a pre-created warm pool** | **191.5 ms** |
| `docker exec` into a long-lived container | 120.5 ms — *not isolated* |

**A warm pool saves 118.7 ms per call, 38% of the run path, and costs 42 ms of pre-creation per
container — so it pays for itself after about 4 calls.**

Note the row that says "saves nothing". Pre-creating *at call time* is not a warm pool; it pays the
same 29 ms and gains nothing. The pool only pays if the containers are created **before** the call.

**The caveat that should decide whether to build it:** 119 ms is 38% of a trivial payload and about
**4% of a real model call**, which takes seconds. The warm pool is worth building if the gateway will
ever serve fast, cheap calls. If it only ever serves model calls, it is 30 lines of extra failure
surface — stale containers, a pool that must be refilled, another thing to leak — for a rounding
error. `scripts/measure_coldstart.py` reproduces all of this; raw numbers in
`docs/gateway-coldstart.json`.

`docker exec` at 120 ms is the prize being declined, and it is worth naming: it is 2.6x faster than
even a warm pool and it is exactly the isolation this project exists to provide. That gap is the
justification for the whole design, so it should never be quietly taken.

---

---

## DECISION (2026-09-30) — Config 2: all traffic goes through the gateway

**Agents must not talk directly to each other. Every client call and every agent-to-agent call is
mediated by the gateway.**

This supersedes the recommendation below, which was Config 3 on the grounds that a depth *bound* was
enough. That reasoning was wrong, and the measurements already contained the correction.

```
┌─ client-facing ─────────────────────────────────────────────────────────┐
│                                                                            │
│   clients ──────▶ ┌────────────────────────────────────────────┐            │
│                   │ GATEWAY                                     │            │
│                   │  · holds the docker socket (spawns only)    │            │
│                   │  · STATIC tool manifest                     │            │
│                   │  · hop counter  ← only possible here       │            │
│                   │  · concurrency cap ← covers every spawn    │            │
│                   │  · call origin + depth, propagated          │            │
│                   └───┬─────────────────────────┬───────────────┘            │
└───────────────────────┼─────────────────────────┼─────────────────────────┘
                        │ spawns                 │ every A↔B call routes here
┌─ internal (true) ─────┼─────────────────────────┼─────────────────────────┐
│                        ▼                         ▼                         │
│                  ┌────────────┐           ┌────────────┐                   │
│                  │  agent A   │──────────▶│  agent B   │  via the gateway   │
│                  └────────────┘           └────────────┘                   │
│                                                                            │
│   agent A ──✗──▶ agent B     NO direct peer path exists on this network     │
│                                                                            │
│   agent A ──▶ gateway  ✓ BY DESIGN — it is the only agent-to-agent route  │
└────────────────────────────────────────────────────────────────────────────┘
```

### Why this is stronger than direct peer calls

It is not a preference between equals. Three of the properties that made direct peer calls unsafe
become **impossible rather than merely bounded**:

1. **The hop limit becomes implementable.** Q5's finding was that *no* gateway-side hop limit can exist
   if agents call each other directly — the call never passes through the gateway, so it can count the
   calls it is asked for but not the ones its containers make to each other. Routing everything through
   one chokepoint is the only shape where the counter has anything to count.
2. **Compromise containment.** With direct calls, a prompt-injected agent can hammer a peer's
   container with no mediation at all. Through the gateway, every A→B call is visible and can be
   rate-limited, depth-checked, and attributed.
3. **The concurrency cap now covers every spawn.** The gateway is the only component that creates
   containers *and* the only route by which anything reaches it. Under direct peer calls those were two
   separate paths, and only one was governed.

### The cost, stated plainly

- **Agents reach the component holding the Docker socket.** Bounded by the cap and the hop limit, but
  it is a real capability and the design should say so out loud rather than discover it later.
- **The gateway is a single point of failure and a throughput bottleneck.** Every call in the system
  crosses it.
- **The audit-trail claim is only as good as the propagation.** The gateway must carry call origin and
  depth on every request, or "we can tell who called what" is hollow.

### What this settles

- **Q5** — hop limit: now implementable, because the gateway observes every call.
- **Q6** — agents and the gateway: the answer inverts from "must not" to **must**, since it is the
  only agent-to-agent route. The gateway is reachable from `internal` deliberately.

No peer-to-peer route is opened on the network. A prompt-injected agent reaching a peer goes through
the same door a client does, which is what makes the hop counter and the cap mean anything.

## The topologies, drawn

Three requirements cannot all hold on one flat network:

1. agents call peers **directly** (the current design)
2. agents must **not** reach the gateway
3. the gateway **must** hold the Docker socket

Network attachment is not directional. Measured: everything on `internal` is mutually reachable,
A→B and B→A both work. So if the gateway and the agents share a network, requirement 2 fails
regardless of naming.

### Today

```
┌─ internal: true ──────── no route off this machine ─────────────────────┐
│                                                                          │
│   ┌─────────┐                          ┌─────────┐                      │
│   │ agent A │◀────────────────────────▶│ agent B │   every peer can      │
│   └────┬────┘       FULL MESH          └────┬────┘   reach every peer    │
│        │                                  │                           │
│        │  docker.sock = root on host      │                           │
│        └──────────────┬───────────────────┘                           │
│                       ▼                                               │
│              ┌─────────────────┐                                      │
│              │ GATEWAY         │◀──────── agents can reach it,         │
│              │ holds the       │           and it holds the socket    │
│              │ docker socket   │                                      │
│              └────────┬────────┘                                      │
└───────────────────────┼──────────────────────────────────────────────┘
                        │  only route off the host
                        ▼
                   the internet
```

### How reachability gets enforced — bind to one interface

The gateway is physically on both networks. It only **listens** on one.

```
┌─ network: client-facing ────────────────────────────────────────────────┐
│                                                                            │
│   clients ──── HTTP ────▶ ┌───────────────────────────────┐              │
│                            │ GATEWAY                        │              │
│                            │                                │              │
│                            │  LISTENING  on 10.0.1.5:4483  │              │
│                            │            ▲                   │              │
│                            │            └── this network    │              │
│                            │                                │              │
│                            │  NOT listening on 10.0.2.5     │              │
│                            └───────────────┬────────────────┘              │
└────────────────────────────────────────────┼───────────────────────────────┘
                                             │
┌─ network: internal (true) ─────────────────┼───────────────────────────────┐
│                                            │                               │
│   ┌─────────┐                        ┌─────┴──────┐                        │
│   │ agent A │◀──────────────────────▶│  agent B   │   peer calls: fine    │
│   └─────────┘                        └────────────┘                        │
│                                                                          │
│   agent A ──▶ 10.0.2.5:4483  ──▶  connection refused                     │
│                                     (nothing is bound there)             │
└──────────────────────────────────────────────────────────────────────────┘
```

Reachability is not blocked by a rule. It is **absent** — no listener on that address, so the kernel
refuses. No firewall, no forwarder, no extra container. About ten lines to discover its own address at
startup.

### The three configurations

These are not six independent choices. **Enforcing Q6 eliminates one of the Q5 options**, because an
agent that cannot reach the gateway cannot route peer calls through it. So they are three coherent
configurations.

#### Config 1 — one network per agent

```
┌─ net-a ──────────┐   ┌─ net-b ──────────┐   ┌─ client-facing ────┐
│                  │   │                  │   │                    │
│  ┌────────────┐  │   │  ┌────────────┐  │   │  clients ──▶ ┌────┴────┐
│  │  agent A   │  │   │  │  agent B   │  │   │             │ GATEWAY │
│  └────────────┘  │   │  └────────────┘  │   │             └────┬────┘
│                  │   │                  │   │                  │
└──────────────────┘   └──────────────────┘   └──────────────────┘
        ▲                                          │
        └──────────── can never talk ────────────────┘

  Q6 enforced (structurally)   Q5 moot — no peer calls exist
```

Q6 enforced structurally. Q5 is moot because the capability is gone. The cost is that agent-to-agent
delegation is gone with it, plus a network and a discovery mechanism per agent.

#### Config 2 — everything routes through the gateway

```
┌─ client-facing ─────────────────────────────────────────────────────────┐
│   clients ──────▶ ┌──────────────────────────────────────────┐            │
│                   │ GATEWAY  · docker socket · HOP COUNTER    │            │
│                   │ every call passes through here            │            │
│                   └───┬──────────────────────────┬───────────┘            │
└───────────────────────┼──────────────────────────┼────────────────────┘
                        │                          │
┌─ internal (true) ─────┼──────────────────────────┼────────────────────┐
│                        ▼                          ▼                    │
│                  ┌────────────┐            ┌────────────┐              │
│                  │  agent A   │───────────▶│  agent B   │              │
│                  └────────────┘  (via gw)  └────────────┘              │
│                                                                        │
│   agent A ──▶ gateway  ✓ REACHABLE — socket exposure, bounded by cap   │
└────────────────────────────────────────────────────────────────────────┘

  Q5 enforced   Q6 deliberately relaxed   agents CAN spawn containers
```

Q5 enforced, because the gateway sees every call and can count depth. Q6 is **deliberately relaxed**,
which means an agent can reach the component holding the Docker socket — bounded by the Q3 cap, but
reachable. It is also the only configuration where the hop counter is meaningful.

#### Config 3 — direct peers, gateway unreachable *(recommended)*

```
┌─ client-facing ─────────────────────────────────────────────────────────┐
│   clients ──────▶ ┌──────────────────────────────────────────┐            │
│                   │ GATEWAY  · docker socket · spawns only   │            │
│                   │ listening here; blind on 10.0.2.5        │            │
│                   └───┬──────────────────────────────────────┘            │
└───────────────────────┼────────────────────────────────────────────────┘
                        │ spawns
┌─ internal (true) ─────┼────────────────────────────────────────────────┐
│                        ▼                                                │
│                  ┌────────────┐            ┌────────────┐                │
│                  │  agent A   │◀──────────▶│  agent B   │  peers: direct │
│                  └────────────┘            └────────────┘                │
│                                                                        │
│   agent A ──▶ gateway  ✗ refused — no listener on this network          │
│                                                                        │
│   no depth counter anywhere; bounded by Q3's cap + peer container limits │
└────────────────────────────────────────────────────────────────────────┘

  Q6 enforced   Q5 NOT enforced   gateway stays small, agents stay isolated
```

### Compared

| | A↔B calls | Agent reaches gateway | Depth bounded by | Cost |
|---|---|---|---|---|
| **Today** | yes | **yes** | nothing | — |
| **Config 1** | impossible | no | n/a | lose delegation; dynamic networks |
| **Config 2** | via gateway | **yes** | hop counter | chokepoint; socket exposed |
| **Config 3** | direct | no | cap + container limits | no depth guarantee |

### Why Config 3 is safe enough

**Peer calls do not spawn anything.** A→B→A reuses long-lived `mcp` containers that already exist.
The only component that spawns containers is the gateway, and Q3's cap governs that.

So a runaway chain is bounded by the **peer container's own limits** — 1024 pids, 4 GB — rather than
by anything unlimited. The harm is load, not a 4 GB leak.

The residual cost is honest: there is **no hard depth guarantee**. That is a decision to stop defending
against a runaway chain, not a claim that one is impossible, and it holds only while the cap is
enforced and treated as a safety property rather than a throughput knob.

### The question that decides it

Does agent-to-agent delegation need to exist, and if so does it need a depth **proof** (Config 2's hop
counter, for audit or billing) or only a depth **bound** (Config 3)?

The plugin currently has **one agent**, so nothing needs peer calls today. Building Config 1 for a
capability nothing uses would be speculative complexity of the kind that has caused most of the
rework so far.

**Original recommendation was Config 3**, on the grounds that a depth bound was sufficient and Config 3
was the cheapest way to keep delegation.

**That was wrong.** Config 2 is stronger, because it converts the properties that direct peer calls
merely *bounded* into properties that are simply *impossible*. See the decision record above. The
measurement that corrected it — peer calls do not spawn, but the gateway does — was in this document the
whole time.

---

## Q5 — Hop limit: was unimplementable, now is

> **Superseded by the decision above.** The finding below still stands — a gateway-side hop limit
> cannot exist when agents call peers directly. The decision routes everything through the gateway, so
> the counter finally has something to count. Kept because the reasoning is what justifies the
> decision.

**Answer: the hop limit and the "agents call peers directly" design are mutually exclusive.**

Reproduced end to end:

```console
# A -> B : can dev-agent call a peer agent?
HTTP=200
# B -> A : can the peer call back?
B->A HTTP=200
```

**A → B → A works, and nothing stops it going further.** Agents are peers on one flat `internal`
network, and the network is a full mesh.

The problem is not that a hop limit is missing — it is that **no hop limit can work under this
design.** A hop limit is state: the gateway has to know the current call is already nested N deep.
But if agents call each other *directly*, the call never passes through the gateway, so the gateway
never sees the chain. It can count the calls it is asked for; it cannot see the ones its own
containers make to each other.

Three ways out, and this issue has to pick one:

| Option | What it costs |
|---|---|
| **Route all agent→agent calls through the gateway** | Defeats the "agents call peers directly" reasoning in Q6. Also puts agent traffic through the one component that must never run agent code, muddying the audit trail. |
| **Put each agent on its own network** | Genuinely enforces A↛B. Costs a network per agent and makes discovery dynamic. The only option that makes the hop question moot. |
| **Accept no hop limit; rely on the concurrency cap** | Honest, and defensible given Q3 caps the blast radius to ~3 containers. But it is not what the issue says. |

The recommendation is the second if per-agent isolation matters, and the third if it does not — with
the caveat that the third is a decision to stop defending, not a decision to be safe.

---

## Q6 — Agents and the gateway

> **Superseded by the decision above.** The answer inverts: agents *must* reach the gateway, because it
> is the only agent-to-agent route. The measurements below are what established that the current
> topology is a flat mesh — which is the thing being replaced.

**Answer: they can, and "no" is currently a policy with no mechanism behind it.**

The design says agents should not reach the gateway. Measured: everything on `internal` is mutually
reachable, so an agent reaches any peer, and would reach a gateway placed there. The peer test in Q5
is the same fact.

**This collides with the gateway's own requirement.** The gateway must hold
`/var/run/docker.sock` — it is the one host-privileged component, and that is the price of per-call
containers. A container holding the Docker socket can start sibling containers. If the gateway sits
on `internal` alongside the agents, that capability is reachable by every agent.

The gateway therefore needs its own network, and the topology becomes:

```
clients ──────────► gateway   (internal + docker socket, client-facing)
                        │  spawns
                        ▼
                     agents      (internal only)
```

The gateway is on `internal` because it *initiates* to the agents. Agents can still address it by
name — so "no" needs to be enforced, not assumed. Three mechanisms, cheapest first:

1. **Don't give the gateway a name the agents use.** Weak — DNS is not an access control.
2. **Firewall the gateway port from the agent subnet.** Real enforcement, needs per-network rules.
3. **Put the gateway on a network agents are not attached to**, and have it dual-homed via a tiny
   forwarder. Cleanest, costs a hop.

This is a genuine gap in the current design, and it is the same class as the one Phase 2 found: a
security property asserted in a document with nothing enforcing it.

---

## Q7 — Auth: the primitive already exists

**Answer: `--auth-token` on the container's `mcp` mode, verified in Phase 0.**

Not re-derived here. `docs/verification/phase-0.md` Check 1 recorded that a non-loopback `--listen`
is refused without either `--auth-token` or `--insecure-no-auth`, and the isolation tests exercise
`--insecure-no-auth` on an internal network where the network is the capability grant.

The gateway is different, because it is client-facing: it is reached from off-box, and Phase 0
established that no tested client enforces the non-loopback HTTPS rule, so `http://` plus a bearer
token is currently the realistic combination. That is a known, tracked, temporary state
([#18](https://github.com/warpcode/cloakai/issues/18)), not a new decision.

**The one thing to settle before building: per-caller identity.** With a single shared token, the
gateway cannot tell one client from another, so it cannot enforce a per-caller concurrency cap or
attribute a call in its logs. If either matters, tokens must be issued and mapped to callers, which
is a real design surface rather than a config flag.

---

## The one question the decision makes sharper

Q7 asked whether the gateway needs **per-caller identity**, and the answer is now a stronger yes. With
every call in the system crossing one chokepoint, the gateway already knows the origin of each call —
so the audit trail, the concurrency cap, and the hop counter are all waiting on the same piece of
state. Carrying call origin and depth on every request makes all three work. Dropping it makes all
three hollow, and it is the easiest thing in this design to skip.

## What should be decided before writing gateway code

Ordered by how much they change the design rather than the code.

1. **Q6 / Q5 are the same problem and must be settled together.** A network topology that makes
   "agents cannot reach the gateway" true is what makes any hop limit or per-caller policy possible.
   Nothing else in this list survives contact with a flat mesh.
2. **Q2's reaper is non-negotiable and must not live in the gateway.** A leak here is 4 GB per
   orphan, and orphaned containers are the *expected* failure of a crash, not an edge case.
3. **Q3's cap is a memory-safety number.** Derive it from available RAM; queue rather than error.
4. **Q4 is optional.** 119 ms is 4% of a model call. Build the warm pool only if fast calls are in
   scope; do not build it speculatively.
5. **Q7's per-caller identity** is the one question whose answer changes the public interface rather
   than the internals.

Nothing here blocks the *local* workflow that already works. The gateway is what makes an agent
reachable from anywhere, and everything above is about doing that without giving up the isolation
Phases 0–3 established.
