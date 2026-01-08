"""FastAPI-native UI authentication routes.

This module provides `/login` and `/logout` implementations that render the
existing Mako templates, without proxying to Pyramid.

This is intentionally minimal: it is designed to satisfy the template contract
(`request.session.get_csrf_token()`, flashes, `request.authenticated_userid`) for
pages rendered by the FastAPI app.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final, cast

import requests
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fishtest.cookie_session import (
    CookieSession,
    clear_session_cookie,
    commit_session,
    load_session,
)
from fishtest.mako import default_template_lookup, render_template
from fishtest.schemas import github_repo
from fishtest.util import email_valid, password_strength
from fishtest.views.common import authenticated_user, is_https
from vtjson import ValidationError, union, validate

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fishtest.userdb import UserDb
    from starlette.datastructures import FormData


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()

HTTP_TIMEOUT = 15

_STATIC_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "static"
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


def _validate_csrf(
    *,
    request: Request,
    session: CookieSession,
    form_token: str | None,
) -> bool:
    """Validate CSRF using `X-CSRF-Token` header or `csrf_token` form field."""
    header_token = request.headers.get("x-csrf-token")
    token = header_token or form_token
    if not token:
        return False
    expected = session.get_csrf_token()
    return secrets.compare_digest(token, expected)


def _render_login(
    *,
    request: Request,
    session: CookieSession,
    userdb: UserDb,
) -> HTMLResponse:
    """Render `login.mak` using the shared base template."""
    template_request = TemplateRequest(
        headers=request.headers,
        cookies=request.cookies,
        query_params=request.query_params,
        session=session,
        authenticated_userid=authenticated_user(session),
        userdb=userdb,
        url=str(request.url),
    )
    rendered = render_template(
        lookup=TEMPLATE_LOOKUP,
        template_name="login.mak",
        context={"request": template_request},
    )
    return HTMLResponse(rendered.html)


def _render_signup(
    *,
    request: Request,
    session: CookieSession,
    userdb: UserDb,
) -> HTMLResponse:
    template_request = TemplateRequest(
        headers=request.headers,
        cookies=request.cookies,
        query_params=request.query_params,
        session=session,
        authenticated_userid=authenticated_user(session),
        userdb=userdb,
        url=str(request.url),
    )
    rendered = render_template(
        lookup=TEMPLATE_LOOKUP,
        template_name="signup.mak",
        context={"request": template_request},
    )
    return HTMLResponse(rendered.html)


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> Response:
    """Render the existing login page."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    if authenticated_user(session):
        return RedirectResponse(url="/tests", status_code=303)

    response = _render_login(request=request, session=session, userdb=userdb)
    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_get(request: Request) -> Response:
    """Render the existing signup page."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    if authenticated_user(session):
        return RedirectResponse(url="/tests", status_code=303)

    response = _render_signup(request=request, session=session, userdb=userdb)
    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


@router.post("/signup")
async def signup_post(  # noqa: C901, PLR0912, PLR0913, PLR0915
    request: Request,
    username: Annotated[str, Form(...)],
    password: Annotated[str, Form(...)],
    password2: Annotated[str, Form(...)],
    email: Annotated[str, Form(...)],
    tests_repo: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form()] = None,
    g_recaptcha_response: Annotated[
        str | None,
        Form(alias="g-recaptcha-response"),
    ] = None,
) -> Response:
    """Handle signup form submission."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    if not _validate_csrf(request=request, session=session, form_token=csrf_token):
        session.flash("CSRF validation failed", "error")
        response = _render_signup(request=request, session=session, userdb=userdb)
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    tests_repo_value = (tests_repo or "").strip()
    username_value = username.strip()
    password_value = password.strip()
    password2_value = password2.strip()
    email_value = email.strip()

    errors: list[str] = []

    strong_password, password_err = password_strength(
        password_value,
        username_value,
        email_value,
    )
    if not strong_password:
        errors.append("Error! Weak password: " + password_err)
    if password_value != password2_value:
        errors.append("Error! Matching verify password required")
    email_is_valid, validated_email = email_valid(email_value)
    if not email_is_valid:
        errors.append("Error! Invalid email: " + validated_email)
    if len(username_value) == 0:
        errors.append("Error! Username required")
    if not username_value.isalnum():
        errors.append("Error! Alphanumeric username required")

    try:
        validate(union(github_repo, ""), tests_repo_value, "tests_repo")
    except ValidationError as exc:
        errors.append(f"Error! Invalid tests repo {tests_repo_value}: {exc!s}")

    if errors:
        for error in errors:
            session.flash(error, "error")
        response = _render_signup(request=request, session=session, userdb=userdb)
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    secret = os.environ.get("FISHTEST_CAPTCHA_SECRET")
    if secret:
        payload = {
            "secret": secret,
            "response": g_recaptcha_response or "",
            "remoteip": request.client.host if request.client else "",
        }
        try:
            response_json = requests.post(  # noqa: ASYNC210
                "https://www.google.com/recaptcha/api/siteverify",
                data=payload,
                timeout=HTTP_TIMEOUT,
            ).json()
        except (requests.RequestException, ValueError):
            response_json = {"success": False}

        if not response_json.get("success", False):
            session.flash("Captcha failed", "error")
            response = _render_signup(request=request, session=session, userdb=userdb)
            commit_session(
                response=response,
                session=session,
                remember=False,
                secure=is_https(request),
            )
            return response

    result = userdb.create_user(
        username=username_value,
        password=password_value,
        email=validated_email if email_is_valid else email_value,
        tests_repo=tests_repo_value,
    )

    if result is None:
        session.flash("Error! Invalid username or password", "error")
        response = _render_signup(request=request, session=session, userdb=userdb)
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    if not result:
        session.flash("Username or email is already registered", "error")
        response = _render_signup(request=request, session=session, userdb=userdb)
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    session.flash(
        "Account created! "
        "To avoid spam, a person will now manually approve your new account. "
        "This is usually quick but sometimes takes a few hours. "
        "Thank you for contributing!",
    )
    response = RedirectResponse(url="/login", status_code=303)
    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


