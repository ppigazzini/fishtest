#!/usr/bin/env python3
# ruff: noqa: T201
"""Compare rendered HTML between template engines.

Goal:
    Render the same template with two engines and compare raw and normalized HTML.
    Normalization removes whitespace and tag gaps to focus on semantic parity.

Usage:
    python WIP/tools/compare_template_parity.py --left-engine mako --right-engine jinja
    python WIP/tools/compare_template_parity.py --templates tests_view.mak,tests.mak
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

from fishtest.http import jinja as jinja_renderer  # noqa: E402
from fishtest.http import template_helpers as helpers  # noqa: E402

DEFAULT_MAKO_DIR = REPO_ROOT / "server" / "fishtest" / "templates"
DEFAULT_JINJA_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2"
SKIP_TEMPLATES = {"base.mak"}


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


class SessionStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    def get_csrf_token(self) -> str:
        return "csrf-token"

    def peek_flash(self, _category: str | None = None) -> bool:
        return False

    def pop_flash(self, _category: str | None = None) -> list[str]:
        return []


class UserDbStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    def get_users(self) -> list[dict[str, Any]]:
        return list(self._data.get("users", []))

    def get_pending(self) -> list[dict[str, Any]]:
        return list(self._data.get("pending", []))


class RequestStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.url = data.get("url", "/")
        self.GET = data.get("GET", {})
        self.headers = data.get("headers", {})
        self.cookies = data.get("cookies", {})
        self.query_params = data.get("query_params", self.GET)
        self.authenticated_userid = data.get("authenticated_userid")
        self.session = SessionStub(data.get("session"))
        self.userdb = UserDbStub(data.get("userdb"))

    def static_url(self, asset: str) -> str:
        return f"/static/{asset}"


def _with_request_stub(context: dict[str, Any]) -> dict[str, Any]:
    request_data = context.get("request")
    if isinstance(request_data, dict) or request_data is None:
        context["request"] = RequestStub(request_data)
    return context


def _with_helpers(context: dict[str, Any]) -> dict[str, Any]:
    context.setdefault("display_residual", helpers.display_residual)
    context.setdefault("format_bounds", helpers.format_bounds)
    context.setdefault("format_date", helpers.format_date)
    context.setdefault("format_group", helpers.format_group)
    context.setdefault("format_results", helpers.format_results)
    context.setdefault("format_time_ago", helpers.format_time_ago)
    context.setdefault("get_cookie", helpers.get_cookie)
    context.setdefault("is_active_sprt_ltc", helpers.is_active_sprt_ltc)
    context.setdefault("tests_repo", helpers.tests_repo)
    context.setdefault("worker_name", helpers.worker_name)
    return context


def _template_names(path: Path, engine: str) -> set[str]:
    if engine == "mako":
        return {item.name for item in path.glob("*.mak")}
    return {
        item.name
        for item in path.glob("*")
        if item.is_file() and not item.name.startswith(".")
    }


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
    if left_dir is None:
        left_dir = DEFAULT_MAKO_DIR if args.left_engine == "mako" else DEFAULT_JINJA_DIR
    if right_dir is None:
        right_dir = (
            DEFAULT_MAKO_DIR if args.right_engine == "mako" else DEFAULT_JINJA_DIR
        )

    names = [item.strip() for item in args.templates.split(",") if item.strip()]
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

    context_map = _load_context(args.context)
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
                left_html = _render_jinja(jinja_env_left, name, context)
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
                right_html = _render_jinja(jinja_env_right, name, context)
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
