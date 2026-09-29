#!/usr/bin/env bash
# Verify the compose service and agents/isolation-flags describe the same isolation.
#
# Compose cannot read a file, so the flag set exists twice: once as a word list for
# `docker run` and once as YAML for `docker compose up`. Nothing makes them agree,
# so a change to one can silently leave the other weaker — and the compose path is
# the one a supervisor will use.
#
# This is the guard for that. It fails rather than warning, because the failure it
# prevents is an isolated-in-testing, unisolated-in-production agent.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE_FILE:-infra/compose.yml}"
SERVICE="${AGENT_SERVICE:-dev-agent}"
FLAGS_FILE="agents/isolation-flags"

fail=0
note() { printf '  %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=1; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }

svc=$(docker compose -f "$COMPOSE" config --format json 2>/dev/null \
      | python3 -c "
import json,sys
svc = json.load(sys.stdin)['services']['$SERVICE']
print(json.dumps({
    'read_only': svc.get('read_only', False),
    'cap_drop': sorted(svc.get('cap_drop') or []),
    'security_opt': sorted(svc.get('security_opt') or []),
    'pids_limit': svc.get('pids_limit'),
    'mem_limit': svc.get('mem_limit'),
    'cpus': svc.get('cpus'),
    'tmpfs': sorted(svc.get('tmpfs') or []),
    'networks': sorted(svc.get('networks', {}).keys()),
    'ports': svc.get('ports', []),
    'privileged': svc.get('privileged', False),
}))")

# Written to a file rather than piped: the heredoc below needs stdin, and a pipe
# into the same interpreter would be clobbered by it.
SVC_FILE=$(mktemp)
trap 'rm -f "$SVC_FILE"' EXIT
printf '%s' "$svc" > "$SVC_FILE"

python3 - "$SVC_FILE" "$FLAGS_FILE" <<'PY'
import json, sys

svc = json.load(open(sys.argv[1]))
flags_path = sys.argv[2]

# The word list agents/isolation-flags, parsed the same way run.sh does, and
# normalised so that --flag=value and --flag value are indistinguishable.
words, pending = [], None
for line in open(flags_path, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if line.endswith(":"):          # --security-opt no-new-privileges:true
        pending = line
        continue
    if pending:
        words.append(pending); words.append(line)
        pending = None
    else:
        words.extend(line.split())

fails = []

# Normalise to name -> value, covering all three spellings the file uses:
#   --flag=value     (--memory=4g)
#   --flag value     (--tmpfs /tmp)
#   --flag           (--read-only, a bare boolean)
# A bare flag maps to the empty string: it is present, it just takes no value.
pairs, i = {}, 0
while i < len(words):
    w = words[i]
    if not w.startswith("--"):
        i += 1
        continue
    if "=" in w:
        k, v = w.split("=", 1)
        pairs[k] = v
    elif i + 1 < len(words) and not words[i + 1].startswith("--"):
        pairs[w] = words[i + 1]
        i += 1
    else:
        pairs[w] = ""
    i += 1

def opt(name):
    return pairs.get(name)

def has(name):
    return name in pairs

# A check that cannot run is not a check. Every one of these is load-bearing, so
# a missing flag is reported rather than skipped — an earlier version of this
# script silently skipped the pids, memory and cpus checks because it only
# understood `--flag value` and the file uses `--flag=value`.
REQUIRED = ["--read-only", "--cap-drop", "--security-opt", "--tmpfs",
            "--pids-limit", "--memory", "--cpus", "--network"]
for name in REQUIRED:
    if not has(name):
        print(f"  \033[31m✗\033[0m {name} is absent from {flags_path} — cannot verify parity")
        fails.append(name)
if fails:
    sys.exit(1)

def check(ok_, msg):
    print(("  \033[32m✓\033[0m " if ok_ else "  \033[31m✗\033[0m ") + msg)
    if not ok_:
        fails.append(msg)

check(svc["read_only"] is True and has("--read-only"), "read_only matches --read-only")
# Compared against the PARSED flag, not a literal. Two of these were hardcoded,
# which meant that changing the flag file and the compose file together would
# make this compare compose against a stale literal and fail for the wrong
# reason — or, worse, agree for the wrong reason.
check(svc["cap_drop"] == [opt("--cap-drop")],
      f"cap_drop matches --cap-drop={opt('--cap-drop')} (compose: {svc['cap_drop']})")
check(any(opt("--security-opt") in s for s in svc["security_opt"]),
      f"security_opt matches --security-opt {opt('--security-opt')} (compose: {svc['security_opt']})")

check(str(svc["pids_limit"]) == opt("--pids-limit"),
      f"pids_limit matches --pids-limit={opt('--pids-limit')} (compose: {svc['pids_limit']})")
# `docker compose config --format json` reports mem_limit in BYTES regardless of
# how it was written in the YAML, so compare the value, not the spelling.
def to_bytes(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        pass
    s = str(v).strip().upper()
    for suffix, mult in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10), ("B", 1)):
        if s.endswith(suffix):
            return int(float(s[:-len(suffix)]) * mult)
    return None

want, got = to_bytes(opt("--memory")), to_bytes(svc["mem_limit"])
check(want is not None and got is not None and want == got,
      f"mem_limit matches --memory={opt('--memory')} ({want} bytes; compose: {got} bytes)")
check(abs(float(svc["cpus"]) - float(opt("--cpus"))) < 0.01,
      f"cpus matches --cpus={opt('--cpus')} (compose: {svc['cpus']})")

flag_tmpfs = [words[i + 1] for i, w in enumerate(words) if w == "--tmpfs"]
check(sorted(svc["tmpfs"]) == sorted(flag_tmpfs),
      f"tmpfs mounts match ({len(flag_tmpfs)} of them)")

# The network is the capability grant, so being on `internal` is not optional.
check(svc["networks"] == ["internal"],
      f"the agent is on internal ONLY (compose: {svc['networks']})")
check(not svc["ports"], f"no ports are published (compose: {svc['ports']})")
check(not svc["privileged"], "not privileged")

# The two things that must never appear anywhere.
check(not has("--privileged"), "the word list contains no --privileged")
check("/var/run/docker.sock" not in " ".join(words),
      "the word list mounts no docker socket")

sys.exit(1 if fails else 0)
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  ok "compose and agents/isolation-flags agree"
else
  bad "compose and agents/isolation-flags DISAGREE — the compose path is less isolated than the tested one"
fi

exit "$fail"
