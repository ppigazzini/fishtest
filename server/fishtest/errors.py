"""FastAPI/Starlette error handlers.

These handlers preserve legacy fishtest behavior:
- JSON 404s for `/api/...`
- HTML 404 page for UI routes rendered via Mako
- Cookie-session commit for UI 404 rendering
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.views.auth import TemplateRequest
from fishtest.views.common import authenticated_user, is_https
from starlette.exceptions import HTTPException as StarletteHTTPException

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.responses import Response

STATUS_NOT_FOUND: Final[int] = 404
TEMPLATE_LOOKUP = default_template_lookup()


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    if exc.status_code != STATUS_NOT_FOUND:
        return await http_exception_handler(request, exc)

    # Preserve JSON behavior for API endpoints.
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Not Found"}, status_code=STATUS_NOT_FOUND)

    session = load_session(request)
    userdb = request.app.state.userdb

    template_request = TemplateRequest(
        headers=request.headers,
        cookies=request.cookies,
        query_params=request.query_params,
        session=session,
        authenticated_userid=authenticated_user(session),
        userdb=userdb,
        url=str(request.url),
    )

    rendered = render_template(
        lookup=TEMPLATE_LOOKUP,
        template_name="notfound.mak",
        context={"request": template_request},
    )
    response = HTMLResponse(rendered.html, status_code=STATUS_NOT_FOUND)
    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


def install_error_handlers(app: FastAPI) -> None:
    """Register exception handlers to preserve legacy API/UI error behavior."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
