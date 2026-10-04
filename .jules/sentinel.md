## 2026-09-30 - Environment Variable Misplacement in `docker run` Command Arguments
**Vulnerability:** Appending `--env` flags to the `docker run` command line after `build_argv()` places them after the container image argument (`docker run [OPTIONS] IMAGE [COMMAND] [ARG...]`).
**Learning:** Any flags placed after `IMAGE` in a `docker run` command line are passed to the container entrypoint as positional command arguments instead of being processed by Docker as runtime container options. This prevents environment variables from being set inside the container and exposes secret keys/values as command line arguments in process lists.
**Prevention:** Construct all container options (including `--env` / `--network` / isolation flags) inside `build_argv` before appending the image name and entrypoint command arguments.

## 2026-09-29 - Path Traversal Bypass via Dangling Symlinks with `pathlib.Path.resolve(strict=True)`
**Vulnerability:** Calling `pathlib.Path.resolve(strict=True)` on a dangling symlink (pointing to a non-existent path outside the root) raises `FileNotFoundError`. Catching and swallowing `FileNotFoundError` during validation allows path traversal via symlinks pointing outside `plugin_root`.
**Learning:** `strict=True` requires the symlink target to exist on disk. When validating untrusted input for directory containment, `resolve(strict=True)` fails silently if the target is missing, creating a bypass vector.
**Prevention:** Use `pathlib.Path.resolve(strict=False)` for path containment checks (`is_relative_to(root)`). It resolves symlink paths without requiring existence, ensuring all symlink targets are properly checked against `plugin_root`.
