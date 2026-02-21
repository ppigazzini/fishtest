#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity check: API logic body (AST) equivalence for endpoints.

This is the API counterpart to `WIP/parity_check_views_ast.py`.

It treats `server/fishtest/api.py` as the Pyramid behavioral spec and checks that
for each `route_name="api_*"` view in the spec, the corresponding method body in
`server/fishtest/http/api.py` is identical.

What it compares:
- Spec: methods inside `WorkerApi` / `UserApi` decorated with
    `@view_config(route_name="api_...")`
- HTTP: methods with the same name inside `WorkerApi` / `UserApi`

Normalization choices (intentional):
- compares function bodies only (signatures ignored)
- ignores decorators, annotations, and type comments
- ignores a leading docstring statement

Metrics reported:
- endpoint counts for spec/http
- missing/extra endpoints
- changed body counts plus expected drift list

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
HTTP_API = REPO_ROOT / "server" / "fishtest" / "http" / "api.py"

# Endpoints that are intentionally expected to differ due to framework response
# primitives (Pyramid Response/FileIter vs Starlette StreamingResponse).
EXPECTED_BODY_DRIFT: set[str] = {
    "api_download_pgn",
    "api_download_run_pgns",
}

REQUIRED_SPEC_CLASSES: set[str] = {"InternalApi"}

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
    def visit_If(self, node: ast.If) -> ast.AST | list[ast.stmt]:
        new_node = self.generic_visit(node)
        if not isinstance(new_node, ast.If):
            return new_node
        node = new_node

        # Normalize "if ...: return ... else: ..." into
        # "if ...: return ..." followed by the else body.
        if node.orelse and _ends_with_return(node.body):
            normalized_if = ast.If(test=node.test, body=node.body, orelse=[])
            return [normalized_if, *node.orelse]

        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        visited = self.generic_visit(node)
        if not isinstance(visited, ast.Call):
            return visited
        node = visited

        normalized_format = _format_call_to_joined_str(node)
        if normalized_format is not None:
            return normalized_format

        # Normalize Pyramid-style handle_error(exception=HTTPNotFound)
        # to http-style handle_error(status_code=404).
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


def _ends_with_return(body: list[ast.stmt]) -> bool:
    if not body:
        return False
    last = body[-1]
    return isinstance(last, ast.Return)


def _format_call_to_joined_str(node: ast.Call) -> ast.JoinedStr | None:
    template: str | None = None
    if (
        not node.keywords
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        func_value = node.func.value
        if isinstance(func_value, ast.Constant) and isinstance(func_value.value, str):
            template = func_value.value

    if template is None:
        return None
    parts = template.split("{}")
    if len(parts) != len(node.args) + 1:
        return None

    values: list[ast.expr] = []
    for idx, part in enumerate(parts):
        if part:
            values.append(ast.Constant(part))
        if idx < len(node.args):
            values.append(
                ast.FormattedValue(
                    value=node.args[idx],
                    conversion=-1,
                    format_spec=None,
                ),
            )

    joined = ast.JoinedStr(values=values)
    ast.fix_missing_locations(joined)
    return joined


def _literal_str(value_node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(value_node)
    except ValueError, SyntaxError:
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


def _class_method_names(tree: ast.Module) -> dict[str, set[str]]:
    """Return a lookup for class_name -> set of method names."""
    out: dict[str, set[str]] = {"WorkerApi": set(), "UserApi": set()}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in out:
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[node.name].add(item.name)
    return out


def _class_presence(tree: ast.Module) -> set[str]:
    """Return top-level class names present in the module."""
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def _missing_required_classes(
    *,
    spec_classes: set[str],
    http_classes: set[str],
) -> list[str]:
    return sorted(
        class_name
        for class_name in REQUIRED_SPEC_CLASSES
        if class_name in spec_classes and class_name not in http_classes
    )


def _print_missing_classes(missing_classes: list[str]) -> None:
    if not missing_classes:
        return
    print("\nMissing required classes:")
    for class_name in missing_classes:
        print(" ", class_name)


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail if any drift is detected, including endpoints that are normally "
            "expected to differ due to framework response primitives."
        ),
    )
    return ap.parse_args()


