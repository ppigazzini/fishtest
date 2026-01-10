"""Template request shim for Mako templates.

The legacy Pyramid UI templates expect a request object with a small subset of
Pyramid's request API (notably: `session`, `authenticated_userid`, and
`static_url`).

FastAPI UI routes construct this shim and pass it as `request` in the template
context.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fishtest.cookie_session import CookieSession
    from fishtest.userdb import UserDb


_STATIC_DIR: Final[Path] = Path(__file__).resolve().parent / "static"
_STATIC_URL_PARAM: Final[str] = "x"
_STATIC_TOKEN_CACHE: dict[str, str] = {}


def _static_file_token(rel_path: str) -> str | None:
    """Return a Pyramid-compatible cache-buster token for a static file.

    Pyramid used a base64-encoded sha384 hash of the file contents as a query
    string parameter (see `FileHashCacheBuster` in the legacy implementation).

    Args:
        rel_path: Path relative to the server static directory, e.g.
            "css/application.css".

    Returns:
        The cache-buster token, or None if the file does not exist/read fails.

    """
    cached = _STATIC_TOKEN_CACHE.get(rel_path)
    if cached is not None:
        return cached

    file_path = _STATIC_DIR / rel_path
    try:
        content = file_path.read_bytes()
    except OSError:
        return None

    token = base64.b64encode(hashlib.sha384(content).digest()).decode("utf-8")
    _STATIC_TOKEN_CACHE[rel_path] = token
    return token


@dataclass
class TemplateRequest:
    """Subset of Pyramid's request API required by shared templates."""

    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    query_params: Mapping[str, str]
    session: CookieSession
    authenticated_userid: str | None
    userdb: UserDb
    url: str

    @property
    def GET(self) -> Mapping[str, str]:  # noqa: N802
        """Pyramid-compatible alias for query parameters."""
        return self.query_params

    def static_url(self, spec: str) -> str:
        """Map a Pyramid asset spec to the FastAPI static mount.

        This preserves Pyramid's cache-busting behavior by appending a stable
        query string token derived from the file contents.
        """
        prefix = "fishtest:static/"
        rel_path = spec.removeprefix(prefix)
        rel_path = rel_path.lstrip("/")

        url = "/static/" + rel_path
        token = _static_file_token(rel_path)
        if token is None:
            return url
        return f"{url}?{_STATIC_URL_PARAM}={token}"
