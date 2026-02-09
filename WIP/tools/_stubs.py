"""Shared test stubs for parity tooling."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fishtest.http import template_helpers as helpers


class SessionStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    def get_csrf_token(self) -> str:
        return "csrf-token"

    def peek_flash(self, _category: str | None = None) -> bool:
        return False

    def pop_flash(self, _category: str | None = None) -> list[str]:
        return []


class UserDbStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    def get_users(self) -> list[dict[str, Any]]:
        return list(self._data.get("users", []))

    def get_pending(self) -> list[dict[str, Any]]:
        return list(self._data.get("pending", []))


class RequestStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.url = data.get("url", "/")
        self.GET = data.get("GET", {})
        self.headers = data.get("headers", {})
        self.cookies = data.get("cookies", {})
        self.query_params = data.get("query_params", self.GET)
        self.authenticated_userid = data.get("authenticated_userid")
        self.session = SessionStub(data.get("session"))
        self.userdb = UserDbStub(data.get("userdb"))

    def static_url(self, asset: str) -> str:
        return f"/static/{asset}"


def with_request_stub(context: dict[str, Any]) -> dict[str, Any]:
    request_data = context.get("request")
    if isinstance(request_data, dict) or request_data is None:
        context["request"] = RequestStub(request_data)
    return context


def with_helpers(context: dict[str, Any]) -> dict[str, Any]:
    context.setdefault("csrf_token", "csrf-token")
    context.setdefault("theme", "")
    context.setdefault("current_user", None)
    context.setdefault("pending_users_count", 0)
    context.setdefault("flash", {"error": [], "warning": [], "info": []})
    context.setdefault("page_title", "")
    context.setdefault("urls", {})
    context.setdefault("static_url", lambda asset: f"/static/{asset}")
    context.setdefault(
        "url_for",
        lambda name, **params: (
            f"/url/{name}" + (f"?{urlencode(params)}" if params else "")
        ),
    )
    context.setdefault("display_residual", helpers.display_residual)
    context.setdefault("format_bounds", helpers.format_bounds)
    context.setdefault("format_date", helpers.format_date)
    context.setdefault("format_group", helpers.format_group)
    context.setdefault("format_results", helpers.format_results)
    context.setdefault("format_time_ago", helpers.format_time_ago)
    context.setdefault("get_cookie", helpers.get_cookie)
    context.setdefault("is_active_sprt_ltc", helpers.is_active_sprt_ltc)
    context.setdefault("tests_repo", helpers.tests_repo)
    context.setdefault("worker_name", helpers.worker_name)
    return context
