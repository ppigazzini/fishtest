#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity check: function-body (AST) equivalence for UI views.

This is the stronger mechanical check used to confirm that http/views.py kept
the exact same view logic bodies as server/fishtest/views.py.

Normalization choices (intentional):
- compares *function bodies only* (signatures ignored)
- ignores decorators, annotations, and type comments
- ignores a leading docstring expression statement

Usage:
  python WIP/parity_check_views_ast.py

Exit status:
  0 if all common functions have identical bodies
  1 if any drift is detected
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_VIEWS = REPO_ROOT / "server" / "fishtest" / "views.py"
GLUE_VIEWS = REPO_ROOT / "server" / "fishtest" / "http" / "views.py"

# Functions that are expected to diverge due to framework wiring or refactors.
# Keep this list small and documented.
EXPECTED_BODY_DRIFT: set[str] = set()


def _func_body_dumps(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text())
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr):
                body0 = body[0]
                value = body0.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    body = body[1:]

            # Normalize to compare function *bodies only*.
            normalized = ast.FunctionDef(
                name=node.name,
                args=ast.arguments(
                    posonlyargs=[],
                    args=[],
                    vararg=None,
                    kwonlyargs=[],
                    kw_defaults=[],
                    kwarg=None,
                    defaults=[],
                ),
                body=body,
                decorator_list=[],
                returns=None,
                type_comment=None,
            )
            ast.fix_missing_locations(normalized)
            out[node.name] = ast.dump(normalized, include_attributes=False)
    return out


def main() -> int:
    """Run the AST body parity check and return process exit code."""
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Fail on expected drift and extra functions.",
    )
    args = ap.parse_args()

    spec = _func_body_dumps(SPEC_VIEWS)
    glue = _func_body_dumps(GLUE_VIEWS)

    spec_only = sorted(set(spec) - set(glue))
    glue_only = sorted(set(glue) - set(spec))
    common = sorted(set(spec) & set(glue))

    changed_all = [name for name in common if spec[name] != glue[name]]
    allowed_changed = [name for name in changed_all if name in EXPECTED_BODY_DRIFT]
    changed = [name for name in changed_all if name not in EXPECTED_BODY_DRIFT]

    coverage = (len(common) / len(spec)) if spec else 1.0
    print("spec functions", len(spec))
    print("http functions", len(glue))
    print("common functions", len(common))
    print("coverage ratio", f"{coverage:.3f}")
    print("missing in http", len(spec_only))
    print("extra in http", len(glue_only))
    print("changed ast bodies", len(changed))
    print("expected drift bodies", len(allowed_changed))

    if spec_only:
        print("\nMissing in http:")
        for name in spec_only[:200]:
            print(" ", name)

    if glue_only:
        print("\nExtra in http:")
        for name in glue_only[:200]:
            print(" ", name)

    if changed:
        print("\nBody drift:")
        for name in changed[:200]:
            print(" ", name)

    if allowed_changed:
        print("\nExpected drift (informational):")
        for name in allowed_changed[:200]:
            print(" ", name)

    if spec_only or changed or (args.strict and (glue_only or allowed_changed)):
        return 1

    print("OK: no function-body drift detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
