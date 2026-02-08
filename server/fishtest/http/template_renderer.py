"""Unified template renderer for Mako and Jinja2."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from fishtest.http import jinja as jinja_renderer
from fishtest.http import mako as mako_renderer
from starlette.responses import HTMLResponse

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from mako.lookup import TemplateLookup
    from starlette.templating import Jinja2Templates

TemplateEngine = Literal["mako", "jinja"]

DEFAULT_ENGINE: Final = "jinja"


@cache
def _mako_lookup() -> TemplateLookup:
    return mako_renderer.default_template_lookup()


@cache
def _jinja_templates() -> Jinja2Templates:
    return jinja_renderer.default_templates()


@dataclass(frozen=True)
class RenderedTemplate:
    """Represents a rendered HTML payload."""

    html: str
    engine: TemplateEngine


@dataclass
class _EngineState:
    engine: TemplateEngine


_STATE = _EngineState(engine=DEFAULT_ENGINE)


class _TemplateDebugResponse(Protocol):
    template: str
    context: dict[str, object]


@contextmanager
def override_engine(engine: TemplateEngine) -> Iterator[None]:
    """Temporarily override the template engine (test helper)."""
    previous = _STATE.engine
    _STATE.engine = engine
    try:
        yield
    finally:
        _STATE.engine = previous


def set_template_engine(engine: TemplateEngine) -> None:
    """Set the active template engine at runtime."""
    _STATE.engine = engine


def get_template_engine() -> TemplateEngine:
    """Return the active template engine."""
    return _STATE.engine


def render_template(
    *,
    template_name: str,
    context: Mapping[str, object],
) -> RenderedTemplate:
    """Render a template using the configured renderer."""
    engine = get_template_engine()
    if engine == "jinja":
        rendered = jinja_renderer.render_template(
            templates=_jinja_templates(),
            template_name=template_name,
            context=context,
        )
        return RenderedTemplate(html=rendered.html, engine=engine)

    rendered = mako_renderer.render_template(
        lookup=_mako_lookup(),
        template_name=template_name,
        context=context,
    )
    return RenderedTemplate(html=rendered.html, engine=engine)


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


def render_template_dual(
    *,
    template_name: str,
    context: Mapping[str, object],
) -> tuple[str, str]:
    """Render the template with both engines for parity checks."""
    mako_html = mako_renderer.render_template(
        lookup=_mako_lookup(),
        template_name=template_name,
        context=context,
    ).html
    jinja_html = jinja_renderer.render_template(
        templates=_jinja_templates(),
        template_name=template_name,
        context=context,
    ).html
    return mako_html, jinja_html
