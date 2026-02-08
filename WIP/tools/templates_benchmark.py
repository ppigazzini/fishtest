#!/usr/bin/env python3
# ruff: noqa: T201
"""Benchmark template render performance across engines.

Goal:
    Render templates multiple times per engine and report median/p95/p99.

Usage:
    python WIP/tools/templates_benchmark.py
    python WIP/tools/templates_benchmark.py --iterations 100
    python WIP/tools/templates_benchmark.py --templates tests_view.mak,tests.mak
    python WIP/tools/templates_benchmark.py --engine mako

Exit status:
    0 on success
    2 on error
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from fishtest.http import jinja as jinja_renderer  # noqa: E402
from fishtest.http import mako as mako_renderer  # noqa: E402

LEGACY_MAKO_DIR = SERVER_ROOT / "fishtest" / "templates"
from fishtest.http import template_helpers as helpers  # noqa: E402

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
SKIP_TEMPLATES = {"base.mak"}


@dataclass(frozen=True)
class BenchmarkResult:
    template: str
    engine: str
    median_ms: float
    p95_ms: float
    p99_ms: float


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


def _load_context(path: Path) -> dict[str, dict[str, Any]]:
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


def _template_names() -> list[str]:
    mako_names = {item.name for item in LEGACY_MAKO_DIR.glob("*.mak")}
    jinja_names = {item.name for item in jinja_renderer.templates_dir().glob("*.mak")}
    return sorted(
        name for name in (mako_names | jinja_names) if name not in SKIP_TEMPLATES
    )


def _percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(percent * (len(ordered) - 1)))))
    return ordered[index]


def _render_mako(lookup, template: str, context: dict[str, Any]) -> None:
    mako_renderer.render_template(
        lookup=lookup,
        template_name=template,
        context=context,
    )


def _render_jinja(env, template: str, context: dict[str, Any]) -> None:
    template_obj = env.get_template(template)
    template_obj.render(**context)


def _benchmark_template(
    *,
    engine: str,
    template: str,
    context: dict[str, Any],
    iterations: int,
    render,
) -> BenchmarkResult:
    timings: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        render(template, context)
        elapsed = (time.perf_counter() - started) * 1000
        timings.append(elapsed)

    median_ms = statistics.median(timings)
    p95_ms = _percentile(timings, 0.95)
    p99_ms = _percentile(timings, 0.99)
    return BenchmarkResult(
        template=template,
        engine=engine,
        median_ms=median_ms,
        p95_ms=p95_ms,
        p99_ms=p99_ms,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context",
        type=Path,
        default=DEFAULT_CONTEXT,
        help="Path to template context JSON.",
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="both",
        choices=["mako", "jinja", "all"],
        help="Template engine to benchmark.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Number of iterations per template.",
    )
    parser.add_argument(
        "--templates",
        type=str,
        default="",
        help="Comma-separated list of template names.",
    )
    parser.add_argument(
        "--ratio-threshold",
        type=float,
        default=1.5,
        help="Flag templates when one engine is >= threshold slower.",
    )
    args = parser.parse_args()

    if not args.context.exists():
        print(f"Context file not found: {args.context}")
        return 2

    context_map = _load_context(args.context)
    defaults = _decode_special(context_map.get("_defaults", {}))

    templates = [item.strip() for item in args.templates.split(",") if item.strip()]
    if not templates:
        templates = _template_names()

    if not templates:
        print("No templates to benchmark.")
        return 0

    results: list[BenchmarkResult] = []

    render_mako = None
    render_jinja = None
    if args.engine in {"mako", "all"}:
        mako_lookup = mako_renderer.default_template_lookup()
        render_mako = partial(_render_mako, mako_lookup)
    if args.engine in {"jinja", "all"}:
        jinja_env = jinja_renderer.default_environment()
        render_jinja = partial(_render_jinja, jinja_env)

    for template in templates:
        context = dict(defaults)
        context.update(_decode_special(context_map.get(template, {})))
        context = _with_request_stub(context)
        context = _with_helpers(context)

        if args.engine in {"mako", "all"}:
            results.append(
                _benchmark_template(
                    engine="mako",
                    template=template,
                    context=context,
                    iterations=args.iterations,
                    render=render_mako,
                )
            )
        if args.engine in {"jinja", "all"}:
            results.append(
                _benchmark_template(
                    engine="jinja",
                    template=template,
                    context=context,
                    iterations=args.iterations,
                    render=render_jinja,
                )
            )

    grouped: dict[str, dict[str, BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(result.template, {})[result.engine] = result

    for template, engines in grouped.items():
        if "mako" in engines and "jinja" in engines:
            mako_median = engines["mako"].median_ms
            jinja_median = engines["jinja"].median_ms
            if mako_median:
                ratio = jinja_median / mako_median
            else:
                ratio = 0.0
            flag = ""
            if ratio >= args.ratio_threshold:
                flag = "JINJA_SLOW"
            elif ratio > 0 and ratio <= 1 / args.ratio_threshold:
                flag = "MAKO_SLOW"
            print(
                f"{template}: mako {mako_median:.2f}ms, "
                f"jinja {jinja_median:.2f}ms, ratio {ratio:.2f} {flag}".rstrip()
            )
        else:
            for result in engines.values():
                print(
                    f"{template}: {result.engine} {result.median_ms:.2f}ms "
                    f"(p95 {result.p95_ms:.2f}ms, p99 {result.p99_ms:.2f}ms)"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
