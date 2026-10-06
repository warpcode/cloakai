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


def test_call_labels_batch_unit() -> None:
    from unittest import mock

    section("batch label fetching unit tests")

    # 1. Empty cids
    res = reaper.call_labels_batch([])
    if res == {}:
        ok("call_labels_batch([]) returns empty dict")
    else:
        bad(f"call_labels_batch([]) returned {res}")

    # 2. Mocked docker inspect output with multiple containers
    fake_cid1 = "123456789abc0000000000000000000000000000000000000000000000000000"
    fake_cid2 = "abcdef1234560000000000000000000000000000000000000000000000000000"
    mock_output = (
        f"{fake_cid1}\t{{\"cloakai.call\":\"1\",\"cloakai.started\":\"1000\"}}\n"
        f"{fake_cid2}\t{{\"cloakai.call\":\"1\",\"cloakai.started\":\"2000\"}}\n"
    )

    with mock.patch.object(reaper, "docker", return_value=(0, mock_output)):
        batch_res = reaper.call_labels_batch(["123456789abc", "abcdef123456"])
        if fake_cid1 in batch_res and "123456789abc" in batch_res:
            if batch_res["123456789abc"].get("cloakai.started") == "1000":
                ok("call_labels_batch parses multi-container output correctly by full and short ID")
            else:
                bad(f"call_labels_batch wrong content: {batch_res}")
        else:
            bad(f"call_labels_batch missing keys: {batch_res}")

    # 3. Handle docker inspect error
    with mock.patch.object(reaper, "docker", return_value=(1, "error")):
        err_res = reaper.call_labels_batch(["123456789abc"])
        if err_res == {}:
            ok("call_labels_batch handles docker inspect error gracefully")
        else:
            bad(f"call_labels_batch error response not empty: {err_res}")


def main() -> int:
    print(f"image={IMAGE} network={NETWORK} max_lifetime={MAX_LIFETIME}s\n")

    test_call_labels_batch_unit()

    # The remaining tests interact with a live Docker daemon (image: cloakai/dev).
    # If Docker or the image is unavailable, run unit tests and report.
    if not reaper.docker("image", "inspect", IMAGE, check=False)[0] == 0:
        print(f"\nNote: Image {IMAGE} not present locally; skipping live docker integration tests.")
        if failures:
            print(f"\033[31m{len(failures)} reaper check(s) failed.\033[0m")
            return 1
        print("\033[32mReaper unit tests passed.\033[0m")
        return 0

    section("a sweep must spare a LIVE call, even another instance's")
    young_other = make(age=0.0, instance="peer-A")
    reaped = reaper.sweep(MAX_LIFETIME)
    if alive(young_other):
        ok("a fresh call from another instance was NOT reaped")
    else:
        bad("THE REAPER KILLED A LIVE CALL — it is too eager")
    cleanup()

    section("a sweep must reap a HUNG call")
    hung = make(age=MAX_LIFETIME + 3, instance="self")
    reaped = reaper.sweep(MAX_LIFETIME)
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
    reaper.sweep(MAX_LIFETIME)
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
    reaper.sweep(MAX_LIFETIME)
    if alive(weird):
        ok("a container whose age cannot be determined was spared, not killed on a guess")
    else:
        bad("a container of UNKNOWN age was killed — we cannot claim it is hung")
    cleanup()

    section("a dry run reports without killing")
    probe = make(age=MAX_LIFETIME + 3, instance="self")
    reaper.sweep(MAX_LIFETIME, dry_run=True)
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
    reaped = reaper.sweep(MAX_LIFETIME)
    if reaped == []:
        ok("no containers to reap is reported as an empty list")
    else:
        bad(f"expected nothing to reap, got {reaped}")

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
