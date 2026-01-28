"""Mako template rendering helpers for the FastAPI UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from mako.lookup import TemplateLookup

if TYPE_CHECKING:
    from collections.abc import Mapping


REPO_ROOT_DEPTH: Final[int] = 3


def _repo_root() -> Path:
    """Return the repository root directory."""
    # server/fishtest/http/mako.py -> server/ -> repo root
    return Path(__file__).resolve().parents[REPO_ROOT_DEPTH]


def default_template_lookup() -> TemplateLookup:
    """Return a `TemplateLookup` bound to the existing Pyramid templates."""
    templates_dir = _repo_root() / "server" / "fishtest" / "templates"
    return TemplateLookup(
        directories=[str(templates_dir)],
        input_encoding="utf-8",
        output_encoding=None,
        # Pyramid's default via pyramid_mako is `strict_undefined = false`.
        # The existing templates rely on that behavior (missing names become
        # Mako's UNDEFINED sentinel instead of raising NameError).
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
    """Render a Mako template to HTML."""
    template = lookup.get_template(template_name)
    # Mako expects a plain dict for keyword args.
    html = template.render(**dict(context))
    return RenderedTemplate(html=html)
