#!/usr/bin/env python3
# ruff: noqa: T201
"""Inventory: UI routes with @view_config but no renderer.

Goal:
    Find UI routes that return redirects/Response objects rather than templates.

Metrics reported:
    - count of routes without a renderer
    - route_name, function name, and request_method for each entry

Usage:
    python WIP/parity_check_views_no_renderer.py

Exit status:
    0 always (informational)
"""

from __future__ import annotations

import ast
from contextlib import suppress
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_VIEWS = REPO_ROOT / "server" / "fishtest" / "views.py"


def main() -> int:
    """Print all UI view_config routes without a renderer."""
    tree = ast.parse(SPEC_VIEWS.read_text())
    no_renderer = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "view_config"
            ):
                continue
            kw = {}
            for k in dec.keywords:
                if not k.arg:
                    continue
                with suppress(ValueError, SyntaxError):
                    kw[k.arg] = ast.literal_eval(k.value)
            if "route_name" in kw and "renderer" not in kw:
                no_renderer.append(
                    (
                        kw["route_name"],
                        node.name,
                        kw.get("request_method"),
                    ),
                )

    print("view_config without renderer:", len(no_renderer))
    for rn, fn, rm in no_renderer:
        print(f"  {rn:14s} {fn:20s} request_method={rm}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
