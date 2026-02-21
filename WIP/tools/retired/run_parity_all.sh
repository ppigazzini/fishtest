#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/server/.venv/bin/python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python interpreter not executable: ${PYTHON_BIN}" >&2
  echo "Set PYTHON_BIN to a valid interpreter (e.g. server/.venv/bin/python)." >&2
  exit 2
fi

STRICT=0
for arg in "$@"; do
  case "$arg" in
    --strict)
      STRICT=1
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: WIP/tools/run_parity_all.sh [--strict]" >&2
      exit 2
      ;;
  esac
done

SCRIPTS=(
  "WIP/tools/parity_check_api_routes.py"
  "WIP/tools/parity_check_views_routes.py"
  "WIP/tools/parity_check_api_ast.py"
  "WIP/tools/parity_check_views_ast.py"
  "WIP/tools/parity_check_hotspots_similarity.py"
  "WIP/tools/parity_check_urls_dict.py"
  "WIP/tools/parity_check_views_no_renderer.py"
  "WIP/tools/compare_template_parity.py"
  "WIP/tools/compare_template_response_parity.py"
  "WIP/tools/compare_jinja_mako_parity.py"
  "WIP/tools/compare_template_parity_summary.py"
)

echo "Running parity suite with ${PYTHON_BIN}"

for script in "${SCRIPTS[@]}"; do
  echo "===== ${script} ====="
  cmd=("${PYTHON_BIN}" "${REPO_ROOT}/${script}")

  if [[ ${STRICT} -eq 1 ]] && [[ "${script}" == "WIP/tools/compare_template_response_parity.py" ]]; then
    cmd+=("--strict")
  fi
  if [[ ${STRICT} -eq 1 ]] && [[ "${script}" == "WIP/tools/compare_jinja_mako_parity.py" ]]; then
    cmd+=("--strict")
  fi

  "${cmd[@]}"
done

echo "Parity suite completed successfully."
