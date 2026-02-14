#!/usr/bin/env python3
# ruff: noqa: T201
"""Benchmark template render performance across engines.

Goal:
    Render templates multiple times per engine and report median/p95/p99.

Usage:
    python WIP/tools/templates_benchmark.py
    python WIP/tools/templates_benchmark.py --iterations 100
    python WIP/tools/templates_benchmark.py --templates tests_view.html.j2,tests.html.j2
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
from typing import TYPE_CHECKING, Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from mako.lookup import TemplateLookup  # noqa: E402

from fishtest.http import jinja as jinja_renderer  # noqa: E402
from fishtest.http import template_helpers as helpers  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from jinja2 import Environment

LEGACY_MAKO_DIR = SERVER_ROOT / "fishtest" / "templates"

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
SKIP_TEMPLATES = {"base.mak"}


@dataclass(frozen=True)
class BenchmarkResult:
    """Aggregated timing stats for one template and engine."""

    template: str
    engine: str
    median_ms: float
    p95_ms: float
    p99_ms: float


@dataclass(frozen=True)
class BenchmarkConfig:
    """Configuration bundle for benchmark runs."""

    args: argparse.Namespace
    context_map: dict[str, dict[str, Any]]
    defaults: dict[str, Any]
    render_mako: Callable[[str, dict[str, Any]], None] | None
    render_jinja: Callable[[str, dict[str, Any]], None] | None


class SessionStub:
    """Minimal session stub for template rendering."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Initialize session data payload."""
        self._data = data or {}

    def get_csrf_token(self) -> str:
        """Return a fixed CSRF token for benchmark rendering."""
        return "csrf-token"

    def peek_flash(self, _category: str | None = None) -> bool:
        """Return whether flash entries exist (always false)."""
        return False

    def pop_flash(self, _category: str | None = None) -> list[str]:
        """Return and clear flash entries (always empty)."""
        return []


class UserDbStub:
    """Minimal user DB stub for template rendering."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Initialize user DB data payload."""
        self._data = data or {}

    def get_users(self) -> list[dict[str, Any]]:
        """Return the configured user list."""
        return list(self._data.get("users", []))

    def get_pending(self) -> list[dict[str, Any]]:
        """Return the configured pending user list."""
        return list(self._data.get("pending", []))


class RequestStub:
    """Minimal request stub for template rendering."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Initialize request data payload."""
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
        """Return a static URL for an asset."""
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


def _template_names() -> list[str]:
    mako_names = {item.name for item in LEGACY_MAKO_DIR.glob("*.mak")}
    jinja_names = {
        _logical_name(item.name)
        for item in jinja_renderer.templates_dir().glob("*.html.j2")
    }
    return sorted(
        name for name in (mako_names | jinja_names) if name not in SKIP_TEMPLATES
    )


def _percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(percent * (len(ordered) - 1))))
    return ordered[index]


def _default_mako_lookup() -> TemplateLookup:
    return TemplateLookup(
        directories=[str(LEGACY_MAKO_DIR)],
        input_encoding="utf-8",
        output_encoding=None,
        strict_undefined=False,
    )


def _render_mako(
    lookup: TemplateLookup,
    template: str,
    context: dict[str, Any],
) -> None:
    lookup.get_template(template).render(**context)


def _render_jinja(env: Environment, template: str, context: dict[str, Any]) -> None:
    resolved = _resolve_name(template, "jinja")
    template_obj = env.get_template(resolved)
    template_obj.render(**context)


def _benchmark_template(
    *,
    engine: str,
    template: str,
    context: dict[str, Any],
    iterations: int,
    render: Callable[[str, dict[str, Any]], None],
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


def _parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def _resolve_templates(args: argparse.Namespace) -> list[str]:
    templates = [
        _logical_name(item.strip())
        for item in args.templates.split(",")
        if item.strip()
    ]
    if templates:
        return templates
    return _template_names()


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


def _normalize_defaults(defaults: object) -> dict[str, Any]:
    if isinstance(defaults, dict):
        return cast("dict[str, Any]", defaults).copy()
    return {}


def _build_renderers(
    args: argparse.Namespace,
) -> tuple[
    Callable[[str, dict[str, Any]], None] | None,
    Callable[[str, dict[str, Any]], None] | None,
]:
    render_mako: Callable[[str, dict[str, Any]], None] | None = None
    render_jinja: Callable[[str, dict[str, Any]], None] | None = None
    if args.engine in {"mako", "all"}:
        mako_lookup = _default_mako_lookup()
        render_mako = partial(_render_mako, mako_lookup)
    if args.engine in {"jinja", "all"}:
        jinja_env = jinja_renderer.default_environment()
        render_jinja = partial(_render_jinja, jinja_env)
    return render_mako, render_jinja


def _collect_results(
    templates: list[str],
    *,
    config: BenchmarkConfig,
) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for template in templates:
        context = _build_context(config.defaults, config.context_map, template)
        if config.args.engine in {"mako", "all"} and config.render_mako:
            results.append(
                _benchmark_template(
                    engine="mako",
                    template=template,
                    context=context,
                    iterations=config.args.iterations,
                    render=config.render_mako,
                ),
            )
        if config.args.engine in {"jinja", "all"} and config.render_jinja:
            results.append(
                _benchmark_template(
                    engine="jinja",
                    template=template,
                    context=context,
                    iterations=config.args.iterations,
                    render=config.render_jinja,
                ),
            )
    return results


def _emit_results(
    grouped: dict[str, dict[str, BenchmarkResult]],
    *,
    ratio_threshold: float,
) -> None:
    for template, engines in grouped.items():
        if "mako" in engines and "jinja" in engines:
            mako_median = engines["mako"].median_ms
            jinja_median = engines["jinja"].median_ms
            ratio = jinja_median / mako_median if mako_median else 0.0
            flag = ""
            if ratio >= ratio_threshold:
                flag = "JINJA_SLOW"
            elif ratio > 0 and ratio <= 1 / ratio_threshold:
                flag = "MAKO_SLOW"
            print(
                f"{template}: mako {mako_median:.2f}ms, "
                f"jinja {jinja_median:.2f}ms, ratio {ratio:.2f} {flag}".rstrip(),
            )
        else:
            for result in engines.values():
                print(
                    f"{template}: {result.engine} {result.median_ms:.2f}ms "
                    f"(p95 {result.p95_ms:.2f}ms, p99 {result.p99_ms:.2f}ms)",
                )


def main() -> int:
    """Run render-time benchmarks for Mako and Jinja templates."""
    args = _parse_args()
    if not args.context.exists():
        print(f"Context file not found: {args.context}")
        return 2

    context_map = _load_context(args.context)
    defaults = _normalize_defaults(_decode_special(context_map.get("_defaults", {})))

    templates = _resolve_templates(args)

    if not templates:
        print("No templates to benchmark.")
        return 0

    render_mako, render_jinja = _build_renderers(args)
    config = BenchmarkConfig(
        args=args,
        context_map=context_map,
        defaults=defaults,
        render_mako=render_mako,
        render_jinja=render_jinja,
    )
    results = _collect_results(templates, config=config)

    grouped: dict[str, dict[str, BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(result.template, {})[result.engine] = result

    _emit_results(grouped, ratio_threshold=args.ratio_threshold)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
