"""UI pipeline helpers for FastAPI HTTP views."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

from fishtest.http.cookie_session import (
    authenticated_user,
    clear_session_cookie,
    commit_session,
    is_https,
)
from fishtest.http.dependencies import get_userdb
from fishtest.http.template_request import TemplateRequest

if TYPE_CHECKING:
    from collections.abc import Iterable, MutableMapping

    from fastapi import Request
    from fishtest.http.cookie_session import CookieSession
    from starlette.responses import Response


class ResponseShim(Protocol):
    """Protocol for shim response metadata."""

    headers: MutableMapping[str, str]
    headerlist: Iterable[tuple[str, str]]


class RequestShim(Protocol):
    """Protocol for request shim state used by the UI pipeline."""

    response: ResponseShim
    _forget: bool
    _remember: bool


def build_template_request(
    request: Request,
    session: CookieSession,
) -> TemplateRequest:
    """Build a Pyramid-compatible template request object."""
    return TemplateRequest(
        headers=request.headers,
        cookies=request.cookies,
        query_params=request.query_params,
        session=session,
        authenticated_userid=authenticated_user(session),
        userdb=get_userdb(request),
        url=str(request.url),
    )


def apply_response_headers(shim: RequestShim, response: Response) -> Response:
    """Apply Pyramid-style response headers from the request shim."""
    for k, v in getattr(shim.response, "headers", {}).items():
        response.headers[k] = v
    for k, v in getattr(shim.response, "headerlist", []):
        response.headers[k] = v
    return response


def apply_session_cookie(
    request: Request,
    session: CookieSession,
    shim: RequestShim,
    response: Response,
) -> Response:
    """Commit or clear the session cookie based on shim flags."""
    if getattr(shim, "_forget", False):
        clear_session_cookie(response=response, secure=is_https(request))
    else:
        commit_session(
            response=response,
            session=session,
            remember=getattr(shim, "_remember", False),
            secure=is_https(request),
        )
    return response


def apply_http_cache(response: Response, cfg: dict[str, object] | None) -> Response:
    """Apply `Cache-Control` from view config when missing."""
    http_cache = cfg.get("http_cache") if cfg else None
    if http_cache is not None and "Cache-Control" not in response.headers:
        with suppress(Exception):
            if isinstance(http_cache, (int, float, str)):
                response.headers["Cache-Control"] = f"max-age={int(http_cache)}"
    return response
