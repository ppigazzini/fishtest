"""Shared HTTP boundary (FastAPI UI/API).

Ownership: request shims, session commit helpers, and template context wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import TYPE_CHECKING, Annotated, Protocol

from fastapi import Depends, Request
from fishtest.http.cookie_session import (
    CookieSession,
    authenticated_user,
    clear_session_cookie,
    commit_session,
    is_https,
    load_session,
)
from fishtest.http.csrf import csrf_or_403
from fishtest.http.dependencies import (
    DependencyNotInitializedError,
    get_actiondb,
    get_rundb,
    get_userdb,
)
from fishtest.http.ui_context import UIRequestContext, build_ui_context

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, MutableMapping

    from starlette.responses import Response


class ResponseShimLike(Protocol):
    """Protocol for shim response metadata."""

    headers: MutableMapping[str, str]
    headerlist: Iterable[tuple[str, str]]


class RequestShimHeaders(Protocol):
    """Protocol for shims that expose response headers."""

    response: ResponseShimLike


class RequestShimSession(Protocol):
    """Protocol for shims that control session persistence."""

    forget: bool
    remember: bool


class RequestShimSessionUser(RequestShimSession, Protocol):
    """Protocol for UI shims with session state."""

    session: CookieSession


@dataclass
class ResponseShim:
    """Minimal response shim for header propagation."""

    headers: dict[str, str] = field(default_factory=dict)
    headerlist: list[tuple[str, str]] = field(default_factory=list)


class ApiRequestShim:
    """Minimal request shim to keep the API port mechanical."""

    def __init__(
        self,
        request: Request,
        *,
        json_body: object | None = None,
        json_error: bool = False,
        matchdict: dict[str, str] | None = None,
    ) -> None:
        """Initialize the request shim with parsed request metadata."""
        self._request = request
        self._json_body = json_body
        self._json_error = json_error
        self.matchdict = matchdict or {}
        self.params = request.query_params
        self.headers = request.headers
        self.cookies = request.cookies
        self.url = request.url
        self.scheme = request.url.scheme
        self.host = request.headers.get("host") or request.url.netloc
        self.host_url = str(request.base_url).rstrip("/")
        self.remote_addr = request.client.host if request.client else None
        self.response = ResponseShim()

        try:
            self.rundb = get_rundb(request)
        except DependencyNotInitializedError:
            self.rundb = None
        try:
            self.userdb = get_userdb(request)
        except DependencyNotInitializedError:
            self.userdb = None
        try:
            self.actiondb = get_actiondb(request)
        except DependencyNotInitializedError:
            self.actiondb = None

    @property
    def json_body(self) -> object | None:
        """Return the parsed JSON body, raising if the request was invalid."""
        if self._json_error:
            message = "request is not json encoded"
            raise ValueError(message)
        return self._json_body


@dataclass(frozen=True)
class JsonBodyResult:
    """Result of JSON parsing with error flag."""

    body: object | None
    error: bool


async def get_json_body(request: Request) -> JsonBodyResult:
    """Parse JSON body, preserving legacy error behavior."""
    try:
        body = await request.json()
    except (JSONDecodeError, TypeError, ValueError):
        return JsonBodyResult(body=None, error=True)
    return JsonBodyResult(body=body, error=False)


async def get_request_shim(
    request: Request,
    matchdict: dict[str, str] | None = None,
) -> ApiRequestShim:
    """Dependency that builds the API request shim."""
    json_body = await get_json_body(request)
    return ApiRequestShim(
        request,
        json_body=json_body.body,
        json_error=json_body.error,
        matchdict=matchdict,
    )


def apply_response_headers(shim: RequestShimHeaders, response: Response) -> Response:
    """Apply Pyramid-style response headers from the request shim."""
    for k, v in getattr(shim.response, "headers", {}).items():
        response.headers[k] = v
    for k, v in getattr(shim.response, "headerlist", []):
        response.headers[k] = v
    return response


def commit_session_flags(
    request: Request,
    session: CookieSession,
    response: Response,
    *,
    remember: bool,
    forget: bool,
) -> Response:
    """Commit or clear the session cookie with explicit flags."""
    if forget:
        clear_session_cookie(response=response, secure=is_https(request))
    else:
        commit_session(
            response=response,
            session=session,
            remember=remember,
            secure=is_https(request),
        )
    return response


def commit_session_response(
    request: Request,
    session: CookieSession,
    shim: RequestShimSession,
    response: Response,
) -> Response:
    """Commit or clear the session cookie based on shim flags."""
    remember_flag = getattr(shim, "remember", False)
    forget_flag = getattr(shim, "forget", False)
    if not remember_flag:
        remember_flag = getattr(shim, "_remember", False)
    if not forget_flag:
        forget_flag = getattr(shim, "_forget", False)
    return commit_session_flags(
        request,
        session,
        response,
        remember=remember_flag,
        forget=forget_flag,
    )


def remember(
    request: RequestShimSessionUser,
    username: str,
    max_age: int | None = None,
) -> list[tuple[str, str]]:
    """Remember a user in the session and mark the cookie for persistence."""
    request.session.data["user"] = username
    request.session.dirty = True
    request.remember = max_age is not None
    return []


def forget(request: RequestShimSessionUser) -> list[tuple[str, str]]:
    """Forget the current user and mark the session to clear the cookie."""
    request.session.data.pop("user", None)
    request.session.dirty = True
    request.forget = True
    return []


def build_template_context(
    request: Request,
    session: CookieSession,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the shared template context (includes `request`)."""

    def _pop_flash_list(queue: str | None = None) -> list[str]:
        return session.pop_flash(queue)

    template_request = build_ui_context(request, session).template_request
    user = authenticated_user(session)
    pending_users_count = 0
    try:
        pending_users_count = len(get_userdb(request).get_pending())
    except DependencyNotInitializedError:
        pending_users_count = 0

    base_context: dict[str, object] = {
        "csrf_token": session.get_csrf_token(),
        "current_user": {"username": user} if user else None,
        "flash": {
            "error": _pop_flash_list("error"),
            "warning": _pop_flash_list("warning"),
            "info": _pop_flash_list(),
        },
        "pending_users_count": pending_users_count,
        "static_url": template_request.static_url,
        "theme": request.cookies.get("theme", ""),
        "urls": {
            "home": "/",
            "login": "/login",
            "logout": "/logout",
            "signup": "/signup",
            "user_profile": "/user",
            "tests": "/tests",
            "tests_finished_ltc": "/tests/finished?ltc_only=1",
            "tests_finished_success": "/tests/finished?success_only=1",
            "tests_finished_yellow": "/tests/finished?yellow_only=1",
            "tests_run": "/tests/run",
            "tests_user_prefix": "/tests/user/",
            "tests_machines": "/tests/machines",
            "nn_upload": "/upload",
            "nns": "/nns",
            "contributors": "/contributors",
            "contributors_monthly": "/contributors/monthly",
            "actions": "/actions",
            "user_management": "/user_management",
            "workers_blocked": "/workers/show",
            "sprt_calc": "/sprt_calc",
            "rate_limits": "/rate_limits",
            "api_rate_limit": "/api/rate_limit",
        },
    }

    context: dict[str, object] = {
        "request": request,
        "template_request": template_request,
        **base_context,
    }
    if extra:
        context.update(extra)
    return context


def get_ui_context(request: Request) -> UIRequestContext:
    """Dependency that builds the UI request context."""
    session = load_session(request)
    return build_ui_context(request, session)


RequestShimDep = Annotated[ApiRequestShim, Depends(get_request_shim)]
JsonBodyDep = Annotated[JsonBodyResult, Depends(get_json_body)]
UIContextDep = Annotated[UIRequestContext, Depends(get_ui_context)]

__all__ = [
    "ApiRequestShim",
    "JsonBodyDep",
    "JsonBodyResult",
    "RequestShimDep",
    "UIContextDep",
    "apply_response_headers",
    "build_template_context",
    "commit_session_flags",
    "commit_session_response",
    "csrf_or_403",
    "forget",
    "get_json_body",
    "get_request_shim",
    "get_ui_context",
    "remember",
]
