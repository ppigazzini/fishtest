"""UI error rendering helpers for FastAPI.

Ownership: render legacy UI error templates and commit session cookies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import HTMLResponse
from fishtest.http.boundary import build_template_context, commit_session_flags
from fishtest.http.cookie_session import load_session
from fishtest.http.template_renderer import render_template
from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from fastapi import Request


async def render_notfound_response(request: Request) -> HTMLResponse:
    """Render the legacy UI 404 page and commit the cookie session."""
    session = load_session(request)

    context = build_template_context(request, session)

    # Mako rendering is sync and can be CPU heavy; keep it off the event loop.
    rendered = await run_in_threadpool(
        render_template,
        template_name="notfound.mak",
        context=context,
    )
    response = HTMLResponse(rendered.html, status_code=404)
    commit_session_flags(
        request,
        session,
        response,
        remember=False,
        forget=False,
    )
    return response


async def render_forbidden_response(request: Request) -> HTMLResponse:
    """Render the legacy UI 403 page (login) and commit the cookie session."""
    session = load_session(request)
    session.flash("Please login")

    context = build_template_context(request, session)

    rendered = await run_in_threadpool(
        render_template,
        template_name="login.mak",
        context=context,
    )
    response = HTMLResponse(rendered.html, status_code=403)
    commit_session_flags(
        request,
        session,
        response,
        remember=False,
        forget=False,
    )
    return response
