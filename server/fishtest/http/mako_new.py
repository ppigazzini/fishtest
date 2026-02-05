"""New Mako template rendering helpers for the FastAPI UI."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING, Final

from mako.lookup import TemplateLookup
from starlette.responses import HTMLResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mako.template import Template
    from starlette.background import BackgroundTask
    from starlette.types import Receive, Scope, Send

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


@dataclass(frozen=True)
class TemplateResponseOptions:
    """Options for building a Mako template response."""

    status_code: int = 200
    headers: Mapping[str, str] | None = None
    media_type: str | None = None
    background: BackgroundTask | None = None


class MakoTemplateResponse(HTMLResponse):
    """TemplateResponse-style wrapper for Mako rendering."""

    def __init__(
        self,
        template: Template,
        context: dict[str, object],
        *,
        options: TemplateResponseOptions | None = None,
    ) -> None:
        """Initialize the Mako template response."""
        self.template = template
        self.context = context
        html = template.render(**context)
        opts = options or TemplateResponseOptions()
        super().__init__(
            html,
            opts.status_code,
            opts.headers,
            opts.media_type,
            opts.background,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send debug metadata before the HTML response, when enabled."""
        request = self.context.get("request")
        raw_request = getattr(request, "raw_request", None)
        extensions = getattr(raw_request or request, "extensions", None)
        if isinstance(extensions, dict) and "http.response.debug" in extensions:
            await send(
                {
                    "type": "http.response.debug",
                    "info": {"template": self.template, "context": self.context},
                },
            )
        await super().__call__(scope, receive, send)


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


def render_template_response(
    *,
    lookup: TemplateLookup,
    template_name: str,
    context: Mapping[str, object],
    options: TemplateResponseOptions | None = None,
) -> MakoTemplateResponse:
    """Render a new Mako template into a TemplateResponse-style wrapper."""
    template = lookup.get_template(template_name)
    return MakoTemplateResponse(
        template,
        dict(context),
        options=options,
    )