def _report_main_counts(
    *,
    endpoints: list[ApiEndpoint],
    missing: list[ApiEndpoint],
    changed: list[ApiEndpoint],
    allowed_changed: list[ApiEndpoint],
    extra_methods: list[tuple[str, str]],
) -> None:
    coverage = (len(endpoints) - len(missing)) / len(endpoints) if endpoints else 1.0
    for label, value in (
        ("spec api endpoints", len(endpoints)),
        ("endpoint coverage ratio", f"{coverage:.3f}"),
        ("missing http methods", len(missing)),
        ("changed method bodies", len(changed)),
        ("expected drift bodies", len(allowed_changed)),
        ("extra http methods", len(extra_methods)),
    ):
        print(label, value)


def _print_endpoints(title: str, items: list[ApiEndpoint]) -> None:
    if not items:
        return
    print(f"\n{title}")
    for ep in items[:200]:
        print(" ", ep.route_name, f"{ep.class_name}.{ep.method_name}")


def _print_extra_methods(extra_methods: list[tuple[str, str]]) -> None:
    if not extra_methods:
        return
    print("\nExtra http methods (informational):")
    for class_name, name in extra_methods[:200]:
        print(" ", f"{class_name}.{name}")


def main() -> int:
    """Run the API AST parity check and return process exit code."""
    args = _parse_args()

    if not SPEC_API.exists():
        print(f"Missing spec file: {SPEC_API}")
        return 1
    if not HTTP_API.exists():
        print(f"Missing http file: {HTTP_API}")
        return 1

    spec_tree = ast.parse(SPEC_API.read_text())
    http_tree = ast.parse(HTTP_API.read_text())

    endpoints = _extract_spec_endpoints(spec_tree)
    spec_methods = _index_class_methods(spec_tree)
    http_methods = _index_class_methods(http_tree)
    spec_names = _class_method_names(spec_tree)
    http_names = _class_method_names(http_tree)
    spec_classes = _class_presence(spec_tree)
    http_classes = _class_presence(http_tree)

    missing: list[ApiEndpoint] = []
    changed: list[ApiEndpoint] = []
    allowed_changed: list[ApiEndpoint] = []
    extra_methods: list[tuple[str, str]] = []
    missing_classes = _missing_required_classes(
        spec_classes=spec_classes,
        http_classes=http_classes,
    )

    for ep in endpoints:
        key = (ep.class_name, ep.method_name)
        spec_fn = spec_methods.get(key)
        http_fn = http_methods.get(key)
        if spec_fn is None or http_fn is None:
            missing.append(ep)
            continue

        if _body_dump(spec_fn) != _body_dump(http_fn):
            if not args.strict and ep.route_name in EXPECTED_BODY_DRIFT:
                allowed_changed.append(ep)
            else:
                changed.append(ep)

    for class_name in ("WorkerApi", "UserApi"):
        spec_set = spec_names.get(class_name, set())
        http_set = http_names.get(class_name, set())
        extra_methods.extend((class_name, name) for name in sorted(http_set - spec_set))

    _report_main_counts(
        endpoints=endpoints,
        missing=missing,
        changed=changed,
        allowed_changed=allowed_changed,
        extra_methods=extra_methods,
    )
    print("missing required classes", len(missing_classes))

    _print_endpoints("Missing in http:", missing)
    _print_endpoints("Body drift:", changed)
    _print_endpoints("Expected drift (informational):", allowed_changed)
    _print_extra_methods(extra_methods)
    _print_missing_classes(missing_classes)

    if (
        missing
        or changed
        or missing_classes
        or (args.strict and (extra_methods or allowed_changed))
    ):
        return 1

    print("OK: no API endpoint method-body drift detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
