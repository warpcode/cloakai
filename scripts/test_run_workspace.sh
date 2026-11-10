#!/usr/bin/env bash
set -euo pipefail

# This is a regression test for the relative workspace path resolution in run.sh.
# The bug: if `run.sh --workspace .` was called from OUTSIDE the repository root,
# the path was resolved relative to the repository root instead of the caller's PWD.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

TEST_OUT_DIR="/tmp/test_workspace_out_$$"
mkdir -p "$TEST_OUT_DIR"
# Ensure cleanup on exit
trap 'rm -rf "$TEST_OUT_DIR"' EXIT

# Change to the temporary directory
cd "$TEST_OUT_DIR"

echo "Testing relative workspace path from outside repo root"
OUTPUT=$(bash "$REPO_ROOT/scripts/run.sh" --workspace . 2>&1 || true)

# Verify that the mount note shows the caller's absolute path
if ! echo "$OUTPUT" | grep -q "mounting $TEST_OUT_DIR/. at /workspace" && ! echo "$OUTPUT" | grep -q "mounting $TEST_OUT_DIR at /workspace"; then
  echo "FAIL: Expected run.sh to mount $TEST_OUT_DIR/. but got:"
  echo "$OUTPUT"
  # Instead of exit 1 which breaks sandbox, we use a command that fails gracefully
  test 1 -eq 0
fi

echo "PASS: Relative workspace path resolves correctly."
