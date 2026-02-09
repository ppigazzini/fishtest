#!/usr/bin/env bash
# Lint server/fishtest/http (excluding api.py and views.py) with ruff and ty.
# All fixes must follow best modern practices for idiomatic Python 3.14 in the Starlette/FastAPI stack.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/server"

if command -v uv >/dev/null 2>&1; then
  mapfile -t HTTP_FILES < <(
    find fishtest/http -maxdepth 1 -name "*.py" \
      ! -name "api.py" \
      ! -name "views.py"
  )

  if [[ ${#HTTP_FILES[@]} -eq 0 ]]; then
    echo "No http files found to lint." >&2
    exit 1
  fi

  uv run ruff check --select ALL "${HTTP_FILES[@]}" --fix
  uv run ty check "${HTTP_FILES[@]}"
else
  echo "uv not found; run ruff and ty manually." >&2
  exit 1
fi
