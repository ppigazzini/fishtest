#!/usr/bin/env bash
# Run the unit tests that do not require MongoDB.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/server"

uv run -m unittest \
  tests.test_http_app \
  tests.test_http_actions_view \
  tests.test_http_helpers \
  tests.test_lru_cache \
  -v
