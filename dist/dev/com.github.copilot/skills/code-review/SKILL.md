---
name: code-review
description: Review a change for correctness, security, and maintainability, then report findings ranked by severity with file:line references.
---

# Code review

Review the change under review. Rank findings by severity and cite `file:line` for every claim.

## What to look for, in order

1. **Correctness.** Does the change do what it claims? Trace the main path for a wrong branch, an
   off-by-one, a missing `await`, an unhandled error. Read the tests: do they actually cover the new
   behaviour, or do they only assert the happy path?
2. **Security.** Untrusted input reaching a sink. Injection, path traversal, deserialisation, SSRF,
   missing authz on a new route, secrets in source or in test fixtures. A new dependency is a finding
   unless its licence and provenance are obvious.
3. **Interface changes.** A renamed or removed export, a widened permission, a changed default, a
   widened scope. These are breaking even when the diff looks local.
4. **Maintainability.** Duplicated logic that should be shared, a comment explaining *what* rather
   than *why*, a name that lies about what it does.
5. **Tests.** Missing coverage on the new branch. A test asserting a mock was called is not coverage
   of behaviour.

## Output format

```
## Blocking
- `path/to/file.py:42` — one sentence on the defect, one on the fix.

## Worth noting
- `path/to/file.py:88` — non-blocking observation.

## Verified
- `path/to/file.py:120` — looked wrong, is actually correct because …
```

Omit a section if it is empty. Do not invent findings to fill a section — "no blocking issues" is a
valid and useful result.

## Rules

- Every finding needs a `file:line`. A finding without one is a guess; drop it.
- Do not restate what the code does as if it were a problem. Only report defects.
- If you cannot tell whether something is a defect, say so and say what would settle it.
