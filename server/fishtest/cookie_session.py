"""Signed cookie session helpers (UI auth support).

This intentionally does not depend on Pyramid. It provides the minimal surface
needed by existing Mako templates:

- `get_csrf_token()` for the meta tag in `base.mak`
- flash queues via `flash()`, `peek_flash()`, `pop_flash()`
- `invalidate()`

The session is stored client-side as an HMAC-signed blob.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

SESSION_COOKIE_NAME: Final[str] = "fishtest_session"
SESSION_SALT: Final[str] = "fishtest.session.v1"
DEFAULT_SAMESITE: Final[Literal["lax", "strict", "none"]] = "lax"


def _secret_key() -> str:
    """Return the application secret used for cookie signing."""
    # Reuse the deployment secret already present in systemd env.
    value = os.environ.get("FISHTEST_AUTHENTICATION_SECRET", "").strip()
    if not value:
        # Unsafe fallback for dev/test environments.
        value = "insecure-dev-secret"
    return value


def _signing_key() -> bytes:
    """Derive a stable signing key from the configured secret and salt."""
    material = f"{_secret_key()}:{SESSION_SALT}".encode()
    return hashlib.sha256(material).digest()


def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    """Base64url decode with optional missing padding."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _encode_cookie(payload: dict[str, Any]) -> str:
    """Serialize and sign a session payload for a cookie value."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8",
    )
    body = _b64url_encode(raw)
    sig = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _decode_cookie(value: str) -> dict[str, Any] | None:
    """Verify and deserialize a session cookie value."""
    if "." not in value:
        return None
    body_b64, sig_b64 = value.split(".", 1)

    try:
        raw = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except (binascii.Error, ValueError):
        return None

    expected = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
    # Constant-time compare to avoid signature oracle side channels.
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CookieSession:
    """Cookie-backed session with CSRF + flash support."""

    data: dict[str, Any]
    dirty: bool = False

    def get_csrf_token(self) -> str:
        """Return the CSRF token, generating one if needed."""
        token = self.data.get("csrf_token")
        if isinstance(token, str) and token:
            return token
        token = secrets.token_hex(32)
        self.data["csrf_token"] = token
        self.dirty = True
        return token

    def new_csrf_token(self) -> str:
        """Rotate the CSRF token."""
        token = secrets.token_hex(32)
        self.data["csrf_token"] = token
        self.dirty = True
        return token

    def flash(self, message: str, queue: str | None = None) -> None:
        """Add a flash message to the given queue."""
        key = queue or ""
        flashes = self.data.setdefault("flashes", {})
        if not isinstance(flashes, dict):
            flashes = {}
            self.data["flashes"] = flashes
        bucket = flashes.setdefault(key, [])
        if not isinstance(bucket, list):
            bucket = []
            flashes[key] = bucket
        bucket.append(str(message))
        self.dirty = True

    def peek_flash(self, queue: str | None = None) -> bool:
        """Return whether there are queued flashes (without consuming them)."""
        key = queue or ""
        flashes = self.data.get("flashes")
        if not isinstance(flashes, dict):
            return False
        bucket = flashes.get(key)
        return isinstance(bucket, list) and len(bucket) > 0

    def pop_flash(self, queue: str | None = None) -> list[str]:
        """Consume and return flash messages for the given queue."""
        key = queue or ""
        flashes = self.data.get("flashes")
        if not isinstance(flashes, dict):
            return []
        bucket = flashes.pop(key, [])
        if not isinstance(bucket, list):
            bucket = []
        if not flashes:
            self.data.pop("flashes", None)
        self.dirty = True
        return [str(x) for x in bucket]

    def invalidate(self) -> None:
        """Clear all session data."""
        self.data.clear()
        self.dirty = True


def load_session(request: Request) -> CookieSession:
    """Load the UI session from cookies.

    Args:
        request: Current request.

    Returns:
        Parsed session, or a new empty session if missing/invalid.

    """
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return CookieSession(data={"created_at": _utc_now_iso()})

    payload = _decode_cookie(raw)
    if payload is None:
        return CookieSession(data={"created_at": _utc_now_iso()})
    return CookieSession(data=dict(payload))


def commit_session(
    *,
    response: Response,
    session: CookieSession,
    remember: bool,
    secure: bool,
) -> None:
    """Persist a dirty session into a response cookie.

    Args:
        response: Outgoing response to attach cookies to.
        session: Current session.
        remember: Whether to keep the session cookie for a long period.
        secure: Whether to set the cookie as Secure.

    """
    if not session.dirty:
        return

    session.data.setdefault("updated_at", _utc_now_iso())
    value = _encode_cookie(session.data)
    max_age = 60 * 60 * 24 * 365 if remember else None

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite=DEFAULT_SAMESITE,
        path="/",
    )


def clear_session_cookie(*, response: Response, secure: bool) -> None:
    """Remove the session cookie from the client."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=secure,
        samesite=DEFAULT_SAMESITE,
    )
