"""Unified template renderer for Mako and Jinja2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from fishtest.http import jinja as jinja_renderer
from fishtest.http import mako as mako_renderer
from fishtest.http import mako_new as mako_new_renderer
from jinja2 import TemplateNotFound

if TYPE_CHECKING:
    from collections.abc import Mapping

TemplateEngine = Literal["mako", "mako_new", "jinja"]

DEFAULT_ENGINE: Final = "jinja"

_MAKO_LOOKUP = mako_renderer.default_template_lookup()
_MAKO_NEW_LOOKUP = mako_new_renderer.default_template_lookup()
_JINJA_ENV = jinja_renderer.default_environment()


@dataclass(frozen=True)
class RenderedTemplate:
    """Represents a rendered HTML payload."""

    html: str
    engine: TemplateEngine


@dataclass
class _EngineState:
    engine: TemplateEngine


_STATE = _EngineState(engine=DEFAULT_ENGINE)


def _jinja_template_exists(template_name: str) -> bool:
    template_path = jinja_renderer.templates_dir() / template_name
    return template_path.exists()


def _mako_new_template_exists(template_name: str) -> bool:
    template_path = mako_new_renderer.templates_dir() / template_name
    return template_path.exists()


def template_engine_for(_template_name: str) -> TemplateEngine:
    """Return the renderer to use for a template name."""
    return _STATE.engine


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
    engine = template_engine_for(template_name)
    if engine == "jinja":
        rendered = jinja_renderer.render_template(
            environment=_JINJA_ENV,
            template_name=template_name,
            context=context,
        )
        return RenderedTemplate(html=rendered.html, engine=engine)

    if engine == "mako_new":
        rendered = mako_new_renderer.render_template(
            lookup=_MAKO_NEW_LOOKUP,
            template_name=template_name,
            context=context,
        )
        return RenderedTemplate(html=rendered.html, engine=engine)

    rendered = mako_renderer.render_template(
        lookup=_MAKO_LOOKUP,
        template_name=template_name,
        context=context,
    )
    return RenderedTemplate(html=rendered.html, engine=engine)


def render_template_dual(
    *,
    template_name: str,
    context: Mapping[str, object],
) -> tuple[str, str]:
    """Render the template with both engines for parity checks."""
    mako_html = mako_renderer.render_template(
        lookup=_MAKO_LOOKUP,
        template_name=template_name,
        context=context,
    ).html
    jinja_html = jinja_renderer.render_template(
        environment=_JINJA_ENV,
        template_name=template_name,
        context=context,
    ).html
    return mako_html, jinja_html


def render_template_legacy_mako(
    *,
    template_name: str,
    context: Mapping[str, object],
) -> str:
    """Render a template with the legacy Mako engine."""
    return mako_renderer.render_template(
        lookup=_MAKO_LOOKUP,
        template_name=template_name,
        context=context,
    ).html


def render_template_mako_new(
    *,
    template_name: str,
    context: Mapping[str, object],
) -> str:
    """Render a template with the new Mako engine."""
    return mako_new_renderer.render_template(
        lookup=_MAKO_NEW_LOOKUP,
        template_name=template_name,
        context=context,
    ).html


def render_template_jinja(
    *,
    template_name: str,
    context: Mapping[str, object],
) -> str:
    """Render a template with the Jinja2 engine."""
    return jinja_renderer.render_template(
        environment=_JINJA_ENV,
        template_name=template_name,
        context=context,
    ).html


def assert_jinja_template_exists(template_name: str) -> None:
    """Raise TemplateNotFound if a Jinja2 template is missing."""
    if not _jinja_template_exists(template_name):
        raise TemplateNotFound(template_name)


def assert_mako_new_template_exists(template_name: str) -> None:
    """Raise TemplateNotFound if a new Mako template is missing."""
    if not _mako_new_template_exists(template_name):
        raise TemplateNotFound(template_name)
