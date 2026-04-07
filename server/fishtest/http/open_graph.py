"""Build server-side Open Graph metadata for full-page UI responses."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict
from urllib.parse import urlsplit, urlunsplit

from fishtest.http.live_elo_preview import (
    LIVE_ELO_PREVIEW_HEIGHT,
    LIVE_ELO_PREVIEW_MIME_TYPE,
    LIVE_ELO_PREVIEW_WIDTH,
)
from fishtest.http.template_helpers import nelo_pentanomial_summary_text

_SITE_NAME = "Stockfish Testing Framework"
_DEFAULT_DESCRIPTION = "Distributed testing framework for the Stockfish chess engine."
_TITLE_SUFFIX = " | Stockfish Testing"
_YELLOW_THEME_COLOR = "#FFFF00"


class OpenGraphImageMetadata(TypedDict):
    """Structured `og:image` metadata rendered by the base template."""

    url: str
    type: str
    width: int
    height: int
    alt: str


class OpenGraphMetadata(TypedDict):
    """Structured page metadata rendered by the base template."""

    site_name: str
    type: str
    title: str
    description: str
    url: str
    image: NotRequired[OpenGraphImageMetadata]


def canonical_page_url(url: str) -> str:
    """Drop query and fragment parts from a page URL."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def default_open_graph(page_url: str) -> OpenGraphMetadata:
    """Return default page metadata for full HTML responses."""
    return {
        "site_name": _SITE_NAME,
        "type": "website",
        "title": _SITE_NAME,
        "description": _DEFAULT_DESCRIPTION,
        "url": canonical_page_url(page_url),
    }


def _normalize_metadata_text(text: str) -> str:
    return " ".join(text.replace(" ± ", " +/- ").split())


def _tests_view_description_lines(
    run: dict[str, Any],
    results_info: dict[str, Any],
) -> list[str]:
    info = results_info.get("info", [])
    if not isinstance(info, list):
        return []

    description_parts: list[str] = []

    for value in info:
        line = str(value)
        normalized_line = _normalize_metadata_text(line)
        if normalized_line:
            description_parts.append(normalized_line)

    nelo_summary = nelo_pentanomial_summary_text(run)
    if nelo_summary:
        description_parts.append(_normalize_metadata_text(nelo_summary))

    return description_parts


def _tests_view_description(
    run: dict[str, Any],
    results_info: dict[str, Any],
) -> str:
    description_lines = _tests_view_description_lines(run, results_info)
    if not description_lines:
        return _DEFAULT_DESCRIPTION

    return "\n".join(description_lines)


def _theme_color_from_results(results_info: dict[str, Any]) -> str | None:
    style = results_info.get("style", "")
    if not isinstance(style, str):
        return None
    if style == "yellow":
        return _YELLOW_THEME_COLOR
    if style.startswith("#"):
        return style
    return None


def _context_float(
    live_elo_context: dict[str, Any],
    key: str,
    default: float = 0.0,
) -> float:
    value = live_elo_context.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except TypeError, ValueError:
        return default


def _context_int(
    live_elo_context: dict[str, Any],
    key: str,
    default: int = 0,
) -> int:
    value = live_elo_context.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except TypeError, ValueError:
        return default


def _format_display_number(value: float, *, decimals: int, signed: bool = False) -> str:
    rounded = round(value, decimals)
    if rounded == 0:
        rounded = 0.0
    if signed:
        return f"{rounded:+.{decimals}f}"
    return f"{rounded:.{decimals}f}"


def _live_elo_preview_path(run_id: object) -> str:
    return f"/tests/live_elo/{run_id}/preview.png"


