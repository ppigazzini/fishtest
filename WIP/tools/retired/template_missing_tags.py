#!/usr/bin/env python3
# ruff: noqa: T201, E402
"""Detect missing or unexpected HTML tags in rendered template output.

Goal:
    Render templates with parity context and scan the HTML output for:
    - missing closing tags (open tags left on the stack)
    - unexpected closing tags (no matching open tag)
    - mismatched closing tags (closing tag that skips open tags)

Usage:
    python WIP/tools/template_missing_tags.py
    python WIP/tools/template_missing_tags.py --engine jinja
    python WIP/tools/template_missing_tags.py --templates workers.html.j2
    python WIP/tools/template_missing_tags.py --json

Exit status:
    0 if no issues are found
    1 if any missing or unexpected tags are found
    2 on error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from WIP.tools import compare_template_parity as parity

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
DEFAULT_MAKO_DIR = parity.DEFAULT_MAKO_DIR
DEFAULT_JINJA_DIR = parity.DEFAULT_JINJA_DIR
SKIP_TEMPLATES = {"base.mak"}

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

SCRIPT_RE = re.compile(r"(<script\b[^>]*>).*?(</script>)", re.IGNORECASE | re.DOTALL)
STYLE_RE = re.compile(r"(<style\b[^>]*>).*?(</style>)", re.IGNORECASE | re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

TAG_RE = re.compile(r"<\s*(/)?\s*([a-zA-Z][a-zA-Z0-9:-]*)\b[^>]*?>")


@dataclass(frozen=True)
class TagIssue:
    """Represents a missing or unexpected HTML tag."""

    tag: str
    line: int
    kind: str


@dataclass(frozen=True)
class TagCheckResult:
    """Tag check results for a single template and engine."""

    template: str
    engine: str
    missing: list[TagIssue]
    unexpected: list[TagIssue]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        type=str,
        default="jinja",
        choices=["mako", "jinja", "both"],
        help="Template engine to analyze.",
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=DEFAULT_CONTEXT,
        help="Path to template context JSON.",
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


def _resolve_templates(args: argparse.Namespace) -> list[str]:
    templates = [item.strip() for item in args.templates.split(",") if item.strip()]
    if templates:
        return [item for item in templates if item not in SKIP_TEMPLATES]
    mako_templates = _template_names_mako(args.mako_dir)
    jinja_templates = _template_names_jinja(args.jinja_dir)
    return sorted(mako_templates | jinja_templates)


def _line_number(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _strip_non_html(source: str) -> str:
    source = HTML_COMMENT_RE.sub("", source)
    source = SCRIPT_RE.sub(r"\1\2", source)
    return STYLE_RE.sub(r"\1\2", source)


def _is_self_closing(match_text: str) -> bool:
    return match_text.rstrip().endswith("/>")


def _check_tags(source: str) -> tuple[list[TagIssue], list[TagIssue]]:
    missing: list[TagIssue] = []
    unexpected: list[TagIssue] = []
    stack: list[tuple[str, int]] = []

    for match in TAG_RE.finditer(source):
        is_close = bool(match.group(1))
        tag = match.group(2).lower()
        if tag in VOID_TAGS:
            continue
        if _is_self_closing(match.group(0)):
            continue
        line = _line_number(source, match.start())

        if not is_close:
            stack.append((tag, line))
            continue

        if not stack:
            unexpected.append(TagIssue(tag=tag, line=line, kind="unexpected"))
            continue

        open_tags = [item[0] for item in stack]
        if tag not in open_tags:
            unexpected.append(TagIssue(tag=tag, line=line, kind="unexpected"))
            continue

        while stack and stack[-1][0] != tag:
            missing_tag, missing_line = stack.pop()
            missing.append(TagIssue(tag=missing_tag, line=missing_line, kind="missing"))
        if stack:
            stack.pop()

    while stack:
        missing_tag, missing_line = stack.pop()
        missing.append(TagIssue(tag=missing_tag, line=missing_line, kind="missing"))

    return missing, unexpected


def _build_context(
    name: str,
    context_map: dict[str, dict[str, Any]],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    context = dict(defaults)
    decoded = parity._decode_special(context_map.get(name, {}))  # noqa: SLF001
    if isinstance(decoded, dict):
        context.update(decoded)
    context = parity._with_request_stub(context)  # noqa: SLF001
    return parity._with_helpers(context)  # noqa: SLF001


def _render_html(
    *,
    engine: str,
    name: str,
    context: dict[str, Any],
    mako_lookup: parity.TemplateLookup | None,
    jinja_env: parity.Environment | None,
) -> str:
    return parity._render_engine(  # noqa: SLF001
        engine=engine,
        name=name,
        context=context,
        mako_lookup=mako_lookup,
        jinja_env=jinja_env,
    )


def _analyze_rendered(
    html_text: str,
    *,
    template: str,
    engine: str,
) -> TagCheckResult:
    html_text = _strip_non_html(html_text)
    missing, unexpected = _check_tags(html_text)
    return TagCheckResult(
        template=template,
        engine=engine,
        missing=missing,
        unexpected=unexpected,
    )


def _emit_results(results: list[TagCheckResult], *, json_output: bool) -> None:
    if json_output:
        payload = {
            "results": [
                {
                    "template": item.template,
                    "engine": item.engine,
                    "missing": [asdict(issue) for issue in item.missing],
                    "unexpected": [asdict(issue) for issue in item.unexpected],
                }
                for item in results
            ],
            "missing": [
                item.template for item in results if item.missing or item.unexpected
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    for item in results:
        if item.missing or item.unexpected:
            print(f"DIFF: {item.template} ({item.engine})")
            for issue in item.unexpected:
                print(
                    f"  unexpected </{issue.tag}> at line {issue.line}",
                )
            for issue in item.missing:
                print(
                    f"  missing </{issue.tag}> opened at line {issue.line}",
                )
        else:
            print(f"OK: {item.template} ({item.engine})")


def main() -> int:
    """Run missing tag checks over rendered template output."""
    args = _parse_args()
    templates = _resolve_templates(args)

    if not templates:
        print("No templates to analyze.")
        return 0

    if not args.context.exists():
        print(f"Context file not found: {args.context}")
        return 2

    context_map, defaults = parity._load_context_bundle(args.context)  # noqa: SLF001
    mako_lookup = parity.TemplateLookup(
        directories=[str(args.mako_dir)],
        input_encoding="utf-8",
        output_encoding=None,
        strict_undefined=False,
    )
    jinja_env = parity._build_jinja_env(args.jinja_dir)  # noqa: SLF001

    results: list[TagCheckResult] = []
    for template in templates:
        context = _build_context(template, context_map, defaults)
        mako_path = args.mako_dir / template
        if args.engine in {"mako", "both"} and mako_path.exists():
            try:
                html_text = _render_html(
                    engine="mako",
                    name=template,
                    context=context,
                    mako_lookup=mako_lookup,
                    jinja_env=None,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED: mako render {template}: {exc}")
                return 2
            results.append(
                _analyze_rendered(html_text, template=template, engine="mako"),
            )
        jinja_path = _jinja_path(args.jinja_dir, template)
        if args.engine in {"jinja", "both"} and jinja_path.exists():
            try:
                html_text = _render_html(
                    engine="jinja",
                    name=template,
                    context=context,
                    mako_lookup=None,
                    jinja_env=jinja_env,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED: jinja render {template}: {exc}")
                return 2
            results.append(
                _analyze_rendered(html_text, template=template, engine="jinja"),
            )

    _emit_results(results, json_output=args.json)
    has_issues = any(item.missing or item.unexpected for item in results)
    return 1 if has_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
