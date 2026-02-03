"""New Mako template rendering helpers for the FastAPI UI."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING, Final

from mako.lookup import TemplateLookup

if TYPE_CHECKING:
    from collections.abc import Mapping

REPO_ROOT_DEPTH: Final[int] = 3
TEMPLATES_DIR_ENV: Final[str] = "FISHTEST_MAKO_NEW_TEMPLATES_DIR"


def _repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parents[REPO_ROOT_DEPTH]


def templates_dir() -> Path:
    """Return the new Mako templates directory path."""
    raw = environ.get(TEMPLATES_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    return _repo_root() / "server" / "fishtest" / "templates_mako"


def default_template_lookup() -> TemplateLookup:
    """Return a `TemplateLookup` bound to the new Mako templates."""
    return TemplateLookup(
        directories=[str(templates_dir())],
        input_encoding="utf-8",
        output_encoding=None,
        default_filters=["h"],
        # Keep legacy behavior until parity is proven.
        strict_undefined=False,
    )


@dataclass(frozen=True)
class RenderedTemplate:
    """Represents a rendered HTML payload."""

    html: str


def render_template(
    *,
    lookup: TemplateLookup,
    template_name: str,
    context: Mapping[str, object],
) -> RenderedTemplate:
    """Render a new Mako template to HTML."""
    template = lookup.get_template(template_name)
    html = template.render(**dict(context))
    return RenderedTemplate(html=html)
