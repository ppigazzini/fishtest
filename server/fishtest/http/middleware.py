"""Starlette/FastAPI middleware.

These middlewares preserve legacy Pyramid behaviors while making middleware
ordering explicit and testable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from fishtest.http.api import WORKER_API_PATHS
from fishtest.http.cookie_session import (
    authenticated_user,
    clear_session_cookie,
    is_https,
    load_session,
)
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response


_BLOCKED_CACHE_TTL_SECONDS = 2.0


class _BlockedUserDb(Protocol):
    def get_blocked(self) -> list[dict[str, object]]:
        """Return blocked users from the data store."""


@dataclass
class _BlockedCache:
    timestamp: float | None = None
    value: list[dict[str, object]] | None = None


_blocked_cache = _BlockedCache()


def _get_blocked_cached(userdb: _BlockedUserDb) -> list[dict[str, object]]:
    now = time.monotonic()
    if (
        _blocked_cache.timestamp is not None
        and _blocked_cache.value is not None
        and now - _blocked_cache.timestamp < _BLOCKED_CACHE_TTL_SECONDS
    ):
        return _blocked_cache.value

    _blocked_cache.value = list(userdb.get_blocked())
    _blocked_cache.timestamp = now
    return _blocked_cache.value


async def _get_blocked_cached_async(userdb: _BlockedUserDb) -> list[dict[str, object]]:
    return await run_in_threadpool(_get_blocked_cached, userdb)


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
        """Short-circuit requests once shutdown has started."""
        rundb = getattr(request.app.state, "rundb", None)
        if rundb is not None and getattr(rundb, "_shutdown", False):
            return PlainTextResponse("", status_code=503)
        return await call_next(request)


def _duration_from_request(request: Request) -> float:
    started_at = getattr(request.state, "request_started_at", None)
    if isinstance(started_at, (int, float)):
        return max(0.0, time.monotonic() - float(started_at))
    return 0.0


class RejectNonPrimaryWorkerApiMiddleware(BaseHTTPMiddleware):
    """Return a stable worker-protocol error when misrouted to a secondary instance."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Reject worker API calls on a non-primary instance with a stable error."""
        path = request.url.path
        if path not in WORKER_API_PATHS:
            return await call_next(request)

        rundb = getattr(request.app.state, "rundb", None)
        if rundb is None:
            return await call_next(request)

        try:
            is_primary = bool(rundb.is_primary_instance())
        except (AttributeError, RuntimeError, TypeError):
            is_primary = True

        if is_primary:
            return await call_next(request)

        return JSONResponse(
            {
                "error": f"{path}: primary instance required",
                "duration": _duration_from_request(request),
            },
            status_code=503,
        )


class AttachRequestStateMiddleware(BaseHTTPMiddleware):
    """Attach DB handles to request.state and set base_url once."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Attach app state handles to request.state and stamp base_url."""
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
        """Invalidate session and redirect if an authenticated user is blocked."""
        path = request.url.path
        if path.startswith(("/api", "/static")):
            return await call_next(request)

        userdb = getattr(request.app.state, "userdb", None)
        if userdb is None:
            return await call_next(request)

        session = load_session(request)
        username = authenticated_user(session)
        if not username:
            return await call_next(request)

        blocked_users = await _get_blocked_cached_async(userdb)
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