@router.post("/login")
async def login_post(
    request: Request,
    username: Annotated[str, Form(...)],
    password: Annotated[str, Form(...)],
    stay_logged_in: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form()] = None,
) -> Response:
    """Authenticate and set the cookie session."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    if not _validate_csrf(request=request, session=session, form_token=csrf_token):
        session.flash("CSRF validation failed", "error")
        response = _render_login(request=request, session=session, userdb=userdb)
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    token = userdb.authenticate(username, password)
    if isinstance(token, dict) and "error" not in token:
        session.data["user"] = username
        session.new_csrf_token()
        session.dirty = True

        came_from = request.query_params.get("came_from")
        if not came_from:
            referer = request.headers.get("referer")
            came_from = referer or "/"
        if came_from == str(request.url):
            came_from = "/"
        next_page = request.query_params.get("next") or came_from

        remember = stay_logged_in is not None
        response = RedirectResponse(url=next_page, status_code=303)
        commit_session(
            response=response,
            session=session,
            remember=remember,
            secure=is_https(request),
        )
        return response

    message = "Login failed"
    if isinstance(token, dict):
        maybe = token.get("error")
        # Only surface the pending-account message verbatim; keep other auth
        # failures as a generic "Login failed".
        if isinstance(maybe, str) and "Account pending for user:" in maybe:
            message = maybe + (
                " . If you recently registered to fishtest, "
                "a person will now manually approve your new account, "
                "to avoid spam. "
                "This is usually quick, but sometimes takes a few hours. "
                "Thank you!"
            )

    session.flash(message, "error")
    response = _render_login(request=request, session=session, userdb=userdb)
    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


@router.post("/logout")
async def logout_post(request: Request) -> RedirectResponse:
    """Clear session and redirect to /tests."""
    session = load_session(request)

    form: FormData = await request.form()
    form_token = form.get("csrf_token")
    if not isinstance(form_token, str):
        form_token = None

    if not _validate_csrf(request=request, session=session, form_token=form_token):
        # Explicit rejection so JS can report an error.
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    response = RedirectResponse(url="/tests", status_code=303)
    clear_session_cookie(response=response, secure=is_https(request))
    return response
