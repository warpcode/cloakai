"""Does the reaper reap what it must, and spare what it must?

The safety property is subtle enough to be worth asserting directly: a sweep must
kill a HUNG call and must not kill a LIVE one, including a live one belonging to
a different gateway instance. A reaper that is too eager is worse than none,
because it kills work that was about to succeed.

Run: python3 scripts/test_reaper.py
Exits non-zero on any failure.
"""

import importlib.util
import sys
import io
import contextlib
from unittest import mock
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("reaper", _HERE / "cloakai-reap.py")
reaper = importlib.util.module_from_spec(spec)
# Register before exec: @dataclass resolves its module through sys.modules, and
# fails with a confusing error if it is not there yet.
sys.modules["reaper"] = reaper
spec.loader.exec_module(reaper)

IMAGE = "cloakai/dev"
NETWORK = "cloakai-internal"
MAX_LIFETIME = 3.0

failures: list[str] = []
created: list[str] = []


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def bad(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")
    failures.append(msg)


def make(age: float, instance: str, seconds: int = 60) -> str:
    """A running container, back-dated by `age` seconds so the sweep can judge it."""
    started = int(time.time() - age)
    labels = [
        "--label", f"{reaper.LABEL}=1",
        "--label", f"{reaper.STARTED}={started}",
        "--label", f"{reaper.INSTANCE}={instance}",
    ]
    rc, cid = reaper.docker("create", "--network", NETWORK, *labels,
                            IMAGE, "shell", "-c", f"sleep {seconds}", check=False)
    if rc != 0:
        raise RuntimeError(f"create failed: {cid}")
    reaper.docker("start", cid, check=False)
    created.append(cid)
    return cid


def alive(cid: str) -> bool:
    rc, out = reaper.docker("inspect", "-f", "{{.State.Running}}", cid, check=False)
    return rc == 0 and out == "true"


def section(t: str) -> None:
    print(f"\n\033[1m{t}\033[0m")


def cleanup() -> None:
    for cid in created:
        reaper.docker("rm", "-f", cid, check=False)
    created.clear()


def main() -> int:
    print(f"image={IMAGE} network={NETWORK} max_lifetime={MAX_LIFETIME}s\n")

    section("a sweep must spare a LIVE call, even another instance's")
    young_other = make(age=0.0, instance="peer-A")
    scanned, reaped = reaper.sweep(MAX_LIFETIME)
    if alive(young_other):
        ok("a fresh call from another instance was NOT reaped")
    else:
        bad("THE REAPER KILLED A LIVE CALL — it is too eager")
    cleanup()

    section("a sweep must reap a HUNG call")
    hung = make(age=MAX_LIFETIME + 3, instance="self")
    scanned, reaped = reaper.sweep(MAX_LIFETIME)
    if not alive(hung):
        ok("a call past its lifetime was reaped")
    else:
        bad("A HUNG CALL SURVIVED — this is the 4GB leak")
    if any(r.cid.startswith(hung[:12]) for r in reaped):
        ok("the reaped container was reported, with its instance id")
    else:
        bad("a container was reaped but not reported")
    cleanup()

    section("a sweep must not touch containers that are not ours")
    rc, out = reaper.docker("create", "--network", NETWORK, IMAGE,
                            "shell", "-c", "sleep 30", check=False)
    foreign = out.strip()
    reaper.docker("start", foreign, check=False)
    created.append(foreign)
    scanned, reaped = reaper.sweep(MAX_LIFETIME)
    if alive(foreign):
        ok("an UNLABELLED container was left alone (no label, no kill)")
    else:
        bad("AN UNLABELLED CONTAINER WAS KILLED — the label filter is not being honoured")
    cleanup()

    section("a container with an unparseable timestamp is spared, not killed")
    rc, out = reaper.docker("create", "--network", NETWORK,
                            "--label", f"{reaper.LABEL}=1",
                            "--label", f"{reaper.STARTED}=not-a-number",
                            IMAGE, "shell", "-c", "sleep 30", check=False)
    weird = out.strip()
    reaper.docker("start", weird, check=False)
    created.append(weird)
    scanned, reaped = reaper.sweep(MAX_LIFETIME)
    if alive(weird):
        ok("a container whose age cannot be determined was spared, not killed on a guess")
    else:
        bad("a container of UNKNOWN age was killed — we cannot claim it is hung")
    cleanup()

    section("a dry run reports without killing")
    probe = make(age=MAX_LIFETIME + 3, instance="self")
    scanned, reaped = reaper.sweep(MAX_LIFETIME, dry_run=True)
    if alive(probe):
        ok("--dry-run reaped nothing")
    else:
        bad("--dry-run KILLED something")
    cleanup()

    section("a dry run must SAY something when there is something to reap")
    # The --dry-run path reported nothing at all, which defeats its only purpose:
    # telling an operator what would happen. Asserted through the real entry point,
    # not sweep(), because the gate that dropped the output was in main().
    import io
    import contextlib
    from unittest import mock

    planted = make(age=MAX_LIFETIME + 3, instance="dryrun-probe")
    argv = ["cloakai-reap.py", "--once", "--dry-run",
            "--max-lifetime", str(MAX_LIFETIME)]
    err = io.StringIO()
    try:
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(err):
            reaper.main()
    finally:
        cleanup()
    out = err.getvalue()
    if "would reap" in out and planted[:12] in out:
        ok("--dry-run names the container it would reap")
    else:
        bad(f"--dry-run said nothing useful; got: {out.strip()[:120]!r}")

    # And with nothing to reap it must stay quiet rather than printing noise.
    err2 = io.StringIO()
    try:
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(err2):
            reaper.main()
    finally:
        cleanup()
    if err2.getvalue().strip() == "":
        ok("--dry-run stays quiet when there is nothing to reap")
    else:
        bad(f"--dry-run was noisy with nothing to reap: {err2.getvalue().strip()[:80]!r}")

    section("sweeping an empty system is a no-op, not an error")
    scanned, reaped = reaper.sweep(MAX_LIFETIME)
    if reaped == []:
        ok("no containers to reap is reported as an empty list")
    else:
        bad(f"expected nothing to reap, got {reaped}")

    section("JSON output")
    import json
    argv = ["cloakai-reap.py", "--once", "--json", "--max-lifetime", str(MAX_LIFETIME)]
    out_buf = io.StringIO()
    try:
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out_buf):
            reaper.main()
        res = json.loads(out_buf.getvalue())
        if "scanned" in res and "reaped" in res and isinstance(res["scanned"], int) and isinstance(res["reaped"], list):
            ok("--json on empty outputs correct JSON structure")
        else:
            bad(f"--json on empty output invalid structure: {res}")
    except BaseException as e:
        bad(f"--json output failed on empty: {e}")
    finally:
        cleanup()

    planted = make(age=MAX_LIFETIME + 3, instance="json-probe")
    argv = ["cloakai-reap.py", "--once", "--json", "--max-lifetime", str(MAX_LIFETIME)]
    out_buf = io.StringIO()
    try:
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out_buf):
            reaper.main()
        res = json.loads(out_buf.getvalue())
        if res["scanned"] > 0 and len(res["reaped"]) > 0 and res["reaped"][0]["instance"] == "json-probe":
            ok("--json on populated system outputs correct JSON structure with reaped data")
        else:
            bad(f"--json on populated output invalid structure/data: {res}")
    except BaseException as e:
        bad(f"--json output failed on populated: {e}")
    finally:
        cleanup()

    print()
    if failures:
        print(f"\033[31m{len(failures)} reaper check(s) failed.\033[0m")
        return 1
    print("\033[32mThe reaper reaps hung calls and spares live ones.\033[0m")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        cleanup()
