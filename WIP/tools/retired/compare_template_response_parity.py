#!/usr/bin/env python3
# ruff: noqa: T201, E402, I001
"""Compare response-level parity for template rendering across engines.

Goal:
    Render templates via local Jinja2/Mako helpers and report:
    - response status equality
    - content-type, cache-control, and set-cookie header parity
    - template/context debug metadata availability
    - raw HTML equality and normalized HTML equality
    - output lengths for both engines

Usage:
    python WIP/tools/compare_template_response_parity.py
    python WIP/tools/compare_template_response_parity.py --left-engine mako \
        --right-engine jinja
    python WIP/tools/compare_template_response_parity.py --templates tests_view.html.j2

Exit status:
    0 if parity looks good
    1 if mismatches are found
    2 on missing template or render error
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import unified_diff
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from jinja2 import Environment, FileSystemLoader
from mako.lookup import TemplateLookup
from starlette.responses import HTMLResponse

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
from fishtest.http import jinja as jinja_renderer

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
DEFAULT_JINJA_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2"
DEFAULT_MAKO_DIR = REPO_ROOT / "server" / "fishtest" / "templates"
SKIP_TEMPLATES = {"base.mak"}

# Known response-level normalized HTML diffs between legacy Mako and Jinja2.
# These are currently treated as informational by default.
EXPECTED_NORMALIZED_DIFF_TEMPLATES = {
    "actions.mak",
    "contributors.mak",
    "login.mak",
    "machines.mak",
    "nn_upload.mak",
    "nns.mak",
    "notfound.mak",
    "rate_limits.mak",
    "run_table.mak",
    "signup.mak",
    "sprt_calc.mak",
    "tests_finished.mak",
    "tests_live_elo.mak",
    "tests_run.mak",
    "tests_stats.mak",
    "tests_user.mak",
    "tests_view.mak",
    "user.mak",
    "user_management.mak",
    "workers.mak",
}

_WHITESPACE_RE = re.compile(r"\s+")
_TAG_GAP_RE = re.compile(r">\s+<")


@dataclass(frozen=True)
class ResponseParityResult:
    """Response parity results for a single template."""

    template: str
    raw_equal: bool
    normalized_equal: bool
    status_equal: bool
    content_type_equal: bool
    cache_control_equal: bool
    set_cookie_equal: bool
    left_has_template: bool
    right_has_template: bool
    left_has_context: bool
    right_has_context: bool
    left_len: int
    right_len: int


@dataclass(frozen=True)
class ResponseParityConfig:
    """Configuration bundle for response parity checks."""

    args: argparse.Namespace
    context_map: dict[str, dict[str, Any]]
    defaults: dict[str, Any]
    jinja_env: Environment
    mako_lookup: TemplateLookup


class _TemplateDebugResponse(Protocol):
    template: str
    context: dict[str, Any]


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


def normalize_html(html_text: str) -> str:
    """Normalize HTML for parity checks."""
    value = _normalize_dom(html_text)
    value = _TAG_GAP_RE.sub("><", value)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    return headers.get(name) or headers.get(name.lower())


def _templates_dir(engine: str, *, jinja_dir: Path, mako_dir: Path) -> Path:
    if engine == "jinja":
        return jinja_dir
    return mako_dir


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


def _build_jinja_env(template_dir: Path) -> Environment:
    env = jinja_renderer.default_environment()
    env.loader = FileSystemLoader(str(template_dir))
    return env


def _build_mako_lookup(template_dir: Path) -> TemplateLookup:
    return TemplateLookup(
        directories=[str(template_dir)],
        input_encoding="utf-8",
        output_encoding=None,
        strict_undefined=False,
    )


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


def _render_response(
    engine: str,
    name: str,
    context: dict[str, Any],
    *,
    jinja_env: Environment,
    mako_lookup: TemplateLookup,
) -> HTMLResponse:
    context_copy = copy.deepcopy(context)
    resolved = _resolve_name(name, engine)
    if engine == "jinja":
        html = jinja_env.get_template(resolved).render(**context_copy)
    else:
        html = mako_lookup.get_template(resolved).render(**context_copy)
    response = HTMLResponse(html, status_code=200)
    debug_response = cast("_TemplateDebugResponse", response)
    debug_response.template = name
    debug_response.context = context_copy
    return response


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--left-engine",
        type=str,
        default="mako",
        choices=["mako", "jinja"],
        help="Left-side engine.",
    )
    parser.add_argument(
        "--right-engine",
        type=str,
        default="jinja",
        choices=["mako", "jinja"],
        help="Right-side engine.",
    )
    parser.add_argument(
        "--jinja-dir",
        type=Path,
        default=DEFAULT_JINJA_DIR,
        help="Path to templates_jinja2 for the jinja engine.",
    )
    parser.add_argument(
        "--templates",
        type=str,
        default="",
        help="Comma-separated list of template names.",
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=DEFAULT_CONTEXT,
        help="Path to template context JSON.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Show unified diffs for normalized HTML mismatches.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on any normalized HTML mismatch (ignore expected-diff allowlist).",
    )
    return parser.parse_args()


def _resolve_templates(
    *,
    args: argparse.Namespace,
    left_dir: Path,
    right_dir: Path,
) -> list[str]:
    names = [
        _logical_name(item.strip())
        for item in args.templates.split(",")
        if item.strip()
    ]
    if names:
        return names
    left_names = _template_names(left_dir, args.left_engine)
    right_names = _template_names(right_dir, args.right_engine)
    return sorted(
        name for name in (left_names & right_names) if name not in SKIP_TEMPLATES
    )


def _load_context_bundle(
    context_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    context_map = _load_context(context_path)
    defaults = _normalize_defaults(_decode_special(context_map.pop("_defaults", {})))
    return context_map, defaults


def _normalize_defaults(defaults: object) -> dict[str, Any]:
    if isinstance(defaults, dict):
        return cast("dict[str, Any]", defaults).copy()
    return {}


def _build_context(
    defaults: dict[str, Any],
    context_map: dict[str, dict[str, Any]],
    template: str,
) -> dict[str, Any]:
    context = dict(defaults)
    decoded = _decode_special(context_map.get(template, {}))
    if isinstance(decoded, dict):
        context.update(decoded)
    context = _with_request_stub(context)
    return _with_helpers(context)


def _decode_body(body: object) -> str:
    if isinstance(body, (bytes, bytearray)):
        return body.decode("utf-8")
    if isinstance(body, memoryview):
        return body.tobytes().decode("utf-8")
    return str(body)


def _render_pair(
    *,
    template: str,
    context: dict[str, Any],
    config: ResponseParityConfig,
) -> tuple[HTMLResponse, HTMLResponse] | None:
    try:
        left = _render_response(
            config.args.left_engine,
            template,
            context,
            jinja_env=config.jinja_env,
            mako_lookup=config.mako_lookup,
        )
        right = _render_response(
            config.args.right_engine,
            template,
            context,
            jinja_env=config.jinja_env,
            mako_lookup=config.mako_lookup,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: render {template}: {exc}")
        return None
    return left, right


def _collect_results(
    *,
    templates: list[str],
    config: ResponseParityConfig,
) -> tuple[
    list[ResponseParityResult],
    list[ResponseParityResult],
    list[ResponseParityResult],
    int,
]:
    results: list[ResponseParityResult] = []
    mismatches: list[ResponseParityResult] = []
    expected_diffs: list[ResponseParityResult] = []

    for name in templates:
        context = _build_context(config.defaults, config.context_map, name)
        rendered = _render_pair(
            template=name,
            context=context,
            config=config,
        )
        if rendered is None:
            return results, mismatches, expected_diffs, 2

        left, right = rendered
        left_body = _decode_body(left.body)
        right_body = _decode_body(right.body)

        raw_equal = left_body == right_body
        normalized_equal = normalize_html(left_body) == normalize_html(right_body)
        status_equal = left.status_code == right.status_code
        content_type_equal = _get_header(left.headers, "content-type") == _get_header(
            right.headers,
            "content-type",
        )
        cache_control_equal = _get_header(left.headers, "cache-control") == _get_header(
            right.headers,
            "cache-control",
        )
        set_cookie_equal = _get_header(left.headers, "set-cookie") == _get_header(
            right.headers,
            "set-cookie",
        )
        left_has_template = hasattr(left, "template")
        right_has_template = hasattr(right, "template")
        left_has_context = hasattr(left, "context")
        right_has_context = hasattr(right, "context")

        result = ResponseParityResult(
            template=name,
            raw_equal=raw_equal,
            normalized_equal=normalized_equal,
            status_equal=status_equal,
            content_type_equal=content_type_equal,
            cache_control_equal=cache_control_equal,
            set_cookie_equal=set_cookie_equal,
            left_has_template=left_has_template,
            right_has_template=right_has_template,
            left_has_context=left_has_context,
            right_has_context=right_has_context,
            left_len=len(left_body),
            right_len=len(right_body),
        )
        results.append(result)

        expected_normalized_diff = (
            not normalized_equal
            and not config.args.strict
            and name in EXPECTED_NORMALIZED_DIFF_TEMPLATES
        )
        if expected_normalized_diff:
            expected_diffs.append(result)

        structural_equal = (
            status_equal
            and content_type_equal
            and cache_control_equal
            and set_cookie_equal
            and left_has_template
            and right_has_template
            and left_has_context
            and right_has_context
        )
        normalized_mismatch = not normalized_equal and not expected_normalized_diff

        if normalized_mismatch or not structural_equal:
            mismatches.append(result)
            if config.args.show_diff and not config.args.json and not normalized_equal:
                diff = unified_diff(
                    normalize_html(left_body).splitlines(),
                    normalize_html(right_body).splitlines(),
                    fromfile=f"{config.args.left_engine}:{name}",
                    tofile=f"{config.args.right_engine}:{name}",
                    lineterm="",
                )
                print("\n".join(diff))

    return results, mismatches, expected_diffs, 0


def _emit_results(
    results: list[ResponseParityResult],
    mismatches: list[ResponseParityResult],
    expected_diffs: list[ResponseParityResult],
    *,
    json_output: bool,
) -> None:
    if json_output:
        payload = {
            "results": [asdict(item) for item in results],
            "mismatches": [item.template for item in mismatches],
            "expected_diffs": [item.template for item in expected_diffs],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    expected_templates = {item.template for item in expected_diffs}
    for item in results:
        if item in mismatches:
            status = "DIFF"
        elif item.template in expected_templates:
            status = "EXPECTED_DIFF"
        else:
            status = "OK"
        print(
            f"{status}: {item.template} (raw_equal={item.raw_equal}, "
            f"normalized_equal={item.normalized_equal})",
        )


def main() -> int:
    """Compare response parity between template engines."""
    args = _parse_args()

    left_dir = _templates_dir(
        args.left_engine,
        jinja_dir=args.jinja_dir,
        mako_dir=DEFAULT_MAKO_DIR,
    )
    right_dir = _templates_dir(
        args.right_engine,
        jinja_dir=args.jinja_dir,
        mako_dir=DEFAULT_MAKO_DIR,
    )

    templates = _resolve_templates(args=args, left_dir=left_dir, right_dir=right_dir)
    if not templates:
        print("No templates to compare.")
        return 0

    context_map, defaults = _load_context_bundle(args.context)
    config = ResponseParityConfig(
        args=args,
        context_map=context_map,
        defaults=defaults,
        jinja_env=_build_jinja_env(args.jinja_dir),
        mako_lookup=_build_mako_lookup(DEFAULT_MAKO_DIR),
    )

    results, mismatches, expected_diffs, status = _collect_results(
        templates=templates,
        config=config,
    )
    if status != 0:
        return status

    _emit_results(
        results,
        mismatches,
        expected_diffs,
        json_output=args.json,
    )
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
