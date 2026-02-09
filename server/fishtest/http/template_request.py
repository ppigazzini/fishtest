"""Template request shim for UI templates.

The legacy Pyramid UI templates expect a request object with a small subset of
Pyramid's request API (notably: `session`, `authenticated_userid`, and
`static_url`).

FastAPI UI routes construct this shim and pass it as `request` in the template
context.
"""

from __future__ import annotations

import base64
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import Request
    from fishtest.http.cookie_session import CookieSession
    from fishtest.userdb import UserDb


_STATIC_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "static"
_STATIC_URL_PARAM: Final[str] = "x"
_STATIC_TOKEN_CACHE_MAX: int = 1024
_STATIC_TOKEN_CACHE: "OrderedDict[str, str]" = OrderedDict()
_MISSING_RAW_REQUEST_ERROR: Final[str] = (
    "TemplateRequest.url_for requires a FastAPI request"
)


def _static_file_token(rel_path: str) -> str | None:
    """Return a Pyramid-compatible cache-buster token for a static file."""
    rel_path = rel_path.replace("\\", "/")
    rel_obj = Path(rel_path)
    if rel_obj.is_absolute() or ".." in rel_obj.parts:
        return None

    cached = _STATIC_TOKEN_CACHE.get(rel_path)
    if cached is not None:
        return cached

    file_path = (_STATIC_DIR / rel_path).resolve()
    try:
        file_path.relative_to(_STATIC_DIR)
    except ValueError:
        return None
    try:
        content = file_path.read_bytes()
    except OSError:
        return None

    token = (
        base64.urlsafe_b64encode(hashlib.sha384(content).digest())
        .decode("utf-8")
        .rstrip("=")
    )

    if _STATIC_TOKEN_CACHE_MAX > 0:
        if len(_STATIC_TOKEN_CACHE) >= _STATIC_TOKEN_CACHE_MAX:
            _STATIC_TOKEN_CACHE.popitem(last=False)
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
    raw_request: Request | None = field(default=None, repr=False)

    @property
    def GET(self) -> Mapping[str, str]:  # noqa: N802
        """Pyramid-compatible alias for query parameters."""
        return self.query_params

    def static_url(self, spec: str) -> str:
        """Map a Pyramid asset spec to the FastAPI static mount."""
        prefix = "fishtest:static/"
        rel_path = spec.removeprefix(prefix)
        rel_path = rel_path.lstrip("/")

        url = "/static/" + rel_path
        token = _static_file_token(rel_path)
        if token is None:
            return url
        return f"{url}?{_STATIC_URL_PARAM}={token}"

    def url_for(self, name: str, **path_params: object) -> str:
        """Return a Starlette-style URL when a raw request is available."""
        if self.raw_request is None:
            raise RuntimeError(_MISSING_RAW_REQUEST_ERROR)
        return str(self.raw_request.url_for(name, **path_params))
