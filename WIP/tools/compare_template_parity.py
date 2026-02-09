#!/usr/bin/env python3
# ruff: noqa: T201
"""Compare rendered HTML between template engines.

Goal:
    Render the same template with two engines and compare raw and normalized HTML.
    Normalization removes whitespace and tag gaps to focus on semantic parity.

Usage:
    python WIP/tools/compare_template_parity.py --left-engine mako --right-engine jinja
    python WIP/tools/compare_template_parity.py --right-dir server/fishtest/templates_jinja2
    python WIP/tools/compare_template_parity.py --templates tests_view.html.j2,tests.html.j2
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
from datetime import datetime
from difflib import unified_diff
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from mako.lookup import TemplateLookup

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from _stubs import (  # noqa: E402
    with_helpers as _with_helpers,
)
from _stubs import (
    with_request_stub as _with_request_stub,
)
from fishtest.http import jinja as jinja_renderer  # noqa: E402

DEFAULT_MAKO_DIR = REPO_ROOT / "server" / "fishtest" / "templates"
DEFAULT_JINJA_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2"
SKIP_TEMPLATES = {"base.mak"}

os.environ.setdefault(jinja_renderer.TEMPLATES_DIR_ENV, str(DEFAULT_JINJA_DIR))


@dataclass(frozen=True)
class ParityResult:
    template: str
    raw_equal: bool
    normalized_equal: bool
    left_len: int
    right_len: int


_WHITESPACE_RE = re.compile(r"\s+")
_TAG_GAP_RE = re.compile(r">\s+<")


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
    value = _normalize_dom(html)
    value = _TAG_GAP_RE.sub("><", value)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _load_context(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Context file must contain a JSON object.")
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _decode_special(value: Any) -> Any:
    if isinstance(value, dict) and "__datetime__" in value:
        raw = value["__datetime__"]
        if isinstance(raw, str):
            normalized = raw.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
    if isinstance(value, dict):
        return {k: _decode_special(v) for k, v in value.items()}
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


def main() -> int:
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
    args = parser.parse_args()

    left_dir = args.left_dir
    right_dir = args.right_dir
    if args.jinja_dir is not None:
        os.environ[jinja_renderer.TEMPLATES_DIR_ENV] = str(args.jinja_dir)
    if left_dir is None:
        left_dir = (
            DEFAULT_MAKO_DIR
            if args.left_engine == "mako"
            else Path(
                os.environ.get(jinja_renderer.TEMPLATES_DIR_ENV, str(DEFAULT_JINJA_DIR))
            )
        )
    if right_dir is None:
        right_dir = (
            DEFAULT_MAKO_DIR
            if args.right_engine == "mako"
            else Path(
                os.environ.get(jinja_renderer.TEMPLATES_DIR_ENV, str(DEFAULT_JINJA_DIR))
            )
        )

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
    context_map = _load_context(context_path)
    defaults = _decode_special(context_map.get("_defaults", {}))
    if "_defaults" in context_map:
        context_map.pop("_defaults")
    mako_lookup_left = None
    mako_lookup_right = None
    jinja_env_left = None
    jinja_env_right = None
    if args.left_engine == "mako":
        mako_lookup_left = TemplateLookup(
            directories=[str(left_dir)],
            input_encoding="utf-8",
            output_encoding=None,
            strict_undefined=False,
        )
    if args.right_engine == "mako":
        mako_lookup_right = TemplateLookup(
            directories=[str(right_dir)],
            input_encoding="utf-8",
            output_encoding=None,
            strict_undefined=False,
        )
    if args.left_engine == "jinja":
        jinja_env_left = _build_jinja_env(left_dir)
    if args.right_engine == "jinja":
        jinja_env_right = _build_jinja_env(right_dir)

    results: list[ParityResult] = []
    mismatches: list[ParityResult] = []

    for name in templates:
        context = dict(defaults)
        context.update(_decode_special(context_map.get(name, {})))
        context = _with_request_stub(context)
        context = _with_helpers(context)
        try:
            if args.left_engine == "mako":
                left_html = _render_mako(mako_lookup_left, name, context)
            else:
                left_html = _render_jinja(
                    jinja_env_left,
                    _resolve_name(name, "jinja"),
                    context,
                )
        except TemplateNotFound:
            print(f"FAILED: {args.left_engine} template not found: {name}")
            return 2
        except Exception as exc:
            print(f"FAILED: {args.left_engine} render {name}: {exc}")
            return 2

        try:
            if args.right_engine == "mako":
                right_html = _render_mako(mako_lookup_right, name, context)
            else:
                right_html = _render_jinja(
                    jinja_env_right,
                    _resolve_name(name, "jinja"),
                    context,
                )
        except TemplateNotFound:
            print(f"FAILED: {args.right_engine} template not found: {name}")
            return 2
        except Exception as exc:
            print(f"FAILED: {args.right_engine} render {name}: {exc}")
            return 2

        raw_equal = left_html == right_html
        normalized_equal = normalize_html(left_html) == normalize_html(right_html)
        result = ParityResult(
            template=name,
            raw_equal=raw_equal,
            normalized_equal=normalized_equal,
            left_len=len(left_html),
            right_len=len(right_html),
        )
        results.append(result)
        if not normalized_equal:
            mismatches.append(result)
            if args.show_diff and not args.json:
                diff = unified_diff(
                    normalize_html(left_html).splitlines(),
                    normalize_html(right_html).splitlines(),
                    fromfile=f"{args.left_engine}:{name}",
                    tofile=f"{args.right_engine}:{name}",
                    lineterm="",
                )
                print("\n".join(diff))

    if args.json:
        payload = {
            "results": [asdict(item) for item in results],
            "mismatches": [item.template for item in mismatches],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in results:
            status = "OK" if item.normalized_equal else "DIFF"
            print(
                f"{status}: {item.template} (raw_equal={item.raw_equal}, "
                f"normalized_equal={item.normalized_equal})"
            )

    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
