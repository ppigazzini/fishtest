"""Jinja2 template rendering helpers for the FastAPI UI."""

from __future__ import annotations

import copy
import datetime
import math
import urllib.parse
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING, Final

import fishtest
import fishtest.github_api as gh
from fishtest.http import template_helpers as helpers
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape
from starlette.templating import Jinja2Templates

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import Request
    from starlette.background import BackgroundTask
    from starlette.responses import Response

REPO_ROOT_DEPTH: Final[int] = 3
TEMPLATES_DIR_ENV: Final[str] = "FISHTEST_JINJA_TEMPLATES_DIR"
_MISSING_REQUEST_ERROR: Final[str] = (
    "context must include TemplateRequest under 'request'"
)


def _repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parents[REPO_ROOT_DEPTH]


def templates_dir() -> Path:
    """Return the Jinja2 templates directory path."""
    raw = environ.get(TEMPLATES_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    return _repo_root() / "server" / "fishtest" / "templates_jinja2"


class MakoUndefined(Undefined):
    """Match Mako's UNDEFINED rendering behavior."""

    def __str__(self) -> str:
        """Return the UNDEFINED sentinel string."""
        return "UNDEFINED"

    def __repr__(self) -> str:
        """Return the UNDEFINED sentinel representation."""
        return "UNDEFINED"


def default_environment() -> Environment:
    """Return a Jinja2 environment bound to the Jinja2 templates directory."""
    env = Environment(
        loader=FileSystemLoader(str(templates_dir())),
        autoescape=select_autoescape(["html", "xml", "mak"]),
        undefined=MakoUndefined,
        extensions=["jinja2.ext.do"],
    )
    env.filters["urlencode"] = helpers.urlencode
    env.filters["split"] = lambda value, sep=None, maxsplit=-1: str(value).split(
        sep,
        maxsplit,
    )
    env.filters["string"] = str
    env.globals.update(
        {
            "copy": copy,
            "datetime": datetime,
            "diff_url": helpers.diff_url,
            "display_residual": helpers.display_residual,
            "fishtest": fishtest,
            "float": float,
            "format_bounds": helpers.format_bounds,
            "format_date": helpers.format_date,
            "format_group": helpers.format_group,
            "format_results": helpers.format_results,
            "format_time_ago": helpers.format_time_ago,
            "gh": gh,
            "get_cookie": helpers.get_cookie,
            "is_active_sprt_ltc": helpers.is_active_sprt_ltc,
            "is_elo_pentanomial_run": helpers.is_elo_pentanomial_run,
            "list_to_string": helpers.list_to_string,
            "math": math,
            "pdf_to_string": helpers.pdf_to_string,
            "results_pre_attrs": helpers.results_pre_attrs,
            "nelo_pentanomial_summary": helpers.nelo_pentanomial_summary,
            "run_tables_prefix": helpers.run_tables_prefix,
            "t_conf": helpers.t_conf,
            "tests_run_setup": helpers.tests_run_setup,
            "tests_repo": helpers.tests_repo,
            "urllib": urllib.parse,
            "worker_name": helpers.worker_name,
        },
    )
    return env


def default_templates() -> Jinja2Templates:
    """Return a Starlette Jinja2Templates instance with the custom environment."""
    env = default_environment()
    return Jinja2Templates(env=env)


@dataclass(frozen=True)
class RenderedTemplate:
    """Represents a rendered HTML payload."""

    html: str


@dataclass(frozen=True)
class TemplateResponseOptions:
    """Options for building a Jinja2 template response."""

    status_code: int = 200
    headers: Mapping[str, str] | None = None
    media_type: str | None = None
    background: BackgroundTask | None = None


def render_template(
    *,
    templates: Jinja2Templates,
    template_name: str,
    context: Mapping[str, object],
) -> RenderedTemplate:
    """Render a Jinja2 template to HTML."""
    template = templates.get_template(template_name)
    html = template.render(**dict(context))
    return RenderedTemplate(html=html)


def render_template_response(
    *,
    templates: Jinja2Templates,
    request: Request,
    template_name: str,
    context: Mapping[str, object],
    options: TemplateResponseOptions | None = None,
) -> Response:
    """Render a template and return a Starlette TemplateResponse."""
    opts = options or TemplateResponseOptions()
    context_dict = dict(context)
    if "request" not in context_dict:
        raise ValueError(_MISSING_REQUEST_ERROR)
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context_dict,
        status_code=opts.status_code,
        headers=opts.headers,
        media_type=opts.media_type,
        background=opts.background,
    )
