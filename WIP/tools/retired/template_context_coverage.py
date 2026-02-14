#!/usr/bin/env python3
# ruff: noqa: T201
"""Report template context coverage against parity context data.

Goal:
    Identify template variable references that are missing from
    template_parity_context.json.

Usage:
    python WIP/tools/template_context_coverage.py
    python WIP/tools/template_context_coverage.py --engine jinja
    python WIP/tools/template_context_coverage.py --templates tests_view.html.j2
    python WIP/tools/template_context_coverage.py --json

Exit status:
    0 if no missing references are found (Jinja only unless --strict-mako)
    1 if missing references are found
    2 on error
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, meta

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from fishtest.http import jinja as jinja_renderer  # noqa: E402
from fishtest.http import template_helpers as helpers  # noqa: E402

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
DEFAULT_OUTPUT = REPO_ROOT / "WIP" / "tools" / "template_context_coverage.json"
DEFAULT_MAKO_DIR = REPO_ROOT / "server" / "fishtest" / "templates"
DEFAULT_JINJA_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2"
SKIP_TEMPLATES = {"base.mak"}
BASE_CONTEXT_KEYS = {"static_url"}

MAKO_EXPR_RE = re.compile(r"\$\{(.*?)\}", re.DOTALL)
MAKO_BLOCK_RE = re.compile(r"<%([!]?)\s*(.*?)\s*%>", re.DOTALL)
MAKO_DOC_RE = re.compile(r"<%doc>.*?</%doc>", re.DOTALL)
MAKO_TEXT_RE = re.compile(r"<%text>.*?</%text>", re.DOTALL)


@dataclass(frozen=True)
class CoverageResult:
    """Coverage result for a single template and engine."""

    template: str
    engine: str
    missing: list[str]
    referenced: list[str]


class _NameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)
        self.generic_visit(node)


def _collect_names(tree: ast.AST) -> set[str]:
    collector = _NameCollector()
    collector.visit(tree)
    return collector.names


def _names_from_expr(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return _collect_names(tree)


def _names_from_code(code: str) -> set[str]:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return set()
    return _collect_names(tree)


def _strip_mako_blocks(source: str) -> str:
    return MAKO_TEXT_RE.sub("", MAKO_DOC_RE.sub("", source))


def _mako_names(source: str) -> set[str]:
    source = _strip_mako_blocks(source)
    names: set[str] = set()
    lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("##"):
            continue
        lines.append(line)
    source = "\n".join(lines)

    for match in MAKO_EXPR_RE.finditer(source):
        names.update(_names_from_expr(match.group(1)))

    for match in MAKO_BLOCK_RE.finditer(source):
        names.update(_names_from_code(match.group(2)))

    for line in source.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("%"):
            continue
        code = stripped[1:].strip()
        if not code:
            continue
        if code.startswith(("end", "else", "elif", "except", "finally")):
            continue
        if code.endswith(":"):
            names.update(_names_from_code(f"{code}\n    pass"))
        else:
            names.update(_names_from_code(code))

    return names


def _jinja_names(env: Environment, source: str) -> set[str]:
    parsed = env.parse(source)
    return set(meta.find_undeclared_variables(parsed))


def _load_context(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        message = "Context file must contain a JSON object."
        raise TypeError(message)
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _logical_name(name: str) -> str:
    if name.endswith(".html.j2"):
        return name[: -len(".html.j2")] + ".mak"
    return name


def _template_names_mako(path: Path) -> set[str]:
    return {
        item.name
        for item in path.glob("*.mak")
        if item.is_file() and item.name not in SKIP_TEMPLATES
    }


def _template_names_jinja(path: Path) -> set[str]:
    return {
        _logical_name(item.name)
        for item in path.glob("*.html.j2")
        if item.is_file() and _logical_name(item.name) not in SKIP_TEMPLATES
    }


def _jinja_path(template_dir: Path, template: str) -> Path:
    if template.endswith(".mak"):
        name = template[: -len(".mak")] + ".html.j2"
    else:
        name = template
    return template_dir / name


def _allowed_names(env: Environment) -> set[str]:
    builtins = {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "filter",
        "float",
        "int",
        "len",
        "list",
        "map",
        "max",
        "min",
        "range",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    }
    helper_names = set(getattr(helpers, "__all__", []))
    if not helper_names:
        helper_names = {name for name in dir(helpers) if not name.startswith("_")}
    return builtins | helper_names | set(env.globals)


def _context_keys(context_map: dict[str, dict[str, Any]], template: str) -> set[str]:
    defaults = context_map.get("_defaults", {})
    template_context = context_map.get(template, {})
    return set(defaults) | set(template_context) | BASE_CONTEXT_KEYS


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context",
        type=Path,
        default=DEFAULT_CONTEXT,
        help="Path to template context JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to JSON output file (use - for stdout only).",
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="both",
        choices=["mako", "jinja", "both"],
        help="Template engine to analyze.",
    )
    parser.add_argument(
        "--strict-mako",
        action="store_true",
        help="Fail on missing Mako references (default: warn only).",
    )
    parser.add_argument(
        "--mako-dir",
        type=Path,
        default=DEFAULT_MAKO_DIR,
        help="Path to legacy Mako templates.",
    )
    parser.add_argument(
        "--jinja-dir",
        type=Path,
        default=DEFAULT_JINJA_DIR,
        help="Path to Jinja2 templates.",
    )
    parser.add_argument(
        "--templates",
        type=str,
        default="",
        help="Comma-separated list of template names.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    return parser.parse_args()


def _resolve_templates(args: argparse.Namespace) -> list[str]:
    templates = [item.strip() for item in args.templates.split(",") if item.strip()]
    if templates:
        return templates
    mako_templates = _template_names_mako(args.mako_dir)
    jinja_templates = _template_names_jinja(args.jinja_dir)
    return sorted(mako_templates | jinja_templates)


def _analyze_templates(
    templates: list[str],
    *,
    args: argparse.Namespace,
    context_map: dict[str, dict[str, Any]],
    env: Environment,
    allowed: set[str],
) -> tuple[list[CoverageResult], bool]:
    results: list[CoverageResult] = []
    has_missing = False

    for template in templates:
        context_keys = _context_keys(context_map, template)

        if args.engine in {"mako", "both"}:
            mako_path = args.mako_dir / template
            if mako_path.exists():
                source = mako_path.read_text(encoding="utf-8")
                referenced = _mako_names(source) - allowed
                missing = sorted(referenced - context_keys)
                if missing and args.strict_mako:
                    has_missing = True
                results.append(
                    CoverageResult(
                        template=template,
                        engine="mako",
                        missing=missing,
                        referenced=sorted(referenced),
                    ),
                )

        if args.engine in {"jinja", "both"}:
            jinja_path = _jinja_path(args.jinja_dir, template)
            if jinja_path.exists():
                source = jinja_path.read_text(encoding="utf-8")
                referenced = _jinja_names(env, source) - allowed
                missing = sorted(referenced - context_keys)
                has_missing = has_missing or bool(missing)
                results.append(
                    CoverageResult(
                        template=template,
                        engine="jinja",
                        missing=missing,
                        referenced=sorted(referenced),
                    ),
                )

    return results, has_missing


def _emit_results(
    results: list[CoverageResult],
    *,
    json_output: bool,
    output_path: Path | None,
    ignore_mako_missing: bool,
) -> None:
    if json_output:
        payload = {
            "results": [asdict(item) for item in results],
            "missing": [item.template for item in results if item.missing],
        }
        if output_path is not None and str(output_path) != "-":
            output_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return

    for item in results:
        if item.missing:
            missing_list = ", ".join(item.missing)
            if ignore_mako_missing and item.engine == "mako":
                message = (
                    "MISSING (ignored): "
                    f"{item.template} ({item.engine}) -> {missing_list}"
                )
                print(message)
            else:
                print(f"MISSING: {item.template} ({item.engine}) -> {missing_list}")
        else:
            print(f"OK: {item.template} ({item.engine})")


def main() -> int:
    """Run template context coverage checks."""
    args = _parse_args()

    if not args.context.exists():
        print(f"Context file not found: {args.context}")
        return 2

    context_map = _load_context(args.context)
    env = jinja_renderer.default_environment()
    allowed = _allowed_names(env)
    templates = _resolve_templates(args)

    if not templates:
        print("No templates to analyze.")
        return 0

    results, has_missing = _analyze_templates(
        templates,
        args=args,
        context_map=context_map,
        env=env,
        allowed=allowed,
    )
    _emit_results(
        results,
        json_output=args.json,
        output_path=args.output,
        ignore_mako_missing=not args.strict_mako,
    )
    return 1 if has_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
