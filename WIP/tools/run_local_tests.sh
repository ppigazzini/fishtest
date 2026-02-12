#!/usr/bin/env bash
# Run all local unit tests (including Mongo-backed suites) with a safe timeout.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/server"

TEST_TIMEOUT_SECONDS="${TEST_TIMEOUT_SECONDS:-900}"

if command -v timeout >/dev/null 2>&1; then
  timeout "${TEST_TIMEOUT_SECONDS}" \
    uv run -m unittest discover -vb -s tests
else
  uv run -m unittest discover -vb -s tests
fi
