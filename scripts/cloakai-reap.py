#!/usr/bin/env python3
"""Reap per-call containers that outlived the call — or the gateway that made them.

The problem this solves, from docs/gateway-verification.md Q2:

    $ docker create ... --label cloakai.test=orphan cloakai/dev shell -c "sleep 300"
    $ docker start <cid>
    # ...the gateway dies...
    RESULT: container SURVIVED the gateway dying  -> LEAK

Docker has no ownership concept. A container's lifetime is not tied to the process
that created it, and `--rm` only cleans up on *exit* — so a hung agent leaks its
full memory limit until it finishes on its own, which it may never do.

The rule, and why it is this rule:

    Kill every container carrying our label whose AGE exceeds the maximum call
    lifetime. Nothing else.

Not "kill everything on startup" — during a rolling restart, a new instance
sweeping unconditionally would kill a live peer's in-flight calls. The age check
is what makes a cross-instance sweep safe, and it is the only thing that does:
a young container is by definition a call still inside its budget, whoever owns
it. Measured both ways; see docs/gateway-verification.md.

An instance id IS recorded, but only so a log line can say whose call leaked.
Safety does not depend on it.

Two modes, and the difference matters:

  --once          at startup. Cheap: a gateway that just started cannot have
                  many orphans, and it catches the overwhelmingly common case of
                  a supervised restart.

  --interval N    while running. This is what actually bounds the leak, because
                  startup cleanup does nothing while the gateway is DOWN, and
                  nothing at all if it is killed and never restarted.

Run both. The startup sweep is three lines and the periodic one is the safety
net; together they are what makes a crash boring instead of a slow disk leak.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass

# One label namespace, so a sweep can never touch a container it did not make.
LABEL = "cloakai.call"
STARTED = "cloakai.started"
INSTANCE = "cloakai.instance"

# Generous by design. A legitimate long-running agent call must not be reaped out
# from under the model; this is a leak bound, not a latency target.
DEFAULT_MAX_LIFETIME = 900.0


@dataclass
class Reaped:
    cid: str
    instance: str
    age: float


def docker(*args: str, check: bool = True) -> tuple[int, str]:
    p = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {p.stderr.strip()}")
    return p.returncode, (p.stdout + p.stderr).strip()


def call_labels(cid: str) -> dict[str, str]:
    _, out = docker("inspect", "-f", "{{json .Config.Labels}}", cid, check=False)
    try:
        labels = json.loads(out or "{}")
    except json.JSONDecodeError:
        return {}
    return labels if isinstance(labels, dict) else {}


def label_for_call(instance: str) -> list[str]:
    """The label set every per-call container must carry."""
    return [
        f"--label", f"{LABEL}=1",
        f"--label", f"{STARTED}={int(time.time())}",
        f"--label", f"{INSTANCE}={instance}",
    ]


def sweep(max_lifetime: float, now: float | None = None, dry_run: bool = False) -> list[Reaped]:
    """Kill labelled containers older than the maximum call lifetime.

    Returns what it reaped. An empty list is the healthy case, so callers must
    not treat "nothing reaped" as "nothing found" without checking the count.
    """
    now = now if now is not None else time.time()
    rc, out = docker("ps", "-q", "--filter", f"label={LABEL}=1", check=False)
    if rc != 0:
        raise RuntimeError(f"docker ps: {out}")

    reaped: list[Reaped] = []
    for cid in out.split():
        labels = call_labels(cid)
        raw = labels.get(STARTED)
        try:
            started = float(raw)
        except (TypeError, ValueError):
            # No parseable timestamp: we cannot reason about its age, so we
            # cannot claim it is young. Skip rather than kill something live.
            continue
        age = now - started
        if age < max_lifetime:
            continue
        reaped.append(Reaped(cid[:12], labels.get(INSTANCE, "?"), age))
        if not dry_run:
            docker("rm", "-f", cid, check=False)

    return reaped


def report(reaped: list[Reaped], dry_run: bool) -> None:
    verb = "would reap" if dry_run else "reaped"
    for r in reaped:
        print(f"  {verb} {r.cid} (instance {r.instance}, age {r.age:.0f}s)", file=sys.stderr)


def loop(max_lifetime: float, interval: float, dry_run: bool) -> None:
    while True:
        try:
            reaped = sweep(max_lifetime, dry_run=dry_run)
            if reaped:
                report(reaped, dry_run)
        except Exception as exc:  # noqa: BLE001 — a reaper must not die
            print(f"  sweep failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="sweep once and exit (startup cleanup)")
    mode.add_argument("--interval", type=float, help="sweep every N seconds instead")
    ap.add_argument("--max-lifetime", type=float, default=DEFAULT_MAX_LIFETIME,
                    help=f"max call lifetime in seconds (default {DEFAULT_MAX_LIFETIME:.0f})")
    ap.add_argument("--instance", default=None, help="instance id recorded on reaped containers")
    ap.add_argument("--print-instance-id", action="store_true",
                    help="print a fresh instance id and exit (for labelling at startup)")
    ap.add_argument("--dry-run", action="store_true", help="report without killing")
    args = ap.parse_args()

    if args.print_instance_id:
        print(uuid.uuid4().hex[:12])
        return 0

    if args.interval:
        loop(args.max_lifetime, args.interval, args.dry_run)
        return 0

    reaped = sweep(args.max_lifetime, dry_run=args.dry_run)
    if not args.dry_run:
        report(reaped, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
