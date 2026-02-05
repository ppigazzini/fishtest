"""UI pipeline helpers for FastAPI HTTP views.

Ownership: build template request shims and apply response cache headers.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from fishtest.http.cookie_session import authenticated_user
from fishtest.http.dependencies import get_userdb
from fishtest.http.template_request import TemplateRequest

if TYPE_CHECKING:
    from fastapi import Request
    from fishtest.http.cookie_session import CookieSession
    from starlette.responses import Response


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
        raw_request=request,
    )


def apply_http_cache(response: Response, cfg: dict[str, object] | None) -> Response:
    """Apply `Cache-Control` from view config when missing."""
    http_cache = cfg.get("http_cache") if cfg else None
    if http_cache is not None and "Cache-Control" not in response.headers:
        with suppress(Exception):
            if isinstance(http_cache, (int, float, str)):
                response.headers["Cache-Control"] = f"max-age={int(http_cache)}"
    return response
