"""Render crawler-friendly live-ELO preview images."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

LIVE_ELO_PREVIEW_WIDTH = 1200
LIVE_ELO_PREVIEW_HEIGHT = 630
LIVE_ELO_PREVIEW_MIME_TYPE = "image/png"

_BACKGROUND = (244, 246, 249, 255)
_CARD_BACKGROUND = (255, 255, 255, 255)
_CARD_OUTLINE = (220, 225, 232, 255)
_TEXT = (28, 34, 42, 255)
_MUTED = (102, 112, 124, 255)
_TRACK = (231, 235, 241, 255)
_ACCENT = (45, 94, 199, 255)
_CI_FILL = (82, 136, 255, 72)
_CI_OUTLINE = (82, 136, 255, 180)
_GOOD = (111, 181, 132, 255)
_GOOD_SOFT = (173, 220, 186, 255)
_WARN = (236, 205, 122, 255)
_BAD = (228, 126, 108, 255)
_BAD_SOFT = (239, 183, 176, 255)

_FONT_PATHS = {
    False: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ),
    True: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ),
}

type _Color = tuple[int, int, int, int]
type _Font = ImageFont.FreeTypeFont | ImageFont.ImageFont
type _GaugeSegment = tuple[float, float, _Color]


@dataclass(frozen=True)
class _GaugeSpec:
    top: int
    label: str
    summary: str
    minimum: float
    maximum: float
    value: float
    segments: tuple[_GaugeSegment, ...]
    left_label: str
    right_label: str
    center_label: tuple[float, str] | None = None
    ci_range: tuple[float, float] | None = None


def _float_value(values: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = values.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except TypeError, ValueError:
        return default


def _int_value(values: dict[str, Any], key: str, default: int = 0) -> int:
    value = values.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except TypeError, ValueError:
        return default


def _display_number(value: float, *, decimals: int, signed: bool = False) -> str:
    rounded = round(value, decimals)
    if rounded == 0:
        rounded = 0.0
    if signed:
        return f"{rounded:+.{decimals}f}"
    return f"{rounded:.{decimals}f}"


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def _scaled_x(
    value: float,
    minimum: float,
    maximum: float,
    *,
    left: int,
    width: int,
) -> int:
    if maximum <= minimum:
        return left
    clamped = _clip(value, minimum, maximum)
    ratio = (clamped - minimum) / (maximum - minimum)
    return left + round(ratio * width)


@cache
def _load_font(size: int, *, bold: bool = False) -> _Font:
    for font_path in _FONT_PATHS[bold]:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: _Font,
) -> int:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
    return int(right - left)


def _truncate_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: _Font,
    max_width: int,
) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text

    ellipsis = "..."
    trimmed = text
    while trimmed and _text_width(draw, f"{trimmed}{ellipsis}", font) > max_width:
        trimmed = trimmed[:-1]
    return f"{trimmed.rstrip()}{ellipsis}"


def _status_style(state: str) -> tuple[str, tuple[int, int, int, int]]:
    normalized = state.strip().lower()
    if normalized == "accepted":
        return "Accepted", _GOOD
    if normalized == "rejected":
        return "Rejected", _BAD
    if normalized == "finished":
        return "Finished", _ACCENT
    if normalized == "paused":
        return "Paused", _WARN
    if normalized == "pending":
        return "Pending", _MUTED
    return "Live", _ACCENT


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    fill: tuple[int, int, int, int],
    font: _Font,
) -> None:
    right, top = position
    text_width = _text_width(draw, text, font)
    padding_x = 18
    padding_y = 10
    box_width = text_width + padding_x * 2
    box_height = 24 + padding_y * 2
    left = right - box_width
    bottom = top + box_height
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=box_height // 2,
        fill=fill,
    )
    draw.text(
        (left + padding_x, top + padding_y - 1),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )


def _draw_gauge(draw: ImageDraw.ImageDraw, spec: _GaugeSpec) -> None:
    label_font = _load_font(28, bold=True)
    summary_font = _load_font(24)
    tick_font = _load_font(18)
    gauge_left = 280
    gauge_width = 840
    gauge_top = spec.top + 40
    gauge_height = 38
    gauge_bottom = gauge_top + gauge_height

    draw.text((60, spec.top), spec.label, font=label_font, fill=_TEXT)
    summary_width = _text_width(draw, spec.summary, summary_font)
    draw.text(
        (1120 - summary_width, spec.top + 2),
        spec.summary,
        font=summary_font,
        fill=_TEXT,
    )

    draw.rounded_rectangle(
        (gauge_left, gauge_top, gauge_left + gauge_width, gauge_bottom),
        radius=gauge_height // 2,
        fill=_TRACK,
        outline=_CARD_OUTLINE,
        width=2,
    )

    inner_top = gauge_top + 4
    inner_bottom = gauge_bottom - 4
    for start, end, color in spec.segments:
        start_x = _scaled_x(
            start,
            spec.minimum,
            spec.maximum,
            left=gauge_left,
            width=gauge_width,
        )
        end_x = _scaled_x(
            end,
            spec.minimum,
            spec.maximum,
            left=gauge_left,
            width=gauge_width,
        )
        if end_x <= start_x:
            continue
        draw.rectangle((start_x, inner_top, end_x, inner_bottom), fill=color)

    if spec.ci_range is not None:
        ci_start = _scaled_x(
            spec.ci_range[0],
            spec.minimum,
            spec.maximum,
            left=gauge_left,
            width=gauge_width,
        )
        ci_end = _scaled_x(
            spec.ci_range[1],
            spec.minimum,
            spec.maximum,
            left=gauge_left,
            width=gauge_width,
        )
        draw.rounded_rectangle(
            (ci_start, gauge_top + 8, ci_end, gauge_bottom - 8),
            radius=10,
            fill=_CI_FILL,
            outline=_CI_OUTLINE,
            width=2,
        )

    if spec.center_label is not None:
        center_x = _scaled_x(
            spec.center_label[0],
            spec.minimum,
            spec.maximum,
            left=gauge_left,
            width=gauge_width,
        )
        draw.line(
            (center_x, gauge_top - 4, center_x, gauge_bottom + 4),
            fill=_MUTED,
            width=3,
        )

    value_x = _scaled_x(
        spec.value,
        spec.minimum,
        spec.maximum,
        left=gauge_left,
        width=gauge_width,
    )
    draw.line(
        (value_x, gauge_top - 8, value_x, gauge_bottom + 8),
        fill=_ACCENT,
        width=6,
    )
    draw.ellipse(
        (value_x - 8, gauge_top + 11, value_x + 8, gauge_bottom - 11),
        fill=_ACCENT,
    )

    tick_y = gauge_bottom + 12
    draw.text((gauge_left, tick_y), spec.left_label, font=tick_font, fill=_MUTED)

    if spec.center_label is not None:
        center_width = _text_width(draw, spec.center_label[1], tick_font)
        center_x = _scaled_x(
            spec.center_label[0],
            spec.minimum,
            spec.maximum,
            left=gauge_left,
            width=gauge_width,
        )
        draw.text(
            (center_x - center_width // 2, tick_y),
            spec.center_label[1],
            font=tick_font,
            fill=_MUTED,
        )

    right_width = _text_width(draw, spec.right_label, tick_font)
    draw.text(
        (gauge_left + gauge_width - right_width, tick_y),
        spec.right_label,
        font=tick_font,
        fill=_MUTED,
    )


def render_live_elo_preview_png(
    *,
    run: dict[str, Any],
    page_title: str,
    live_elo_context: dict[str, Any],
) -> bytes:
    """Render the server-owned PNG used by the live-ELO `og:image` tag."""
    image = Image.new(
        "RGBA",
        (LIVE_ELO_PREVIEW_WIDTH, LIVE_ELO_PREVIEW_HEIGHT),
        _BACKGROUND,
    )
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (24, 24, LIVE_ELO_PREVIEW_WIDTH - 24, LIVE_ELO_PREVIEW_HEIGHT - 24),
        radius=30,
        fill=_CARD_BACKGROUND,
        outline=_CARD_OUTLINE,
        width=2,
    )
    draw.rectangle(
        (24, 24, LIVE_ELO_PREVIEW_WIDTH - 24, 148),
        fill=(233, 238, 245, 255),
    )

    title_font = _load_font(40, bold=True)
    subtitle_font = _load_font(24)
    meta_font = _load_font(20)
    pill_font = _load_font(20, bold=True)

    llr = _float_value(live_elo_context, "LLR")
    lower_llr = _float_value(live_elo_context, "a", -2.94)
    upper_llr = _float_value(live_elo_context, "b", 2.94)
    los = _float_value(live_elo_context, "LOS")
    elo = _float_value(live_elo_context, "elo_value")
    ci_lower = _float_value(live_elo_context, "ci_lower")
    ci_upper = _float_value(live_elo_context, "ci_upper")
    games = _int_value(live_elo_context, "games")
    sprt_state = str(live_elo_context.get("sprt_state", "") or "")
    run_status = str(live_elo_context.get("run_status_label", "") or "")
    status_label, status_color = _status_style(sprt_state or run_status)

    title_text = _truncate_text(
        draw,
        f"Live Elo | {page_title}",
        title_font,
        820,
    )
    subtitle_text = _truncate_text(
        draw,
        f"Run {run['_id']} | {games} games | /tests/live_elo/{run['_id']}",
        subtitle_font,
        860,
    )
    meta_text = _truncate_text(
        draw,
        f"{run['args']['new_tag']} vs {run['args']['base_tag']}",
        meta_font,
        860,
    )

    draw.text((60, 56), title_text, font=title_font, fill=_TEXT)
    draw.text((60, 104), subtitle_text, font=subtitle_font, fill=_MUTED)
    draw.text((60, 134), meta_text, font=meta_font, fill=_MUTED)
    _draw_pill(
        draw,
        (LIVE_ELO_PREVIEW_WIDTH - 60, 62),
        status_label,
        status_color,
        pill_font,
    )

    _draw_gauge(
        draw,
        _GaugeSpec(
            top=190,
            label="LLR",
            summary=(
                f"{_display_number(llr, decimals=2, signed=True)} "
                f"[{_display_number(lower_llr, decimals=2, signed=True)}, "
                f"{_display_number(upper_llr, decimals=2, signed=True)}]"
            ),
            minimum=lower_llr,
            maximum=upper_llr,
            value=llr,
            segments=(
                (lower_llr, 0.0, _BAD_SOFT),
                (0.0, upper_llr, _GOOD_SOFT),
            ),
            left_label=_display_number(lower_llr, decimals=2, signed=True),
            right_label=_display_number(upper_llr, decimals=2, signed=True),
            center_label=(0.0, "0"),
        ),
    )

    _draw_gauge(
        draw,
        _GaugeSpec(
            top=326,
            label="LOS",
            summary=f"{_display_number(los, decimals=1)}%",
            minimum=0.0,
            maximum=100.0,
            value=los,
            segments=(
                (0.0, 50.0, _BAD),
                (50.0, 95.0, _WARN),
                (95.0, 100.0, _GOOD),
            ),
            left_label="0%",
            right_label="100%",
            center_label=(50.0, "50%"),
        ),
    )

    _draw_gauge(
        draw,
        _GaugeSpec(
            top=462,
            label="Elo",
            summary=(
                f"{_display_number(elo, decimals=2, signed=True)} "
                f"[{_display_number(ci_lower, decimals=2, signed=True)}, "
                f"{_display_number(ci_upper, decimals=2, signed=True)}] 95%"
            ),
            minimum=-4.0,
            maximum=4.0,
            value=elo,
            segments=(
                (-4.0, 0.0, _BAD_SOFT),
                (0.0, 4.0, _GOOD_SOFT),
            ),
            left_label="-4",
            right_label="+4",
            center_label=(0.0, "0"),
            ci_range=(ci_lower, ci_upper),
        ),
    )

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


__all__ = [
    "LIVE_ELO_PREVIEW_HEIGHT",
    "LIVE_ELO_PREVIEW_MIME_TYPE",
    "LIVE_ELO_PREVIEW_WIDTH",
    "render_live_elo_preview_png",
]
