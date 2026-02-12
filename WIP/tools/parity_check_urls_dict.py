#!/usr/bin/env python3
# ruff: noqa: T201
"""Validate URL mappings in build_template_context against router paths.

Goal:
    Ensure the urls dict entries correspond to registered routes and report
    any missing or unused mappings.

Metrics reported:
    - per-entry OK/MISSING status
    - unused routes not present in the urls dict
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from starlette.routing import Route
except ModuleNotFoundError:
    try:
        from fastapi.routing import APIRoute as Route
    except ModuleNotFoundError as exc:  # pragma: no cover
        message = "starlette (or fastapi) is required to validate router paths"
        raise ModuleNotFoundError(message) from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from fishtest.http.api import router as api_router  # noqa: E402
from fishtest.http.views import router as views_router  # noqa: E402

BOUNDARY_PATH = SERVER_ROOT / "fishtest" / "http" / "boundary.py"


@dataclass(frozen=True)
class UrlCheckResult:
    """Result of validating one URL mapping entry."""

    key: str
    value: str
    ok: bool


def _literal_urls_dict(source: str) -> dict[str, str]:
    tree = ast.parse(source)

    class Finder(ast.NodeVisitor):
        def __init__(self) -> None:
            self.urls: dict[str, str] | None = None

        def visit_Dict(self, node: ast.Dict) -> None:
            for key, value in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "urls"
                    and isinstance(value, ast.Dict)
                ):
                    try:
                        self.urls = ast.literal_eval(value)
                    except ValueError, SyntaxError:
                        return
                    else:
                        return
            self.generic_visit(node)

    finder = Finder()
    finder.visit(tree)
    if finder.urls is None:
        message = "Unable to locate urls dict in boundary.py"
        raise RuntimeError(message)
    return finder.urls


def _route_paths() -> set[str]:
    routes = []
    for router in (views_router, api_router):
        routes.extend(route for route in router.routes if isinstance(route, Route))
    return {route.path for route in routes}


def _is_prefix_key(key: str, value: str) -> bool:
    if key.endswith("_prefix"):
        return True
    return value.endswith("/")


def _path_matches_route(path: str, route_path: str) -> bool:
    if path == route_path:
        return True
    if "{" not in route_path:
        return False
    pattern = re.sub(r"\{[^/]+\}", r"[^/]+", route_path)
    return bool(re.fullmatch(pattern, path))


def _check_urls(urls: dict[str, str], route_paths: set[str]) -> list[UrlCheckResult]:
    results: list[UrlCheckResult] = []
    for key, value in urls.items():
        path = value.split("?", 1)[0]
        ok = False
        if any(_path_matches_route(path, route) for route in route_paths):
            ok = True
        elif _is_prefix_key(key, path):
            ok = any(route.startswith(path) for route in route_paths)
        results.append(UrlCheckResult(key=key, value=value, ok=ok))
    return results


def main() -> int:
    """Report mismatches between URL mappings and registered routes."""
    source = BOUNDARY_PATH.read_text(encoding="utf-8")
    urls = _literal_urls_dict(source)
    route_paths = _route_paths()
    results = _check_urls(urls, route_paths)

    failures = [item for item in results if not item.ok]
    for item in results:
        status = "OK" if item.ok else "MISSING"
        print(f"{status}: {item.key} -> {item.value}")

    if failures:
        return 1

    unused = sorted(route_paths - {value.split("?", 1)[0] for value in urls.values()})
    if unused:
        print("Unused routes (not in urls dict):")
        for path in unused:
            print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
