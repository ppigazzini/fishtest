"""Starlette/FastAPI middleware.

These middlewares preserve legacy Pyramid behaviors while making middleware
ordering explicit and testable.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import RedirectResponse
from fishtest.cookie_session import clear_session_cookie, load_session
from fishtest.views.common import authenticated_user, is_https
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.responses import Response


def _external_base_url_from_request(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    scheme = forwarded or request.url.scheme
    host = request.headers.get("host")
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


class ShutdownGuardMiddleware(BaseHTTPMiddleware):
    """Return HTTP 503 when the app is shutting down."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        rundb = getattr(request.app.state, "rundb", None)
        if rundb is not None and getattr(rundb, "_shutdown", False):
            return PlainTextResponse("", status_code=503)
        return await call_next(request)


class AttachRequestStateMiddleware(BaseHTTPMiddleware):
    """Attach DB handles to request.state and set base_url once."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Used by centralized exception handlers (e.g., worker protocol duration).
        if getattr(request.state, "request_started_at", None) is None:
            request.state.request_started_at = time.monotonic()

        rundb = getattr(request.app.state, "rundb", None)
        if rundb is not None:
            request.state.rundb = rundb
            request.state.userdb = getattr(request.app.state, "userdb", None)
            request.state.actiondb = getattr(request.app.state, "actiondb", None)
            request.state.workerdb = getattr(request.app.state, "workerdb", None)

            if not getattr(rundb, "_base_url_set", True):
                rundb.base_url = _external_base_url_from_request(request)
                rundb._base_url_set = True  # noqa: SLF001

        return await call_next(request)


class RedirectBlockedUiUsersMiddleware(BaseHTTPMiddleware):
    """If an authenticated UI user becomes blocked, invalidate and redirect."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        if path.startswith("/api") or path.startswith("/static"):
            return await call_next(request)

        userdb = getattr(request.app.state, "userdb", None)
        if userdb is None:
            return await call_next(request)

        session = load_session(request)
        username = authenticated_user(session)
        if not username:
            return await call_next(request)

        blocked_users = userdb.get_blocked()
        is_blocked = any(
            isinstance(user, dict)
            and user.get("username") == username
            and user.get("blocked")
            for user in blocked_users
        )
        if is_blocked:
            session.invalidate()
            response = RedirectResponse(url="/tests", status_code=302)
            clear_session_cookie(response=response, secure=is_https(request))
            return response

        return await call_next(request)
