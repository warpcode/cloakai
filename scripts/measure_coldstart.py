"""Measure per-call container OVERHEAD, not the work inside it.

The first pass at this compared `docker run --rm` against `create+start+rm` while
both ran `opencode --help`. Both were dominated by the ~900ms the command takes,
so the lifecycle difference was invisible and the warm pool appeared worthless.
That is a confounded benchmark, not a finding.

This measures the thing the gateway actually pays for: the cost of going from
"no container" to "container running a given payload", with the payload held
constant and made trivial. The difference between the paths is then the
lifecycle cost, and the warm-pool proposal can be judged on its own terms.

Three paths, same trivial payload (`true`):
  bare        the shell's own baseline, for scale
  run         docker run --rm
  create      docker create, timed ALONE — this is the claim under test
  start       docker start of an already-created container
  exec        exec into a long-lived container (NOT isolated; for comparison)
"""

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

IMAGE = "cloakai/dev"
NETWORK = "cloakai-internal"
REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 15
PAYLOAD = ["shell", "-c", "true"]

FLAGS: list[str] = []
pending = None
for line in Path("agents/isolation-flags").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if line.endswith(":"):
        pending = line
        continue
    if pending:
        FLAGS += [pending, line]
        pending = None
    else:
        FLAGS += line.split()
FLAGS = [w.replace("NETWORK_PLACEHOLDER", NETWORK) for w in FLAGS]
RUN_FLAGS = [f for f in FLAGS if f != "--rm"]


def sh(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, (p.stdout + p.stderr).strip()


def timed(cmd) -> float | None:
    t0 = time.perf_counter()
    rc, out = sh(cmd)
    dt = time.perf_counter() - t0
    if rc != 0:
        print(f"    FAILED: {' '.join(cmd[:5])}… :: {out[:120]}")
        return None
    return dt


def wait_exit(cid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        rc, state = sh(["docker", "inspect", "-f", "{{.State.Running}}", cid])
        if state != "true":
            return
        time.sleep(0.01)


def report(label, samples, note=""):
    c = [s for s in samples if s is not None]
    if not c:
        print(f"  {label:24s} no samples")
        return None
    r = dict(n=len(c), min=min(c), median=statistics.median(c),
             mean=statistics.mean(c), max=max(c), stdev=statistics.stdev(c) if len(c) > 1 else 0.0)
    print(f"  {label:24s} n={r['n']:2d}  median={r['median']*1000:7.1f}ms  "
          f"min={r['min']*1000:7.1f}  max={r['max']*1000:7.1f}  ±{r['stdev']*1000:5.1f}  {note}")
    return r


def main():
    print(f"image={IMAGE}  network={NETWORK}  reps={REPS}  payload={" ".join(PAYLOAD)}\n")
    out = {}

    print("baseline (no container):")
    out["bare"] = report("bash -c true", [timed(["bash", "-c", "true"]) for _ in range(REPS)])

    print("\nper-call lifecycle (identical trivial payload):")
    out["run"] = report("docker run --rm",
                        [timed(["docker", "run", "--rm", *RUN_FLAGS, IMAGE, *PAYLOAD])
                         for _ in range(REPS)])

    creates, starts, totals = [], [], []
    for _ in range(REPS):
        rc, cid = sh(["docker", "create", *RUN_FLAGS, IMAGE, *PAYLOAD])
        if rc != 0:
            print(f"    create failed: {cid[:150]}")
            continue
        t0 = time.perf_counter()
        sh(["docker", "start", cid])
        start_dt = time.perf_counter() - t0
        wait_exit(cid)
        sh(["docker", "rm", "-f", cid])
        creates.append(start_dt)
        starts.append(start_dt)
        totals.append(time.perf_counter() - t0)

    # Time `create` on its own, which is the actual claim: "docker create is cheap,
    # since layers are already on disk".
    create_only = []
    for _ in range(REPS):
        rc, cid = sh(["docker", "create", *RUN_FLAGS, IMAGE, *PAYLOAD])
        if rc == 0:
            t0 = time.perf_counter()
            sh(["docker", "rm", "-f", cid])
            create_only.append(time.perf_counter() - t0)
    out["create_only"] = report("docker create (+rm)", create_only)
    out["start"] = report("docker start", starts)
    out["create_start"] = report("create+start+rm", totals)

    # The benchmark above creates at CALL time, which is not the warm pool. A warm
    # pool pre-creates N containers and holds them, so a call is `docker start`
    # alone. This measures that properly — it is the actual claim in the issue.
    print("\nwarm pool done PROPERLY (containers pre-created, call = start only):")
    pool = []
    warm_starts = []
    pool_build = []
    try:
        t_pool0 = time.perf_counter()
        for _ in range(REPS):
            rc, cid = sh(["docker", "create", *RUN_FLAGS, IMAGE, *PAYLOAD])
            if rc == 0:
                pool.append(cid)
        pool_build.append((time.perf_counter() - t_pool0) / max(1, len(pool)))

        # Serve each pre-created container once. This is a call.
        for cid in pool:
            t0 = time.perf_counter()
            sh(["docker", "start", cid])
            warm_starts.append(time.perf_counter() - t0)
            wait_exit(cid)
            sh(["docker", "rm", "-f", cid])
    finally:
        for cid in pool:
            sh(["docker", "rm", "-f", cid])

    out["warm_pool"] = report("start from a warm pool", warm_starts)
    out["pool_build_per"] = report("  (pool build, per ctr)", pool_build)

    print("\nnon-isolated comparison (a long-lived container, exec into it):")
    execs = []
    for _ in range(max(3, REPS // 3)):
        rc, cid = sh(["docker", "run", "-d", "--rm", "--network", NETWORK,
                      IMAGE, "shell", "-c", "sleep 60"])
        if rc != 0:
            print(f"    start failed: {cid[:150]}")
            continue
        try:
            execs.append(timed(["docker", "exec", cid, "true"]))
        finally:
            sh(["docker", "rm", "-f", cid])
    out["exec"] = report("docker exec", execs, "(NOT isolated)")

    print()
    run_m = (out.get("run") or {}).get("median")
    cs_m = (out.get("create_start") or {}).get("median")
    warm_m = (out.get("warm_pool") or {}).get("median")
    if run_m and warm_m:
        delta = (run_m - warm_m) * 1000
        pct = (1 - warm_m / run_m) * 100
        print(f"warm pool saves {delta:.1f}ms per call ({pct:.0f}% of the run path)")
        print(f"  and buys that for {(out['pool_build_per']['median'] * 1000):.0f}ms of pre-creation each,")
        print(f"  so it pays for itself after "
              f"{(out['pool_build_per']['median'] * max(1, REPS)) / (delta / 1000):.0f} calls per container"
              if delta > 0 else "  — and never pays for itself")
    if run_m and cs_m:
        print(f"  (create-at-call-time saves {(run_m - cs_m) * 1000:+.1f}ms — i.e. nothing)")

    if run_m:
        print(f"\nat the run path: {1 / run_m:.1f} calls/sec serially")

    Path("docs/gateway-coldstart.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("wrote docs/gateway-coldstart.json")


if __name__ == "__main__":
    main()
