# Bolt's Journal

## 2026-09-29 - Precomputing resolved paths in loop validation
**Learning:** In compiler path boundary checks (`check_no_escape`), calling `plugin_root.resolve(strict=True)` inside validation loops causes redundant filesystem `realpath` syscalls for the same root directory on every checked file.
**Action:** Allow passing pre-resolved `resolved_root` or precomputing resolved roots when validating multiple subpaths under a common directory root.

## 2026-09-30 - Standard shutil.copytree copy2 fast paths and path traversal safety
**Learning:** Passing `copy_function=shutil.copyfile` to `shutil.copytree` to avoid `copystat` overhead actually decreases performance because `copy2` uses OS-level fast-copy syscalls (`sendfile`/`copy_file_range`). Furthermore, attempting to shortcut `check_no_escape` using string/relative prefix checks bypasses canonicalization and exposes `..` path traversal security risks.
**Action:** Preserve `shutil.copytree` defaults and avoid bypassing `Path.resolve()` canonicalization in path escape validation routines.
