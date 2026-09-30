## 2025-03-09 - Pre-resolving root paths during recursive validation
**Learning:** Calling `Path.resolve(strict=False)` repeatedly in validation helper loops causes redundant disk syscalls for the base directory.
**Action:** Pass pre-resolved root paths into validation helper functions when validating file trees.
