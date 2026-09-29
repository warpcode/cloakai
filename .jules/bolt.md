# Bolt's Journal

## 2026-03-31 - Precomputing resolved paths in loop validation
**Learning:** In compiler path boundary checks (`check_no_escape`), calling `plugin_root.resolve(strict=True)` inside validation loops causes redundant filesystem `realpath` syscalls for the same root directory on every checked file.
**Action:** Allow passing pre-resolved `resolved_root` or precomputing resolved roots when validating multiple subpaths under a common directory root.
