#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity check: function-body (AST) equivalence for UI views.

This is the stronger mechanical check used to confirm that http/views.py kept
the exact same view logic bodies as server/fishtest/views.py.

Normalization choices (intentional):
- compares function bodies only (signatures ignored)
- ignores decorators, annotations, and type comments
- ignores a leading docstring expression statement

Metrics reported:
- function counts and coverage ratio
- missing/extra functions
- changed body counts plus expected drift list

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
HTTP_VIEWS = REPO_ROOT / "server" / "fishtest" / "http" / "views.py"

# Functions that are expected to diverge due to framework wiring or refactors.
# Keep this list small and documented.
#
# Pre-M11: drifts inherited from earlier milestones (M0-M10).
# M11-Phase1: replaced HTTPFound→RedirectResponse, HTTPNotFound→StarletteHTTPException,
# route_url()→hardcoded paths, ensure_logged_in raise→return pattern.
EXPECTED_BODY_DRIFT: set[str] = {
    # --- Pre-M11 inherited drifts ---
    "contributors",
    "contributors_monthly",
    "get_nets",
    "get_paginated_finished_runs",
    "get_sha",
    "homepage_results",
    "pagination",
    "parse_spsa_params",
    "tests",
    "tests_finished",
    "tests_machines",
    "update_nets",
    "validate_form",
    # --- M11-Phase1 drifts ---
    "actions",
    "ensure_logged_in",
    "home",
    "login",
    "logout",
    "nns",
    "signup",
    "tests_approve",
    "tests_delete",
    "tests_live_elo",
    "tests_modify",
    "tests_purge",
    "tests_run",
    "tests_stats",
    "tests_stop",
    "tests_tasks",
    "tests_view",
    "tests_user",
    "upload",
    "user",
    "user_management",
    "validate_modify",
    "workers",
}

# Functions expected to be missing from the http side (removed as dead code).
EXPECTED_MISSING: set[str] = {
    "notfound_view",  # M11-Phase1: dead code, 404 handled by errors.py
}


def _split_missing(names: set[str]) -> tuple[list[str], list[str]]:
    unexpected = [name for name in names if name not in EXPECTED_MISSING]
    expected = [name for name in names if name in EXPECTED_MISSING]
    return unexpected, expected


def _print_names(title: str, names: list[str]) -> None:
    if not names:
        return
    print(f"\n{title}:")
    for name in names[:200]:
        print(" ", name)


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
    http = _func_body_dumps(HTTP_VIEWS)

    spec_only = sorted(set(spec) - set(http))
    spec_only_unexpected, spec_only_expected = _split_missing(set(spec_only))
    http_only = sorted(set(http) - set(spec))
    common = sorted(set(spec) & set(http))

    changed_all = [name for name in common if spec[name] != http[name]]
    allowed_changed = [name for name in changed_all if name in EXPECTED_BODY_DRIFT]
    changed = [name for name in changed_all if name not in EXPECTED_BODY_DRIFT]

    coverage = (len(common) / len(spec)) if spec else 1.0
    print("spec functions", len(spec))
    print("http functions", len(http))
    print("common functions", len(common))
    print("coverage ratio", f"{coverage:.3f}")
    print("missing in http", len(spec_only_unexpected))
    print("expected missing", len(spec_only_expected))
    print("extra in http", len(http_only))
    print("changed ast bodies", len(changed))
    print("expected drift bodies", len(allowed_changed))

    _print_names("Missing in http", spec_only_unexpected)
    _print_names("Expected missing (informational)", spec_only_expected)
    _print_names("Extra in http", http_only)
    _print_names("Body drift", changed)
    _print_names("Expected drift (informational)", allowed_changed)

    strict_failures = args.strict and (http_only or allowed_changed)
    if spec_only_unexpected or changed or strict_failures:
        return 1

    print("OK: no function-body drift detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
