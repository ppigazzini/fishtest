#!/usr/bin/env python3
# ruff: noqa: T201
"""Compare response-level parity for templates across engines.

Goal:
    Render templates via the unified response helper and compare
    status, content type, debug metadata, and HTML parity.

Usage:
    python WIP/tools/compare_template_response_parity.py
    python WIP/tools/compare_template_response_parity.py --left-engine mako --right-engine jinja_tmp
    python WIP/tools/compare_template_response_parity.py --templates tests_view.mak

Exit status:
    0 if parity looks good
    1 if mismatches are found
    2 on missing template or render error
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from fishtest.http import jinja as jinja_renderer  # noqa: E402
from fishtest.http import template_helpers as helpers  # noqa: E402
from fishtest.http.template_renderer import (  # noqa: E402
    override_engine,
    render_template_to_response,
)

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
DEFAULT_JINJA_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2"
DEFAULT_JINJA_TMP_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2_tmp"
SKIP_TEMPLATES = {"base.mak"}

os.environ.setdefault(jinja_renderer.TEMPLATES_DIR_ENV, str(DEFAULT_JINJA_DIR))

_WHITESPACE_RE = re.compile(r"\s+")
_TAG_GAP_RE = re.compile(r">\s+<")


@dataclass(frozen=True)
class ResponseParityResult:
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


def normalize_html(html: str) -> str:
    value = _TAG_GAP_RE.sub("><", html)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _get_header(headers, name: str) -> str | None:
    return headers.get(name) or headers.get(name.lower())


def _templates_dir(engine: str, *, jinja_dir: Path, jinja_tmp_dir: Path) -> Path:
    if engine == "jinja":
        return jinja_dir
    if engine == "jinja_tmp":
        return jinja_tmp_dir
    return REPO_ROOT / "server" / "fishtest" / "templates"


def _template_names(path: Path, engine: str) -> set[str]:
    if engine == "mako":
        return {item.name for item in path.glob("*.mak")}
    return {item.name for item in path.glob("*") if item.is_file()}


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
    context.setdefault("csrf_token", "csrf-token")
    context.setdefault("theme", "")
    context.setdefault("current_user", None)
    context.setdefault("pending_users_count", 0)
    context.setdefault("flash", {"error": [], "warning": [], "info": []})
    context.setdefault("page_title", "")
    context.setdefault("urls", {})
    context.setdefault("static_url", lambda asset: f"/static/{asset}")
    context.setdefault("url_for", lambda name, **params: f"/url/{name}")
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


def _render_response(engine: str, name: str, context: dict[str, Any]):
    context_copy = copy.deepcopy(context)
    with override_engine(engine):
        return render_template_to_response(
            template_name=name,
            context=context_copy,
            status_code=200,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--left-engine",
        type=str,
        default="mako",
        choices=["mako", "jinja", "jinja_tmp"],
        help="Left-side engine.",
    )
    parser.add_argument(
        "--right-engine",
        type=str,
        default="jinja_tmp",
        choices=["mako", "jinja", "jinja_tmp"],
        help="Right-side engine.",
    )
    parser.add_argument(
        "--jinja-dir",
        type=Path,
        default=DEFAULT_JINJA_DIR,
        help="Path to templates_jinja2 for the jinja engine.",
    )
    parser.add_argument(
        "--jinja-tmp-dir",
        type=Path,
        default=DEFAULT_JINJA_TMP_DIR,
        help="Path to templates_jinja2_tmp for the jinja_tmp engine.",
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
    args = parser.parse_args()

    os.environ[jinja_renderer.TEMPLATES_DIR_ENV] = str(args.jinja_dir)

    left_dir = _templates_dir(
        args.left_engine,
        jinja_dir=args.jinja_dir,
        jinja_tmp_dir=args.jinja_tmp_dir,
    )
    right_dir = _templates_dir(
        args.right_engine,
        jinja_dir=args.jinja_dir,
        jinja_tmp_dir=args.jinja_tmp_dir,
    )

    names = [item.strip() for item in args.templates.split(",") if item.strip()]
    if names:
        templates = names
    else:
        left_names = _template_names(left_dir, args.left_engine)
        right_names = _template_names(right_dir, args.right_engine)
        templates = sorted(
            name for name in (left_names & right_names) if name not in SKIP_TEMPLATES
        )

    if not templates:
        print("No templates to compare.")
        return 0

    context_map = _load_context(args.context)
    defaults = _decode_special(context_map.get("_defaults", {}))
    if "_defaults" in context_map:
        context_map.pop("_defaults")

    results: list[ResponseParityResult] = []
    mismatches: list[ResponseParityResult] = []

    for name in templates:
        context = dict(defaults)
        context.update(_decode_special(context_map.get(name, {})))
        context = _with_request_stub(context)
        context = _with_helpers(context)

        try:
            left = _render_response(args.left_engine, name, context)
            right = _render_response(args.right_engine, name, context)
        except Exception as exc:
            print(f"FAILED: render {name}: {exc}")
            return 2

        left_body = left.body.decode("utf-8")
        right_body = right.body.decode("utf-8")

        raw_equal = left_body == right_body
        normalized_equal = normalize_html(left_body) == normalize_html(right_body)
        status_equal = left.status_code == right.status_code
        content_type_equal = _get_header(left.headers, "content-type") == _get_header(
            right.headers, "content-type"
        )
        cache_control_equal = _get_header(left.headers, "cache-control") == _get_header(
            right.headers, "cache-control"
        )
        set_cookie_equal = _get_header(left.headers, "set-cookie") == _get_header(
            right.headers, "set-cookie"
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

        if not (
            normalized_equal
            and status_equal
            and content_type_equal
            and cache_control_equal
            and set_cookie_equal
            and left_has_template
            and right_has_template
            and left_has_context
            and right_has_context
        ):
            mismatches.append(result)
            if args.show_diff and not args.json and not normalized_equal:
                diff = unified_diff(
                    normalize_html(left_body).splitlines(),
                    normalize_html(right_body).splitlines(),
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
            status = "OK" if item in results and item not in mismatches else "DIFF"
            print(
                f"{status}: {item.template} (raw_equal={item.raw_equal}, "
                f"normalized_equal={item.normalized_equal})"
            )

    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
