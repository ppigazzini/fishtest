"""FastAPI/Starlette error handlers.

These handlers preserve legacy fishtest behavior:
- JSON 404s for `/api/...`
- HTML 404 page for UI routes rendered via Mako
- Cookie-session commit for UI 404 rendering
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.template_request import TemplateRequest
from fishtest.views.common import authenticated_user, is_https
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.responses import Response

STATUS_NOT_FOUND: Final[int] = 404
TEMPLATE_LOOKUP = default_template_lookup()

_WORKER_API_PATHS: Final[set[str]] = {
    "/api/request_version",
    "/api/request_task",
    "/api/update_task",
    "/api/beat",
    "/api/request_spsa",
    "/api/failed_task",
    "/api/stop_run",
    "/api/upload_pgn",
    "/api/worker_log",
}


def _duration_from_request(request: Request) -> float:
    started_at = getattr(request.state, "request_started_at", None)
    if isinstance(started_at, (int, float)):
        return max(0.0, time.monotonic() - float(started_at))
    return 0.0


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


async def _request_validation_handler(
    request: Request,
    exc: Exception,
) -> Response:
    if not isinstance(exc, RequestValidationError):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    # Keep worker protocol stable (always dict + duration).
    if request.url.path in _WORKER_API_PATHS:
        return JSONResponse(
            {
                "error": f"{request.url.path}: invalid request",
                "duration": _duration_from_request(request),
            },
            status_code=400,
        )

    # Preserve API semantics (JSON) while keeping UI routes out of it.
    if request.url.path.startswith("/api"):
        return JSONResponse(
            {
                "detail": "Invalid request",
                "errors": exc.errors(),
            },
            status_code=400,
        )

    return await request_validation_exception_handler(request, exc)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    # Worker protocol must return a JSON dict with duration.
    if request.url.path in _WORKER_API_PATHS:
        return JSONResponse(
            {
                "error": f"{request.url.path}: Internal Server Error",
                "duration": _duration_from_request(request),
            },
            status_code=500,
        )

    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    # For UI routes, avoid returning JSON by default.
    return PlainTextResponse("Internal Server Error", status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    """Register exception handlers to preserve legacy API/UI error behavior."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
