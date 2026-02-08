"""Unified template renderer for Jinja2."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Protocol, cast

from fishtest.http import jinja as jinja_renderer
from starlette.responses import HTMLResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

    from starlette.templating import Jinja2Templates


@cache
def _jinja_templates() -> Jinja2Templates:
    return jinja_renderer.default_templates()


class _TemplateDebugResponse(Protocol):
    template: str
    context: dict[str, object]


@dataclass(frozen=True)
class RenderedTemplate:
    """Represents a rendered HTML payload."""

    html: str


def render_template(
    *,
    template_name: str,
    context: Mapping[str, object],
) -> RenderedTemplate:
    """Render a template using the Jinja2 renderer."""
    rendered = jinja_renderer.render_template(
        templates=_jinja_templates(),
        template_name=template_name,
        context=context,
    )
    return RenderedTemplate(html=rendered.html)


def render_template_to_response(
    *,
    template_name: str,
    context: Mapping[str, object],
    status_code: int = 200,
) -> HTMLResponse:
    """Render a template and return an HTMLResponse with debug metadata."""
    rendered = render_template(template_name=template_name, context=context)
    response = HTMLResponse(rendered.html, status_code=status_code)
    # Attach debug-friendly attributes without changing the response body.
    debug_response = cast("_TemplateDebugResponse", response)
    debug_response.template = template_name
    debug_response.context = dict(context)
    return response
