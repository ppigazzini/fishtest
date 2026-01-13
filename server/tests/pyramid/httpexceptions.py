"""Minimal `pyramid.httpexceptions` stubs.

Implements only what the legacy fishtest unit tests and modules need.
"""

from __future__ import annotations


class HTTPException(Exception):
    code = 500

    def __init__(self, detail=None, headers=None, location: str | None = None):
        super().__init__(detail)
        self.detail = detail
        self.headers = headers or {}
        self.location = location


class HTTPBadRequest(HTTPException):
    code = 400


class HTTPUnauthorized(HTTPException):
    code = 401


class HTTPForbidden(HTTPException):
    code = 403


class HTTPNotFound(HTTPException):
    code = 404


class HTTPFound(HTTPException):
    code = 302

    def __init__(self, location: str = "", headers=None, detail=None):
        super().__init__(detail=detail, headers=headers, location=location)

    def __str__(self) -> str:
        return f"The resource was found at {self.location}"
