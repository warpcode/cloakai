## 2026-09-29 - Path Traversal Bypass via Dangling Symlinks with `pathlib.Path.resolve(strict=True)`
**Vulnerability:** Calling `pathlib.Path.resolve(strict=True)` on a dangling symlink (pointing to a non-existent path outside the root) raises `FileNotFoundError`. Catching and swallowing `FileNotFoundError` during validation allows path traversal via symlinks pointing outside `plugin_root`.
**Learning:** `strict=True` requires the symlink target to exist on disk. When validating untrusted input for directory containment, `resolve(strict=True)` fails silently if the target is missing, creating a bypass vector.
**Prevention:** Use `pathlib.Path.resolve(strict=False)` for path containment checks (`is_relative_to(root)`). It resolves symlink paths without requiring existence, ensuring all symlink targets are properly checked against `plugin_root`.

## 2026-09-30 - Path Traversal and Parameter Injection in API Client via Unsanitized Session IDs
**Vulnerability:** Untrusted `session_id` parameters in `agents/jules-mcp/jules_client.py` were formatted directly into API endpoints without URL encoding, allowing path traversal (e.g. `../../sources`) and query string parameter injection against the upstream REST API.
**Learning:** Removing expected prefixes like `sessions/` via `removeprefix()` is insufficient when handling arbitrary user strings in URL path generation. Unencoded slashes, dots, and question marks allow callers to break out of the resource path hierarchy.
**Prevention:** Always sanitize and URL-encode identifier parameters (`urllib.parse.quote(sid, safe="")`) before formatting them into REST API endpoint URLs.
