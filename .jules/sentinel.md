## 2026-09-29 - Path Traversal Bypass via Dangling Symlinks with `pathlib.Path.resolve(strict=True)`
**Vulnerability:** Calling `pathlib.Path.resolve(strict=True)` on a dangling symlink (pointing to a non-existent path outside the root) raises `FileNotFoundError`. Catching and swallowing `FileNotFoundError` during validation allows path traversal via symlinks pointing outside `plugin_root`.
**Learning:** `strict=True` requires the symlink target to exist on disk. When validating untrusted input for directory containment, `resolve(strict=True)` fails silently if the target is missing, creating a bypass vector.
**Prevention:** Use `pathlib.Path.resolve(strict=False)` for path containment checks (`is_relative_to(root)`). It resolves symlink paths without requiring existence, ensuring all symlink targets are properly checked against `plugin_root`.

## 2026-09-29 - Docker Container Flag Misordering Leaking Secrets into Process Arguments
**Vulnerability:** Appending `--env` flags to the command line array returned by `build_argv` placed them after the Docker image argument. Docker syntax (`docker run [OPTIONS] IMAGE [COMMAND]`) causes Docker to treat arguments following `IMAGE` as positional parameters to the container entrypoint process rather than container environment options.
**Learning:** Flags appended after `IMAGE` in `docker run` are not processed as Docker daemon flags. Environment variables failed to set in the container and secret keys/values were passed as positional parameters to container processes, exposing secrets in process listings (`ps`).
**Prevention:** Pass caller environment variables into `build_argv` so that all `--env` options are placed before `IMAGE` in the `docker run` argument list.
