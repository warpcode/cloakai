# Bolt's Journal

## 2026-09-29 - Batching container label inspection in reaper sweeps
**Learning:** In container reaper sweeps (`scripts/cloakai-reap.py`), inspecting $N$ containers individually using single-arg `docker inspect` spawns $N$ sub-processes, incurring significant IPC/process overhead. Batching container IDs into a single `docker inspect` call reduces sub-process spawns from $O(N)$ to $O(1)$.
**Action:** Pass candidate container IDs as variadic arguments to `docker inspect` with a line-formatted template to inspect container metadata in a single process invocation.

## 2026-09-29 - Precomputing resolved paths in loop validation
**Learning:** In compiler path boundary checks (`check_no_escape`), calling `plugin_root.resolve(strict=True)` inside validation loops causes redundant filesystem `realpath` syscalls for the same root directory on every checked file.
**Action:** Allow passing pre-resolved `resolved_root` or precomputing resolved roots when validating multiple subpaths under a common directory root.
