#!/usr/bin/env python3
# ruff: noqa: T201, E402, I001
"""Compare rendered HTML parity between template engines.

Goal:
    Render the same template with two engines and report:
    - raw equality (exact HTML)
    - normalized equality (DOM-normalized, tag gaps removed, whitespace collapsed)
    - minified equality (all whitespace removed)
    - minified similarity score (SequenceMatcher ratio)
    - output lengths for both engines

Usage:
    python WIP/tools/compare_template_parity.py --left-engine mako --right-engine jinja
    python WIP/tools/compare_template_parity.py --right-dir \
        server/fishtest/templates_jinja2
    python WIP/tools/compare_template_parity.py --templates \
        tests_view.html.j2,tests.html.j2
    python WIP/tools/compare_template_parity.py --json --show-diff

Exit status:
    0 if all templates match (normalized)
    1 if any template differs (normalized)
    2 on missing template or render error
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher, unified_diff
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from mako.lookup import TemplateLookup

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = Path(__file__).resolve().parent
SERVER_ROOT = REPO_ROOT / "server"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from WIP.tools._stubs import (
    with_helpers as _with_helpers,
    with_request_stub as _with_request_stub,
)
from fishtest import util
from fishtest.http import jinja as jinja_renderer
from fishtest.http import template_helpers as helpers
from fishtest.util import plural

_FIXED_NOW = datetime(2026, 2, 11, tzinfo=UTC)


def _format_time_ago_fixed(date: datetime) -> str:
    if date == datetime.min.replace(tzinfo=UTC):
        return "Never"
    elapsed_time = _FIXED_NOW - date
    time_values = (
        elapsed_time.days,
        elapsed_time.seconds // 3600,
        elapsed_time.seconds // 60,
    )
    time_units = "day", "hour", "minute"
    for value, unit in zip(time_values, time_units, strict=False):
        if value >= 1:
            unit_label = plural(value, unit)
            return f"{value:d} {unit_label} ago"
    return "seconds ago"


def _freeze_time_helpers() -> None:
    formatter: Any = _format_time_ago_fixed
    util.format_time_ago = formatter  # type: ignore[assignment]
    helpers.format_time_ago = formatter  # type: ignore[assignment]


DEFAULT_MAKO_DIR = REPO_ROOT / "server" / "fishtest" / "templates"
DEFAULT_JINJA_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2"
SKIP_TEMPLATES = {"base.mak"}

ContextMap = dict[str, dict[str, Any]]

os.environ.setdefault(jinja_renderer.TEMPLATES_DIR_ENV, str(DEFAULT_JINJA_DIR))


@dataclass(frozen=True)
class ParityResult:
    """Result of comparing one template across engines."""

    template: str
    raw_equal: bool
    normalized_equal: bool
    minified_equal: bool
    minified_score: float
    left_len: int
    right_len: int


@dataclass(frozen=True)
class ParityConfig:
    """Configuration bundle for parity comparison."""

    left_engine: str
    right_engine: str
    context_map: ContextMap
    defaults: dict[str, Any]
    mako_lookup_left: TemplateLookup | None
    mako_lookup_right: TemplateLookup | None
    jinja_env_left: Environment | None
    jinja_env_right: Environment | None
    show_diff: bool
    json_output: bool


_WHITESPACE_RE = re.compile(r"\s+")
_TAG_GAP_RE = re.compile(r">\s+<")
_TITLE_ASSIGN_RE = re.compile(
    r"document\.title\s*=\s*([\"'])(.*?)\1\s*;?",
    re.DOTALL,
)
_TITLE_TAG_RE = re.compile(r"<title>.*?</title>", re.DOTALL)
_DATA_OPTIONS_RE = re.compile(r"data-options=(\".*?\"|'.*?')", re.DOTALL)
_HEAD_RE = re.compile(r"<head>.*?</head>", re.DOTALL)
_HEAD_LINK_RE = re.compile(r"<link\b[^>]*>", re.DOTALL)
_HEAD_SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc=[^>]*>\s*</script>", re.DOTALL)
_EMPTY_PLAIN_SCRIPT_RE = re.compile(r"<script>\s*</script>", re.DOTALL)


class _DomNormalizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ordered = sorted(attrs, key=lambda item: item[0])
        rendered = " ".join(
            f'{name}="{html.escape(value or "", quote=True)}"'
            for name, value in ordered
        )
        suffix = f" {rendered}" if rendered else ""
        self._parts.append(f"<{tag}{suffix}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ordered = sorted(attrs, key=lambda item: item[0])
        rendered = " ".join(
            f'{name}="{html.escape(value or "", quote=True)}"'
            for name, value in ordered
        )
        suffix = f" {rendered}" if rendered else ""
        self._parts.append(f"<{tag}{suffix} />")

    def handle_endtag(self, tag: str) -> None:
        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_comment(self, data: str) -> None:
        self._parts.append(f"<!--{data}-->")

    def handle_entityref(self, name: str) -> None:
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._parts.append(f"&#{name};")

    def normalized(self) -> str:
        return "".join(self._parts)


def _normalize_dom(html_text: str) -> str:
    parser = _DomNormalizer()
    parser.feed(html_text)
    return parser.normalized()


def normalize_html(html: str) -> str:
    """Normalize HTML for parity checks."""
    value = _normalize_title(html)
    value = _normalize_data_options(value)
    value = _normalize_head_assets(value)
    value = _normalize_dom(value)
    value = _TAG_GAP_RE.sub("><", value)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _normalize_title(html_text: str) -> str:
    match = _TITLE_ASSIGN_RE.search(html_text)
    if not match:
        return html_text
    title_text = match.group(2)
    title_text = title_text.replace('\\"', '"').replace("\\'", "'")
    normalized = _TITLE_TAG_RE.sub(
        f"<title>{title_text}</title>",
        html_text,
        count=1,
    )
    normalized = _TITLE_ASSIGN_RE.sub("", normalized)
    return _EMPTY_PLAIN_SCRIPT_RE.sub("", normalized)


def _normalize_data_options(html_text: str) -> str:
    def _normalize_match(match: re.Match[str]) -> str:
        raw = match.group(1)
        raw_value = raw[1:-1]
        try:
            decoded = html.unescape(raw_value)
            payload = json.loads(decoded)
        except json.JSONDecodeError, TypeError:
            return match.group(0)
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        escaped = html.escape(normalized, quote=True)
        return f'data-options="{escaped}"'

    return _DATA_OPTIONS_RE.sub(_normalize_match, html_text)


def _normalize_head_assets(html_text: str) -> str:
    match = _HEAD_RE.search(html_text)
    if not match:
        return html_text
    head_html = match.group(0)
    head_html = _HEAD_LINK_RE.sub("", head_html)
    head_html = _HEAD_SCRIPT_SRC_RE.sub("", head_html)
    return html_text[: match.start()] + head_html + html_text[match.end() :]


def minify_html(html_text: str) -> str:
    """Remove all whitespace to create a minified comparison payload."""
    return _WHITESPACE_RE.sub("", html_text)


def _load_context(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        message = "Context file must contain a JSON object."
        raise TypeError(message)
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _decode_special(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        raw = mapping.get("__datetime__")
        if isinstance(raw, str):
            normalized = raw.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        return {k: _decode_special(v) for k, v in mapping.items()}
    if isinstance(value, list):
        return [_decode_special(item) for item in value]
    return value


def _logical_name(name: str) -> str:
    if name.endswith(".html.j2"):
        return name[: -len(".html.j2")] + ".mak"
    return name


def _resolve_name(name: str, engine: str) -> str:
    if engine == "jinja" and name.endswith(".mak"):
        return name[: -len(".mak")] + ".html.j2"
    return name


def _template_names(path: Path, engine: str) -> set[str]:
    if engine == "mako":
        return {item.name for item in path.glob("*.mak")}
    return {_logical_name(item.name) for item in path.glob("*.html.j2")}


def _collect_templates(
    *,
    left_dir: Path,
    right_dir: Path,
    left_engine: str,
    right_engine: str,
    names: list[str] | None,
) -> list[str]:
    if names:
        return names

    if not left_dir.exists() or not right_dir.exists():
        return []

    left_names = _template_names(left_dir, left_engine)
    right_names = _template_names(right_dir, right_engine)
    return sorted(
        name for name in left_names & right_names if name not in SKIP_TEMPLATES
    )


def _render_mako(lookup: TemplateLookup, name: str, context: dict[str, Any]) -> str:
    template = lookup.get_template(name)
    return template.render(**context)


def _render_jinja(env: Environment, name: str, context: dict[str, Any]) -> str:
    template = env.get_template(name)
    return template.render(**context)


def _build_jinja_env(template_dir: Path) -> Environment:
    env = jinja_renderer.default_environment()
    env.loader = FileSystemLoader(str(template_dir))
    return env


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--left-engine",
        type=str,
        default="mako",
        choices=["mako", "jinja"],
        help="Left-side engine (mako or jinja).",
    )
    parser.add_argument(
        "--right-engine",
        type=str,
        default="jinja",
        choices=["mako", "jinja"],
        help="Right-side engine (mako or jinja).",
    )
    parser.add_argument(
        "--jinja-dir",
        type=Path,
        default=None,
        help="Override the Jinja2 templates directory.",
    )
    parser.add_argument("--left-dir", type=Path, default=None)
    parser.add_argument("--right-dir", type=Path, default=None)
    parser.add_argument(
        "--templates",
        type=str,
        default="",
        help="Comma-separated list of template names.",
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=None,
        help="Path to JSON file mapping template name to context dict.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument("--show-diff", action="store_true")
    return parser.parse_args()


def _jinja_dir_from_env() -> Path:
    return Path(
        os.environ.get(jinja_renderer.TEMPLATES_DIR_ENV, str(DEFAULT_JINJA_DIR)),
    )


def _resolve_dir(engine: str, override: Path | None) -> Path:
    if override is not None:
        return override
    if engine == "mako":
        return DEFAULT_MAKO_DIR
    return _jinja_dir_from_env()


def _normalize_defaults(defaults: object) -> dict[str, Any]:
    if isinstance(defaults, dict):
        return cast("dict[str, Any]", defaults).copy()
    return {}


def _load_context_bundle(path: Path | None) -> tuple[ContextMap, dict[str, Any]]:
    context_map = _load_context(path)
    defaults = _normalize_defaults(_decode_special(context_map.pop("_defaults", {})))
    return context_map, defaults


def _render_engine(
    *,
    engine: str,
    name: str,
    context: dict[str, Any],
    mako_lookup: TemplateLookup | None,
    jinja_env: Environment | None,
) -> str:
    if engine == "mako":
        if mako_lookup is None:
            message = "Mako lookup is required for Mako rendering."
            raise RuntimeError(message)
        return _render_mako(mako_lookup, name, context)
    if jinja_env is None:
        message = "Jinja environment is required for Jinja rendering."
        raise RuntimeError(message)
    return _render_jinja(jinja_env, _resolve_name(name, "jinja"), context)


def _compare_templates(
    *,
    templates: list[str],
    config: ParityConfig,
) -> tuple[list[ParityResult], list[ParityResult], int]:
    results: list[ParityResult] = []
    mismatches: list[ParityResult] = []

    for name in templates:
        context = dict(config.defaults)
        decoded = _decode_special(config.context_map.get(name, {}))
        if isinstance(decoded, dict):
            context.update(decoded)
        context = _with_request_stub(context)
        context = _with_helpers(context)
        try:
            left_html = _render_engine(
                engine=config.left_engine,
                name=name,
                context=context,
                mako_lookup=config.mako_lookup_left,
                jinja_env=config.jinja_env_left,
            )
        except TemplateNotFound:
            print(f"FAILED: {config.left_engine} template not found: {name}")
            return results, mismatches, 2
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: {config.left_engine} render {name}: {exc}")
            return results, mismatches, 2

        try:
            right_html = _render_engine(
                engine=config.right_engine,
                name=name,
                context=context,
                mako_lookup=config.mako_lookup_right,
                jinja_env=config.jinja_env_right,
            )
        except TemplateNotFound:
            print(f"FAILED: {config.right_engine} template not found: {name}")
            return results, mismatches, 2
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: {config.right_engine} render {name}: {exc}")
            return results, mismatches, 2

        raw_equal = left_html == right_html
        normalized_left = normalize_html(left_html)
        normalized_right = normalize_html(right_html)
        normalized_equal = normalized_left == normalized_right
        minified_left = minify_html(left_html)
        minified_right = minify_html(right_html)
        minified_equal = minified_left == minified_right
        minified_score = SequenceMatcher(None, minified_left, minified_right).ratio()
        result = ParityResult(
            template=name,
            raw_equal=raw_equal,
            normalized_equal=normalized_equal,
            minified_equal=minified_equal,
            minified_score=minified_score,
            left_len=len(left_html),
            right_len=len(right_html),
        )
        results.append(result)
        if not normalized_equal:
            mismatches.append(result)
            if config.show_diff and not config.json_output:
                diff = unified_diff(
                    normalized_left.splitlines(),
                    normalized_right.splitlines(),
                    fromfile=f"{config.left_engine}:{name}",
                    tofile=f"{config.right_engine}:{name}",
                    lineterm="",
                )
                print("\n".join(diff))

    return results, mismatches, 0


def _emit_results(
    results: list[ParityResult],
    mismatches: list[ParityResult],
    *,
    json_output: bool,
) -> None:
    if json_output:
        payload = {
            "results": [asdict(item) for item in results],
            "mismatches": [item.template for item in mismatches],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    for item in results:
        status = "OK" if item.normalized_equal else "DIFF"
        print(
            f"{status}: {item.template} (raw_equal={item.raw_equal}, "
            f"normalized_equal={item.normalized_equal}, "
            f"minified_equal={item.minified_equal}, "
            f"minified_score={item.minified_score:.4f})",
        )


def main() -> int:
    """Compare rendered HTML between template engines."""
    args = _parse_args()

    _freeze_time_helpers()

    if args.jinja_dir is not None:
        os.environ[jinja_renderer.TEMPLATES_DIR_ENV] = str(args.jinja_dir)

    left_dir = _resolve_dir(args.left_engine, args.left_dir)
    right_dir = _resolve_dir(args.right_engine, args.right_dir)

    names = [
        _logical_name(item.strip())
        for item in args.templates.split(",")
        if item.strip()
    ]
    templates = _collect_templates(
        left_dir=left_dir,
        right_dir=right_dir,
        left_engine=args.left_engine,
        right_engine=args.right_engine,
        names=names or None,
    )

    if not templates:
        print("No templates to compare.")
        return 0

    context_path = args.context
    if context_path is None:
        default_context = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
        if default_context.exists():
            context_path = default_context
    context_map, defaults = _load_context_bundle(context_path)

    mako_lookup_left = (
        TemplateLookup(
            directories=[str(left_dir)],
            input_encoding="utf-8",
            output_encoding=None,
            strict_undefined=False,
        )
        if args.left_engine == "mako"
        else None
    )
    mako_lookup_right = (
        TemplateLookup(
            directories=[str(right_dir)],
            input_encoding="utf-8",
            output_encoding=None,
            strict_undefined=False,
        )
        if args.right_engine == "mako"
        else None
    )
    jinja_env_left = _build_jinja_env(left_dir) if args.left_engine == "jinja" else None
    jinja_env_right = (
        _build_jinja_env(right_dir) if args.right_engine == "jinja" else None
    )

    config = ParityConfig(
        left_engine=args.left_engine,
        right_engine=args.right_engine,
        context_map=context_map,
        defaults=defaults,
        mako_lookup_left=mako_lookup_left,
        mako_lookup_right=mako_lookup_right,
        jinja_env_left=jinja_env_left,
        jinja_env_right=jinja_env_right,
        show_diff=args.show_diff,
        json_output=args.json,
    )
    results, mismatches, status = _compare_templates(
        templates=templates,
        config=config,
    )
    if status != 0:
        return status

    _emit_results(results, mismatches, json_output=args.json)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
