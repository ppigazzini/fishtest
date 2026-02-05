#!/usr/bin/env python3
"""Run parity checks for Jinja2 vs new Mako templates.

Goal:
    Provide a single, easy-to-spot entry point for comparing Jinja2 output
    against the new Mako templates using the shared parity tool.

Usage:
    python WIP/tools/compare_jinja_mako_new_parity.py
    python WIP/tools/compare_jinja_mako_new_parity.py --templates tests_view.mak
    python WIP/tools/compare_jinja_mako_new_parity.py --json --show-diff

Exit status:
    0 if all templates match (normalized)
    1 if any template differs (normalized)
    2 on missing template or render error
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from WIP.tools import compare_template_response_parity as response_parity


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_context() -> Path:
    return _repo_root() / "WIP" / "tools" / "template_parity_context.json"


def _run_parity(args: argparse.Namespace) -> int:
    argv = [
        "compare_template_response_parity",
        "--left-engine",
        "jinja",
        "--right-engine",
        "mako_new",
        "--context",
        str(args.context),
    ]
    if args.templates:
        argv.extend(["--templates", args.templates])
    if args.json:
        argv.append("--json")
    if args.show_diff:
        argv.append("--show-diff")

    original_argv = sys.argv
    try:
        sys.argv = argv
        return response_parity.main()
    finally:
        sys.argv = original_argv


def main() -> int:
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
        "--json",
        action="store_true",
        help="Emit JSON to stdout.",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Show unified diffs for mismatches.",
    )
    args = parser.parse_args()

    banner = "=== JINJA2 VS MAKO_NEW PARITY CHECK ==="
    print(banner)
    print(f"Context:   {args.context}\n")

    return _run_parity(args)


if __name__ == "__main__":
    raise SystemExit(main())
