# Bolt's Journal

## 2026-09-29 - Precomputing resolved paths in loop validation
**Learning:** In compiler path boundary checks (`check_no_escape`), calling `plugin_root.resolve(strict=True)` inside validation loops causes redundant filesystem `realpath` syscalls for the same root directory on every checked file.
**Action:** Allow passing pre-resolved `resolved_root` or precomputing resolved roots when validating multiple subpaths under a common directory root.

## 2026-09-30 - Batching docker inspect calls in periodic container reaper sweeps
**Learning:** In periodic container cleanup sweeps (`cloakai-reap.py`), calling `docker inspect` inside a loop for each container ID spawns N subprocesses, causing high process spawning overhead during periodic background sweeps.
**Action:** Pass all container IDs to a single `docker inspect` invocation to fetch container labels in batch (reducing process spawns from O(N) to O(1)).
