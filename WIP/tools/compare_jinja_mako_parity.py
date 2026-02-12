#!/usr/bin/env python3
# ruff: noqa: E402
"""Run response-level parity checks for legacy Mako vs Jinja2 templates.

Goal:
    Provide a single entry point that delegates to the response-parity tool
    and reports status/header parity plus raw/normalized HTML equality.

Usage:
    python WIP/tools/compare_jinja_mako_parity.py
    python WIP/tools/compare_jinja_mako_parity.py --jinja-dir \
        server/fishtest/templates_jinja2
    python WIP/tools/compare_jinja_mako_parity.py --templates tests_view.html.j2

Exit status:
    0 if parity looks good
    1 if mismatches are found
    2 on missing template or render error
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from WIP.tools import compare_template_response_parity as response_parity


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_context() -> Path:
    return _repo_root() / "WIP" / "tools" / "template_parity_context.json"


def _run_parity(args: argparse.Namespace) -> int:
    argv = [
        "compare_template_response_parity",
        "--left-engine",
        "mako",
        "--right-engine",
        "jinja",
        "--context",
        str(args.context),
    ]
    if args.jinja_dir is not None:
        argv.extend(["--jinja-dir", str(args.jinja_dir)])
    if args.templates:
        argv.extend(["--templates", args.templates])
    if args.json:
        argv.append("--json")
    if args.show_diff:
        argv.append("--show-diff")
    if args.strict:
        argv.append("--strict")

    original_argv = sys.argv
    try:
        sys.argv = argv
        return response_parity.main()
    finally:
        sys.argv = original_argv


def _write_line(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def main() -> int:
    """Run the response parity tool with Mako vs Jinja2 defaults."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context",
        type=Path,
        default=_default_context(),
        help="Path to template context JSON.",
    )
    parser.add_argument(
        "--templates",
        type=str,
        default="",
        help="Comma-separated list of template names.",
    )
    parser.add_argument(
        "--jinja-dir",
        type=Path,
        default=None,
        help="Path to templates_jinja2 for the jinja engine.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout.",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Show unified diffs for mismatches.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on any normalized HTML mismatch.",
    )
    args = parser.parse_args()

    banner = "=== MAKO VS JINJA2 PARITY CHECK ==="
    _write_line(banner)
    _write_line(f"Context:   {args.context}\n")

    return _run_parity(args)


if __name__ == "__main__":
    raise SystemExit(main())
