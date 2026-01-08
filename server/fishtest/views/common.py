"""Shared helpers for FastAPI-rendered UI pages.

This keeps session/auth/HTTPS detection consistent across UI endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import Request
    from fishtest.cookie_session import CookieSession


def is_https(request: Request) -> bool:
    """Return whether the original request was HTTPS (proxy-aware)."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded == "https"
    return request.url.scheme == "https"


def authenticated_user(session: CookieSession) -> str | None:
    """Return the logged-in username from the session, if present."""
    value = session.data.get("user")
    return value if isinstance(value, str) and value else None


def authenticated_user_from_data(session_data: Mapping[str, object]) -> str | None:
    """Return the logged-in username from raw session data, if present."""
    value = session_data.get("user")
    return value if isinstance(value, str) and value else None
