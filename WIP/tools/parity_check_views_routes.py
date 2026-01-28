#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity check: Pyramid UI views vs FastAPI HTTP UI views.

This script captures the ad-hoc checks used during the mechanical port.

It compares:
- route_name coverage and request_method coverage via @view_config
- notfound_view_config and forbidden_view_config presence
- _ROUTE_PATHS coverage in http/views.py

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
GLUE_VIEWS = REPO_ROOT / "server" / "fishtest" / "http" / "views.py"
WEB_ROOT = REPO_ROOT / "server" / "fishtest" / "web"


def _assert_glue_first_layout() -> bool:
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


def _parse_route_paths(glue_path: Path) -> dict[str, str]:
    tree = ast.parse(glue_path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_ROUTE_PATHS":
                    return ast.literal_eval(node.value)
    return {}


def main() -> int:  # noqa: C901, PLR0912, PLR0915
    """Run the UI route parity check and return process exit code."""
    if not _assert_glue_first_layout():
        return 1

    a = _parse_view_configs(SPEC_VIEWS)
    b = _parse_view_configs(GLUE_VIEWS)

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
    glue_routes = [
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
    glue_idx = index(glue_routes)

    spec_set = set(spec_idx)
    glue_set = set(glue_idx)

    print("spec route_name count", len(spec_set))
    print("http route_name count", len(glue_set))

    missing = sorted(spec_set - glue_set)
    extra = sorted(glue_set - spec_set)

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
            print("  ", rn, glue_idx[rn])

    mismatch = []
    for rn in sorted(spec_set & glue_set):
        spec_methods = sorted(
            {m if m is not None else "GET?" for _, m, _ in spec_idx[rn]},
        )
        glue_methods = sorted(
            {m if m is not None else "GET?" for _, m, _ in glue_idx[rn]},
        )
        if spec_methods != glue_methods:
            mismatch.append((rn, spec_methods, glue_methods))

    print("method mismatches", len(mismatch))
    if mismatch:
        ok = False
        for rn, sm, gm in mismatch:
            print(" ", rn, "spec", sm, "http", gm)

    route_paths = _parse_route_paths(GLUE_VIEWS)
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
    glue_nf = [
        (n, kw.get("renderer")) for dec, n, kw in b if dec == "notfound_view_config"
    ]
    glue_fb = [
        (n, kw.get("renderer")) for dec, n, kw in b if dec == "forbidden_view_config"
    ]

    print("spec notfound", spec_nf)
    print("http notfound", glue_nf)
    print("spec forbidden", spec_fb)
    print("http forbidden", glue_fb)

    if spec_nf != glue_nf or spec_fb != glue_fb:
        ok = False

    # Renderer parity check (per route_name/request_method).
    renderer_mismatches = []
    for rn in sorted(spec_set & glue_set):
        spec_by_method = defaultdict(set)
        glue_by_method = defaultdict(set)
        for _, method, renderer in spec_idx[rn]:
            spec_by_method[method].add(renderer)
        for _, method, renderer in glue_idx[rn]:
            glue_by_method[method].add(renderer)

        methods = sorted(
            set(spec_by_method) | set(glue_by_method),
            key=lambda x: (x is None, x),
        )
        for method in methods:
            if spec_by_method.get(method, set()) != glue_by_method.get(method, set()):
                renderer_mismatches.extend(
                    [
                        (
                            rn,
                            method,
                            sorted(spec_by_method.get(method, set())),
                            sorted(glue_by_method.get(method, set())),
                        ),
                    ],
                )

    print("renderer mismatches", len(renderer_mismatches))
    if renderer_mismatches:
        ok = False
        for rn, method, sr, gr in renderer_mismatches[:200]:
            m = method if method is not None else "GET?"
            print(" ", rn, "method", m, "spec", sr, "glue", gr)

    if ok:
        print("OK: route/method/path parity looks good.")
        return 0

    print("FAILED: mismatches found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
