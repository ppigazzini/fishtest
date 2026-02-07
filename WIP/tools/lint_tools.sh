#!/usr/bin/env bash
# Lint WIP/tools scripts with ruff and ty.
# All fixes must follow best modern practices for idiomatic Python 3.14 in the Starlette/FastAPI stack.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/server"

if command -v uv >/dev/null 2>&1; then
  export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/server"
  uv run ruff check --select ALL ../WIP/tools/*.py --fix
  uv run ty check ../WIP/tools/*.py
else
  echo "uv not found; run ruff and ty manually." >&2
  exit 1
fi
