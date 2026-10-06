## 2026-10-15 - Unintentional Host Environment Variable Leakage via `run_agents`
**Vulnerability:** `reg.run_agents(...)` defaulted to passing every host environment variable (`os.environ`) into the Docker container via `--env VAR` when the caller passed `env=None`.
**Learning:** Defaulting an optional `env: dict | None` parameter to fall back to `os.environ` when `None` exposes all secrets, tokens, and API keys on the host machine to untrusted containers.
**Prevention:** Default optional environment dictionaries to `{}` (`env or {}`) instead of `os.environ`, ensuring only explicitly requested environment variables are forwarded to containerized agents.

## 2026-09-29 - Path Traversal Bypass via Dangling Symlinks with `pathlib.Path.resolve(strict=True)`
**Vulnerability:** Calling `pathlib.Path.resolve(strict=True)` on a dangling symlink (pointing to a non-existent path outside the root) raises `FileNotFoundError`. Catching and swallowing `FileNotFoundError` during validation allows path traversal via symlinks pointing outside `plugin_root`.
**Learning:** `strict=True` requires the symlink target to exist on disk. When validating untrusted input for directory containment, `resolve(strict=True)` fails silently if the target is missing, creating a bypass vector.
**Prevention:** Use `pathlib.Path.resolve(strict=False)` for path containment checks (`is_relative_to(root)`). It resolves symlink paths without requiring existence, ensuring all symlink targets are properly checked against `plugin_root`.
