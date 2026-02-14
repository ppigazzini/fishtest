#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity check: Pyramid API routes vs FastAPI HTTP API routes.

This script captures the ad-hoc checks used during the mechanical port and
reports route coverage parity.

It compares:
- Pyramid spec: server/fishtest/api.py (route_name="api_...")
- FastAPI HTTP: server/fishtest/http/api.py (@router.<method>("/api/..."))

Metrics reported:
- route_name counts on both sides
- missing and extra API endpoints (method + route_name)

Usage:
    python WIP/parity_check_api_routes.py

Exit status:
    0 if parity looks good (all expected endpoints present)
    1 if mismatches are found
"""

from __future__ import annotations

import ast
from contextlib import suppress
from pathlib import Path

API_PATH_MIN_PARTS = 3
API_PATH_STEM_INDEX = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_API = REPO_ROOT / "server" / "fishtest" / "api.py"
HTTP_API = REPO_ROOT / "server" / "fishtest" / "http" / "api.py"
WEB_ROOT = REPO_ROOT / "server" / "fishtest" / "web"


_SPEC_TO_HTTP_STEM_OVERRIDES: dict[str, str] = {
    # Historical naming differences between route_name suffixes and http paths.
    "download_nn": "nn",
    "download_pgn": "pgn",
    "download_run_pgns": "run_pgns",
}


def _iter_pyramid_api_routes(path: Path) -> set[tuple[str, str]]:  # noqa: C901
    """Extract (method, route_name) from Pyramid @view_config decorators.

    Notes:
    - If a view omits request_method, Pyramid semantics are effectively "any".
      We represent that as "*".
    - Class-level @view_defaults(...) are respected.

    """
    tree = ast.parse(path.read_text())
    routes: set[tuple[str, str]] = set()

    def _decode_value(node: ast.AST) -> object | None:
        with suppress(ValueError, SyntaxError):
            return ast.literal_eval(node)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _get_decorator_kwargs(dec: ast.Call) -> dict[str, object]:
        kw: dict[str, object] = {}
        for k in dec.keywords:
            if not k.arg:
                continue
            v = _decode_value(k.value)
            if v is not None:
                kw[k.arg] = v
        return kw

    def _extract_view_defaults(cls: ast.ClassDef) -> dict[str, object]:
        for dec in cls.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "view_defaults"
            ):
                return _get_decorator_kwargs(dec)
        return {}

    def _add_routes_from_function(
        fn: ast.AST,
        defaults: dict[str, object],
    ) -> None:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return

        for dec in fn.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "view_config"
            ):
                continue

            kw = _get_decorator_kwargs(dec)
            rn = kw.get("route_name")
            if not (isinstance(rn, str) and rn.startswith("api_")):
                continue

            rm = kw.get("request_method", defaults.get("request_method"))
            method = "*" if rm is None else (rm if isinstance(rm, str) else "*")
            routes.add((method.upper(), rn))

    # The Pyramid spec uses @view_config on methods of a view class.
    # Respect @view_defaults on that class so request_method defaults match.
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            defaults = _extract_view_defaults(node)
            for item in node.body:
                _add_routes_from_function(item, defaults=defaults)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _add_routes_from_function(node, defaults={})

    return routes


def _iter_fastapi_api_paths(path: Path) -> list[tuple[str, str]]:
    """Extract FastAPI router paths like @router.get("/api/..."), etc."""
    tree = ast.parse(path.read_text())
    out: list[tuple[str, str]] = []

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            # Matches router.get/post/options/etc
            if not (isinstance(dec.func, ast.Attribute) and dec.func.attr):
                continue
            if not (
                isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"
            ):
                continue
            if not dec.args:
                continue
            path_lit: object | None = None
            with suppress(ValueError, SyntaxError):
                path_lit = ast.literal_eval(dec.args[0])
            if isinstance(path_lit, str) and path_lit.startswith("/api/"):
                out.append((dec.func.attr.lower(), path_lit))

    return out


def _http_stem_from_path(path: str) -> str:
    # /api/get_run/{id} -> get_run
    parts = path.split("/")
    if len(parts) < API_PATH_MIN_PARTS:
        return path
    return parts[API_PATH_STEM_INDEX]


def _spec_stem_from_route_name(route_name: str) -> str:
    # api_get_run -> get_run
    suffix = route_name[len("api_") :]
    return _SPEC_TO_HTTP_STEM_OVERRIDES.get(suffix, suffix)


def main() -> int:  # noqa: C901, PLR0912, PLR0915
    """Run the API parity check and return process exit code."""
    if WEB_ROOT.exists():
        route_files = sorted(WEB_ROOT.rglob("routes_*.py"))
        if route_files:
            print("FAILED: http-first layout violation (web route modules found).")
            for path in route_files:
                rel = path.relative_to(REPO_ROOT)
                print("  ", rel)
            return 1

    if not SPEC_API.exists():
        print(f"Missing spec file: {SPEC_API}")
        return 1
    if not HTTP_API.exists():
        print(f"Missing http file: {HTTP_API}")
        return 1

    pyramid_routes = _iter_pyramid_api_routes(SPEC_API)
    http_routes = _iter_fastapi_api_paths(HTTP_API)
    http_routes_no_options = [(m, p) for m, p in http_routes if m.lower() != "options"]

    http_pairs: set[tuple[str, str]] = {
        (m.upper(), _http_stem_from_path(p)) for m, p in http_routes_no_options
    }
    http_stems: set[str] = {stem for _, stem in http_pairs}

    spec_pairs: set[tuple[str, str]] = {
        (m.upper(), _spec_stem_from_route_name(rn)) for m, rn in pyramid_routes
    }
    spec_stems: set[str] = {stem for _, stem in spec_pairs}

    print("Pyramid api.py route_name count:", len({rn for _, rn in pyramid_routes}))
    print("FastAPI http/api.py /api paths count:", len([p for _, p in http_routes]))

    if pyramid_routes:
        print("\nPyramid route_names:")
        for rn in sorted({rn for _, rn in pyramid_routes}):
            print("  ", rn)

    print("\nFastAPI /api paths:")
    for method, path in sorted(http_routes, key=lambda x: (x[1], x[0])):
        print(f"  {method.upper():7s} {path}")

    missing_stems = sorted(spec_stems - http_stems)

    # Method mismatches: only for method-specified routes (not '*').
    method_required = {(m, stem) for (m, stem) in spec_pairs if m != "*"}
    missing_methods = sorted(method_required - http_pairs)

    # Purely informational extras.
    extra = sorted(http_pairs - method_required)

    ok = True

    print("\n(spec→http) normalized endpoint pairs:")
    print("  spec pairs", len(spec_pairs))
    print("  http pairs", len(http_pairs), "(OPTIONS ignored)")
    print("  missing stems", len(missing_stems))
    print("  missing methods", len(missing_methods))
    print("  extra (informational)", len(extra))

    if missing_stems:
        ok = False
        print("\nMissing in http (stem):")
        for stem in missing_stems:
            print("  ", stem)

    if missing_methods:
        ok = False
        print("\nMissing in http (method, stem) for method-specified routes:")
        for m, stem in missing_methods:
            print("  ", m, stem)

    if extra:
        # Extra endpoints might exist during transitions; report them but do not fail.
        print("\nExtra in http (method, stem):")
        for m, stem in extra:
            print("  ", m, stem)

    if ok:
        print("\nOK: normalized API endpoint coverage/methods look good.")
        print(
            "Note: This script checks route presence + HTTP method; "
            "behavioral parity is validated separately.",
        )
        return 0

    print("\nFAILED: API route/method mismatches found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