def _live_elo_description(
    page_title: str,
    live_elo_context: dict[str, Any],
) -> str:
    llr = _context_float(live_elo_context, "LLR")
    lower_llr = _context_float(live_elo_context, "a", -2.94)
    upper_llr = _context_float(live_elo_context, "b", 2.94)
    elo = _context_float(live_elo_context, "elo_value")
    ci_lower = _context_float(live_elo_context, "ci_lower")
    ci_upper = _context_float(live_elo_context, "ci_upper")
    los = _context_float(live_elo_context, "LOS")
    games = _context_int(live_elo_context, "games")
    sprt_state = str(live_elo_context.get("sprt_state", "") or "").strip()
    state_prefix = f"{sprt_state.title()} snapshot. " if sprt_state else ""
    return (
        f"{page_title} live Elo preview. {state_prefix}"
        f"LLR {_format_display_number(llr, decimals=2, signed=True)} in "
        f"[{_format_display_number(lower_llr, decimals=2, signed=True)}, "
        f"{_format_display_number(upper_llr, decimals=2, signed=True)}], "
        f"Elo {_format_display_number(elo, decimals=2, signed=True)} with 95% CI "
        f"[{_format_display_number(ci_lower, decimals=2, signed=True)}, "
        f"{_format_display_number(ci_upper, decimals=2, signed=True)}], "
        f"LOS {_format_display_number(los, decimals=1)}%, {games} games."
    )


def _live_elo_image_alt(
    page_title: str,
    live_elo_context: dict[str, Any],
) -> str:
    llr = _context_float(live_elo_context, "LLR")
    lower_llr = _context_float(live_elo_context, "a", -2.94)
    upper_llr = _context_float(live_elo_context, "b", 2.94)
    elo = _context_float(live_elo_context, "elo_value")
    ci_lower = _context_float(live_elo_context, "ci_lower")
    ci_upper = _context_float(live_elo_context, "ci_upper")
    los = _context_float(live_elo_context, "LOS")
    return (
        f"Three horizontal live-Elo gauges for {page_title}: "
        f"LLR {_format_display_number(llr, decimals=2, signed=True)} in "
        f"[{_format_display_number(lower_llr, decimals=2, signed=True)}, "
        f"{_format_display_number(upper_llr, decimals=2, signed=True)}], "
        f"LOS {_format_display_number(los, decimals=1)}%, "
        f"Elo {_format_display_number(elo, decimals=2, signed=True)} with 95% CI "
        f"[{_format_display_number(ci_lower, decimals=2, signed=True)}, "
        f"{_format_display_number(ci_upper, decimals=2, signed=True)}]."
    )


def build_tests_view_open_graph(
    *,
    host_url: str,
    run: dict[str, Any],
    page_title: str,
    results_info: dict[str, Any],
) -> tuple[OpenGraphMetadata, str | None]:
    """Return Open Graph metadata and theme color for `/tests/view/{id}`."""
    open_graph = default_open_graph(f"{host_url.rstrip('/')}/tests/view/{run['_id']}")
    open_graph["title"] = f"{page_title}{_TITLE_SUFFIX}"
    open_graph["description"] = _tests_view_description(run, results_info)
    return (
        open_graph,
        _theme_color_from_results(results_info),
    )


def build_live_elo_open_graph(
    *,
    host_url: str,
    run: dict[str, Any],
    page_title: str,
    live_elo_context: dict[str, Any],
) -> OpenGraphMetadata:
    """Return Open Graph metadata for `/tests/live_elo/{id}`."""
    run_id = run["_id"]
    page_path = f"/tests/live_elo/{run_id}"
    open_graph = default_open_graph(f"{host_url.rstrip('/')}{page_path}")
    open_graph["title"] = f"Live Elo - {page_title}{_TITLE_SUFFIX}"
    open_graph["description"] = _live_elo_description(page_title, live_elo_context)
    open_graph["image"] = {
        "url": f"{host_url.rstrip('/')}{_live_elo_preview_path(run_id)}",
        "type": LIVE_ELO_PREVIEW_MIME_TYPE,
        "width": LIVE_ELO_PREVIEW_WIDTH,
        "height": LIVE_ELO_PREVIEW_HEIGHT,
        "alt": _live_elo_image_alt(page_title, live_elo_context),
    }
    return open_graph


__all__ = [
    "OpenGraphImageMetadata",
    "OpenGraphMetadata",
    "build_live_elo_open_graph",
    "build_tests_view_open_graph",
    "canonical_page_url",
    "default_open_graph",
]
