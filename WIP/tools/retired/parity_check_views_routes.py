#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity check: Pyramid UI views vs FastAPI HTTP UI views.

This script captures the ad-hoc checks used during the mechanical port and
reports routing coverage parity between Pyramid and FastAPI.

It compares:
- route_name coverage and request_method coverage via @view_config (spec side)
- _VIEW_ROUTES list (http side, post M11-Phase1)
- notfound/forbidden error handler presence

Metrics reported:
- route_name counts on both sides
- missing and extra route_name entries
- method/renderer mismatches per route_name

Usage:
    python WIP/parity_check_views_routes.py

Exit status:
    0 if parity looks good
    1 if mismatches are found
"""

from __future__ import annotations

import ast
from collections import defaultdict
from contextlib import suppress
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_VIEWS = REPO_ROOT / "server" / "fishtest" / "views.py"
HTTP_VIEWS = REPO_ROOT / "server" / "fishtest" / "http" / "views.py"
WEB_ROOT = REPO_ROOT / "server" / "fishtest" / "web"
_MIN_ROUTE_TUPLE_LEN = 3

# Reverse mapping: path → route_name (matches the removed _ROUTE_PATHS).
# Used to translate _VIEW_ROUTES paths back to route_names for comparison.
_PATH_TO_ROUTE_NAME = {
    "/": "home",
    "/login": "login",
    "/upload": "nn_upload",
    "/logout": "logout",
    "/signup": "signup",
    "/user/{username}": "user",
    "/user": "profile",
    "/user_management": "user_management",
    "/contributors": "contributors",
    "/contributors/monthly": "contributors_monthly",
    "/actions": "actions",
    "/nns": "nns",
    "/sprt_calc": "sprt_calc",
    "/rate_limits": "rate_limits",
    "/workers/{worker_name}": "workers",
    "/tests": "tests",
    "/tests/machines": "tests_machines",
    "/tests/finished": "tests_finished",
    "/tests/run": "tests_run",
    "/tests/view/{id}": "tests_view",
    "/tests/tasks/{id}": "tests_tasks",
    "/tests/user/{username}": "tests_user",
    "/tests/stats/{id}": "tests_stats",
    "/tests/live_elo/{id}": "tests_live_elo",
    "/tests/modify": "tests_modify",
    "/tests/delete": "tests_delete",
    "/tests/stop": "tests_stop",
    "/tests/approve": "tests_approve",
    "/tests/purge": "tests_purge",
}


def _assert_http_first_layout() -> bool:
    """Fail if route logic is split into web/ modules (http-first rule)."""
    if not WEB_ROOT.exists():
        return True

    route_files = sorted(WEB_ROOT.rglob("routes_*.py"))
    if not route_files:
        return True

    print("FAILED: http-first layout violation (web route modules found).")
    for path in route_files:
        rel = path.relative_to(REPO_ROOT)
        print("  ", rel)
    return False


def _normalize_request_methods(value: object) -> list[str | None]:
    if value is None:
        return [None]
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        out = [v for v in value if v is None or isinstance(v, str)]
        return out or [None]
    return [None]


def _get_assignment_value(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    return None


def _literal_eval(node: ast.AST) -> object | None:
    with suppress(ValueError, SyntaxError):
        return ast.literal_eval(node)
    return None


def _parse_view_configs(path: Path) -> list[tuple[str, str, dict[str, object]]]:
    tree = ast.parse(path.read_text())
    out: list[tuple[str, str, dict[str, object]]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id
                    in {"view_config", "notfound_view_config", "forbidden_view_config"}
                ):
                    kw = {}
                    for k in dec.keywords:
                        if not k.arg:
                            continue
                        with suppress(ValueError, SyntaxError):
                            kw[k.arg] = ast.literal_eval(k.value)
                    out.append((dec.func.id, node.name, kw))
    return out


def _parse_view_routes(http_path: Path) -> list[tuple[str, str, dict[str, object]]]:
    """Parse _VIEW_ROUTES list from http/views.py (post M11-Phase1 format).

    Returns list of (dec_type, fn_name, kw_dict) matching _parse_view_configs format.
    The kw_dict includes route_name (reverse-mapped from path).
    """
    tree = ast.parse(http_path.read_text())
    value = _get_assignment_value(tree, "_VIEW_ROUTES")
    if not isinstance(value, (ast.List, ast.Tuple)):
        return []

    out: list[tuple[str, str, dict[str, object]]] = []
    for elt in value.elts:
        if not isinstance(elt, (ast.Tuple, ast.List)):
            continue
        if len(elt.elts) < _MIN_ROUTE_TUPLE_LEN:
            continue
        fn_node, path_node, cfg_node = elt.elts[0], elt.elts[1], elt.elts[2]
        fn_name = fn_node.id if isinstance(fn_node, ast.Name) else "?"
        path = _literal_eval(path_node)
        if not isinstance(path, str):
            continue
        cfg_value = _literal_eval(cfg_node) if isinstance(cfg_node, ast.Dict) else {}
        if not isinstance(cfg_value, dict):
            cfg_value = {}
        cfg: dict[str, object] = {str(key): value for key, value in cfg_value.items()}
        route_name = _PATH_TO_ROUTE_NAME.get(path, path)
        cfg["route_name"] = route_name
        out.append(("view_config", fn_name, cfg))
    return out


def _parse_route_paths(http_path: Path) -> dict[str, str]:
    """Parse _ROUTE_PATHS or derive from _VIEW_ROUTES."""
    tree = ast.parse(http_path.read_text())
    route_paths = _get_assignment_value(tree, "_ROUTE_PATHS")
    if isinstance(route_paths, ast.Dict):
        value = _literal_eval(route_paths)
        if isinstance(value, dict):
            return {str(key): str(val) for key, val in value.items()}
    # Fall back: derive from _VIEW_ROUTES
    routes = _parse_view_routes(http_path)
    return {
        str(kw["route_name"]): str(kw.get("route_name", ""))
        for _, _, kw in routes
        if "route_name" in kw
    }


def main() -> int:  # noqa: C901, PLR0912, PLR0915
    """Run the UI route parity check and return process exit code."""
    if not _assert_http_first_layout():
        return 1

    a = _parse_view_configs(SPEC_VIEWS)
    # Post M11-Phase1: HTTP side uses _VIEW_ROUTES instead of @view_config decorators
    b_decorators = _parse_view_configs(HTTP_VIEWS)
    b_routes = _parse_view_routes(HTTP_VIEWS)
    b = b_decorators or b_routes

    def _as_route_name(value: object) -> str | None:
        return value if isinstance(value, str) else None

    spec_routes = [
        (
            name,
            _as_route_name(kw.get("route_name")),
            kw.get("request_method"),
            kw.get("renderer"),
        )
        for dec, name, kw in a
        if dec == "view_config"
    ]
    http_routes = [
        (
            name,
            _as_route_name(kw.get("route_name")),
            kw.get("request_method"),
            kw.get("renderer"),
        )
        for dec, name, kw in b
        if dec == "view_config"
    ]

    def index(
        entries: list[tuple[str, str | None, object, object]],
    ) -> dict[str, list[tuple[str, str | None, object]]]:
        m = defaultdict(list)
        for fn, rn, rm, renderer in entries:
            if rn is None:
                continue
            for method in _normalize_request_methods(rm):
                m[rn].append((fn, method, renderer))
        return m

    spec_idx = index(spec_routes)
    http_idx = index(http_routes)

    spec_set = set(spec_idx)
    http_set = set(http_idx)

    print("spec route_name count", len(spec_set))
    print("http route_name count", len(http_set))

    missing = sorted(spec_set - http_set)
    extra = sorted(http_set - spec_set)

    ok = True

    if missing:
        ok = False
        print("missing in http", len(missing))
        for rn in missing:
            print("  ", rn, spec_idx[rn])

    if extra:
        ok = False
        print("extra in http", len(extra))
        for rn in extra:
            print("  ", rn, http_idx[rn])

    mismatch = []
    for rn in sorted(spec_set & http_set):
        spec_methods = sorted(
            {m if m is not None else "GET?" for _, m, _ in spec_idx[rn]},
        )
        http_methods = sorted(
            {m if m is not None else "GET?" for _, m, _ in http_idx[rn]},
        )
        if spec_methods != http_methods:
            mismatch.append((rn, spec_methods, http_methods))

    print("method mismatches", len(mismatch))
    if mismatch:
        ok = False
        for rn, sm, gm in mismatch:
            print(" ", rn, "spec", sm, "http", gm)

    route_paths = _parse_route_paths(HTTP_VIEWS)
    missing_paths = sorted(spec_set - set(route_paths))
    extra_paths = sorted(set(route_paths) - spec_set)

    print("_ROUTE_PATHS missing", len(missing_paths))
    print("_ROUTE_PATHS extra", len(extra_paths))
    if missing_paths:
        ok = False
        print(" missing examples", missing_paths[:20])
    if extra_paths:
        ok = False
        print(" extra examples", extra_paths[:20])

    spec_nf = [
        (n, kw.get("renderer")) for dec, n, kw in a if dec == "notfound_view_config"
    ]
    spec_fb = [
        (n, kw.get("renderer")) for dec, n, kw in a if dec == "forbidden_view_config"
    ]
    http_nf = [
        (n, kw.get("renderer")) for dec, n, kw in b if dec == "notfound_view_config"
    ]
    http_fb = [
        (n, kw.get("renderer")) for dec, n, kw in b if dec == "forbidden_view_config"
    ]

    print("spec notfound", spec_nf)
    print("http notfound", http_nf, "(handled by errors.py)" if not http_nf else "")
    print("spec forbidden", spec_fb)
    print("http forbidden", http_fb, "(handled by errors.py)" if not http_fb else "")

    # Post M11-Phase1: notfound/forbidden are handled by errors.py, not decorators.
    # Empty http_nf/http_fb is expected and not a failure.
    if http_nf and spec_nf != http_nf:
        ok = False
    if http_fb and spec_fb != http_fb:
        ok = False

    # Renderer parity check (per route_name/request_method).
    # Normalize renderer names: strip .mak / .html.j2 suffixes for cross-format
    # comparison.
    def _normalize_renderer(r: object) -> str | None:
        if r is None:
            return None
        s = str(r)
        for suffix in (".html.j2", ".mak"):
            if s.endswith(suffix):
                return s[: -len(suffix)]
        return s

    renderer_mismatches = []
    for rn in sorted(spec_set & http_set):
        spec_by_method = defaultdict(set)
        http_by_method = defaultdict(set)
        for _, method, renderer in spec_idx[rn]:
            spec_by_method[method].add(_normalize_renderer(renderer))
        for _, method, renderer in http_idx[rn]:
            http_by_method[method].add(_normalize_renderer(renderer))

        methods = sorted(
            set(spec_by_method) | set(http_by_method),
            key=lambda x: (x is None, x),
        )
        for method in methods:
            if spec_by_method.get(method, set()) != http_by_method.get(method, set()):
                renderer_mismatches.extend(
                    [
                        (
                            rn,
                            method,
                            sorted(spec_by_method.get(method, set())),
                            sorted(http_by_method.get(method, set())),
                        ),
                    ],
                )

    print("renderer mismatches", len(renderer_mismatches))
    if renderer_mismatches:
        ok = False
        for rn, method, sr, gr in renderer_mismatches[:200]:
            m = method if method is not None else "GET?"
            print(" ", rn, "method", m, "spec", sr, "http", gr)

    if ok:
        print("OK: route/method/path parity looks good.")
        return 0

    print("FAILED: mismatches found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
