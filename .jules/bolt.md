# Bolt's Journal

## 2026-09-29 - Precomputing resolved paths in loop validation
**Learning:** In compiler path boundary checks (`check_no_escape`), calling `plugin_root.resolve(strict=True)` inside validation loops causes redundant filesystem `realpath` syscalls for the same root directory on every checked file.
**Action:** Allow passing pre-resolved `resolved_root` or precomputing resolved roots when validating multiple subpaths under a common directory root.

## 2026-09-30 - Batching CLI subprocess inspections in container reaper
**Learning:** Sequential `docker inspect` process invocations inside periodic container sweeps (`cloakai-reap.py`) incur severe process execution and IPC overhead when inspecting multiple container labels ($O(N)$ process spawns).
**Action:** Always batch container IDs into a single `docker inspect` CLI call (`call_labels_batch`) to retrieve metadata in one subprocess execution ($O(1)$ process spawns).
