#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity check: API logic body (AST) equivalence for endpoints.

This is the API counterpart to `WIP/parity_check_views_ast.py`.

It treats `server/fishtest/api.py` as the Pyramid behavioral spec and checks that
for each `route_name="api_*"` view in the spec, the corresponding method body in
`server/fishtest/glue/api.py` is identical.

What it compares:
- Spec: methods inside `WorkerApi` / `UserApi` decorated with
    `@view_config(route_name="api_...")`
- Glue: methods with the same name inside `WorkerApi` / `UserApi`

Normalization choices (intentional):
- compares function bodies only (signatures ignored)
- ignores decorators, annotations, and type comments
- ignores a leading docstring statement

Usage:
  python WIP/parity_check_api_ast.py
    python WIP/parity_check_api_ast.py --strict

Exit status:
  0 if no method-body drift is detected
  1 if any drift or missing methods are detected
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_API = REPO_ROOT / "server" / "fishtest" / "api.py"
GLUE_API = REPO_ROOT / "server" / "fishtest" / "glue" / "api.py"

# Endpoints that are intentionally expected to differ due to framework response
# primitives (Pyramid Response/FileIter vs Starlette StreamingResponse).
EXPECTED_BODY_DRIFT: set[str] = {
    "api_download_pgn",
    "api_download_run_pgns",
}

_PYRAMID_EXCEPTION_STATUS: dict[str, int] = {
    "HTTPBadRequest": 400,
    "HTTPUnauthorized": 401,
    "HTTPNotFound": 404,
}


@dataclass(frozen=True)
class ApiEndpoint:
    """Spec endpoint identified by route_name + class/method name."""

    route_name: str
    class_name: str
    method_name: str


def _is_str_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _strip_leading_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and _is_str_constant(body[0].value):
        return body[1:]
    return body


def _body_dump(fn: ast.AST) -> str:
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node_type = type(fn).__name__
        raise TypeError("Expected a function node, got " + node_type)

    body = _strip_leading_docstring(list(fn.body))
    body = _normalize_body(body)
    normalized = ast.FunctionDef(
        name=fn.name,
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
    return ast.dump(normalized, include_attributes=False)


class _BodyNormalizer(ast.NodeTransformer):
    def visit_Call(self, node: ast.Call) -> ast.AST:
        visited = self.generic_visit(node)
        if not isinstance(visited, ast.Call):
            return visited
        node = visited

        # Normalize Pyramid-style handle_error(exception=HTTPNotFound)
        # to glue-style handle_error(status_code=404).
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "handle_error"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            new_keywords: list[ast.keyword] = []
            status_code_kw: ast.keyword | None = None
            for kw in node.keywords:
                if kw.arg == "exception" and isinstance(kw.value, ast.Name):
                    code = _PYRAMID_EXCEPTION_STATUS.get(kw.value.id)
                    if code is not None:
                        status_code_kw = ast.keyword(
                            arg="status_code",
                            value=ast.Constant(value=code),
                        )
                        continue
                new_keywords.append(kw)
            if status_code_kw is not None:
                new_keywords.append(status_code_kw)
                node.keywords = new_keywords
            return node

        return node

    def visit_Return(self, node: ast.Return) -> ast.AST:
        visited = self.generic_visit(node)
        if not isinstance(visited, ast.Return) or visited.value is None:
            return visited
        node = visited

        # Normalize Pyramid HTTPFound(url) to RedirectResponse(url, status_code=302)
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "HTTPFound"
            and len(node.value.args) == 1
        ):
            node.value = ast.Call(
                func=ast.Name(id="RedirectResponse", ctx=ast.Load()),
                args=[node.value.args[0]],
                keywords=[ast.keyword(arg="status_code", value=ast.Constant(302))],
            )
            return node

        return node


def _normalize_body(body: list[ast.stmt]) -> list[ast.stmt]:
    """Apply small framework-adapter normalizations to reduce false drift."""
    normalizer = _BodyNormalizer()
    out: list[ast.stmt] = []
    for stmt in body:
        new = normalizer.visit(stmt)
        if isinstance(new, list):
            out.extend(new)
        elif isinstance(new, ast.stmt):
            out.append(new)
    for stmt in out:
        ast.fix_missing_locations(stmt)
    return out


def _literal_str(value_node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(value_node)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _extract_spec_endpoints(tree: ast.Module) -> list[ApiEndpoint]:  # noqa: C901
    endpoints: list[ApiEndpoint] = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in {"WorkerApi", "UserApi"}:
            continue

        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for dec in item.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id == "view_config"
                ):
                    continue

                route_name: str | None = None
                for kw in dec.keywords:
                    if kw.arg == "route_name":
                        route_name = _literal_str(kw.value)
                        break

                if route_name and route_name.startswith("api_"):
                    endpoints.append(
                        ApiEndpoint(
                            route_name=route_name,
                            class_name=node.name,
                            method_name=item.name,
                        ),
                    )

    endpoints.sort(key=lambda e: (e.route_name, e.class_name, e.method_name))
    return endpoints


def _index_class_methods(tree: ast.Module) -> dict[tuple[str, str], ast.AST]:
    """Return a lookup for (class_name, method_name) -> function AST node."""
    out: dict[tuple[str, str], ast.AST] = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in {"WorkerApi", "UserApi"}:
            continue

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[(node.name, item.name)] = item

    return out


def main() -> int:  # noqa: C901, PLR0912
    """Run the API AST parity check and return process exit code."""
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail if any drift is detected, including endpoints that are normally "
            "expected to differ due to framework response primitives."
        ),
    )
    args = ap.parse_args()

    if not SPEC_API.exists():
        print(f"Missing spec file: {SPEC_API}")
        return 1
    if not GLUE_API.exists():
        print(f"Missing glue file: {GLUE_API}")
        return 1

    spec_tree = ast.parse(SPEC_API.read_text())
    glue_tree = ast.parse(GLUE_API.read_text())

    endpoints = _extract_spec_endpoints(spec_tree)
    spec_methods = _index_class_methods(spec_tree)
    glue_methods = _index_class_methods(glue_tree)

    missing: list[ApiEndpoint] = []
    changed: list[ApiEndpoint] = []
    allowed_changed: list[ApiEndpoint] = []

    for ep in endpoints:
        key = (ep.class_name, ep.method_name)
        spec_fn = spec_methods.get(key)
        glue_fn = glue_methods.get(key)
        if spec_fn is None or glue_fn is None:
            missing.append(ep)
            continue

        if _body_dump(spec_fn) != _body_dump(glue_fn):
            if not args.strict and ep.route_name in EXPECTED_BODY_DRIFT:
                allowed_changed.append(ep)
            else:
                changed.append(ep)

    print("spec api endpoints", len(endpoints))
    print("missing glue methods", len(missing))
    print("changed method bodies", len(changed))
    print("expected drift bodies", len(allowed_changed))

    if missing:
        print("\nMissing in glue:")
        for ep in missing[:200]:
            print(" ", ep.route_name, f"{ep.class_name}.{ep.method_name}")

    if changed:
        print("\nBody drift:")
        for ep in changed[:200]:
            print(" ", ep.route_name, f"{ep.class_name}.{ep.method_name}")

    if allowed_changed:
        print("\nExpected drift (informational):")
        for ep in allowed_changed[:200]:
            print(" ", ep.route_name, f"{ep.class_name}.{ep.method_name}")

    if missing or changed:
        return 1

    print("OK: no API endpoint method-body drift detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
