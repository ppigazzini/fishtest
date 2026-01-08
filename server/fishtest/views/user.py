"""FastAPI-native `/user` and `/user/{username}` UI routes.

This ports the Pyramid user/profile view to FastAPI while keeping the existing
Mako templates.

Important: this uses the same (currently plaintext) password comparison logic as
Pyramid; password hashing changes are explicitly out of scope.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import fishtest.github_api as gh
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fishtest.cookie_session import CookieSession, commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.schemas import github_repo
from fishtest.util import email_valid, format_date, password_strength
from fishtest.views.auth import TemplateRequest
from fishtest.views.common import authenticated_user, is_https
from vtjson import ValidationError, union, validate

if TYPE_CHECKING:
    from fishtest.actiondb import ActionDb
    from fishtest.userdb import UserDb
    from starlette.datastructures import FormData


class _UserDbThrottle(Protocol):
    last_blocked_time: int
    last_pending_time: int


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()


@dataclass(frozen=True)
class _UserPage:
    user_data: dict[str, object]
    profile: bool
    extract_repo_from_link: str
    hours: int
    limit: int


@dataclass(frozen=True)
class _ProfileUpdate:
    old_password: str
    new_password: str
    new_password_verify: str
    new_email: str
    tests_repo: str


def _parse_profile_update(form: FormData) -> _ProfileUpdate:
    return _ProfileUpdate(
        old_password=str(form.get("old_password", "")).strip(),
        new_password=str(form.get("password", "")).strip(),
        new_password_verify=str(form.get("password2", "")).strip(),
        new_email=str(form.get("email", "")).strip(),
        tests_repo=str(form.get("tests_repo", "")).strip(),
    )


def _check_old_password(
    *,
    session: CookieSession,
    user_data: dict[str, object],
    old_password: str,
) -> bool:
    # Temporary comparison until passwords are hashed.
    if old_password == str(user_data.get("password", "")).strip():
        return True
    session.flash("Invalid password!", "error")
    return False


def _maybe_update_password(
    *,
    session: CookieSession,
    user_data: dict[str, object],
    username: str,
    update: _ProfileUpdate,
) -> bool:
    if not update.new_password:
        return True

    if update.new_password != update.new_password_verify:
        session.flash("Error! Matching verify password required", "error")
        return False

    strong_password, password_err = password_strength(
        update.new_password,
        username,
        str(user_data.get("email", "")),
        (update.new_email if update.new_email else None),
    )
    if not strong_password:
        session.flash("Error! Weak password: " + password_err, "error")
        return False

    user_data["password"] = update.new_password
    session.flash("Success! Password updated")
    return True


def _update_tests_repo(
    *,
    session: CookieSession,
    user_data: dict[str, object],
    tests_repo: str,
) -> bool:
    try:
        validate(union(github_repo, ""), tests_repo, "tests_repo")
    except ValidationError as exc:
        session.flash(f"Error! Invalid test repo {tests_repo}: {exc}", "error")
        return False

    user_data["tests_repo"] = tests_repo
    return True


def _maybe_update_email(
    *,
    session: CookieSession,
    user_data: dict[str, object],
    new_email: str,
) -> bool:
    if not new_email or str(user_data.get("email", "")) == new_email:
        return True

    email_is_valid, validated_email = email_valid(new_email)
    if not email_is_valid:
        session.flash("Error! Invalid email: " + str(validated_email), "error")
        return False

    user_data["email"] = validated_email
    session.flash("Success! Email updated")
    return True


def _is_approver(*, userdb: UserDb, username: str) -> bool:
    groups = userdb.get_user_groups(username) or []
    return "group:approvers" in groups


def _validate_csrf(
    *,
    request: Request,
    session: CookieSession,
    csrf_token: str | None,
) -> None:
    expected = session.get_csrf_token()
    token = request.headers.get("x-csrf-token") or csrf_token
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def _redirect_with_session(
    *,
    request: Request,
    session: CookieSession,
    url: str,
) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=303)
    commit_session(
        response=response,
        session=session,
        remember=True,
        secure=is_https(request),
    )
    return response


def _render_user(
    *,
    request: Request,
    session: CookieSession,
    userdb: UserDb,
    page: _UserPage,
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
        template_name="user.mak",
        context={
            "request": template_request,
            "format_date": format_date,
            "user": page.user_data,
            "limit": page.limit,
            "hours": page.hours,
            "profile": page.profile,
            "extract_repo_from_link": page.extract_repo_from_link,
        },
    )

    response = HTMLResponse(rendered.html)
    commit_session(
        response=response,
        session=session,
        remember=True,
        secure=is_https(request),
    )
    return response


def _extract_repo_from_link(tests_repo: str) -> str:
    if not tests_repo:
        return ""
    user, repo = gh.parse_repo(tests_repo)
    return f"{user}/{repo}"


def _user_cpu_hours(*, userdb: UserDb, username: str) -> int:
    cached = userdb.user_cache.find_one({"username": username})
    if cached is None:
        return 0
    value = cached.get("cpu_hours")
    return int(value) if isinstance(value, (int, float)) else 0


@router.get("/user", response_class=HTMLResponse)
async def profile_get(request: Request) -> Response:
    """Show logged-in user's profile page."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    userid = authenticated_user(session)
    if not userid:
        session.flash("Please login")
        response = RedirectResponse(
            url=f"/login?next={request.url.path}",
            status_code=303,
        )
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    user_data = userdb.get_user(userid)
    if user_data is None:
        raise HTTPException(status_code=404)

    return _render_user(
        request=request,
        session=session,
        userdb=userdb,
        page=_UserPage(
            user_data=user_data,
            profile=True,
            extract_repo_from_link=_extract_repo_from_link(
                str(user_data.get("tests_repo", "")),
            ),
            hours=_user_cpu_hours(userdb=userdb, username=userid),
            limit=int(userdb.get_machine_limit(userid)),
        ),
    )


@router.post("/user")
async def profile_post(request: Request) -> RedirectResponse:
    """Handle profile updates.

    Mirrors the Pyramid behavior: after processing, redirect to /tests.
    """
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    userid = authenticated_user(session)
    redirect_url = "/tests"

    if not userid:
        session.flash("Please login")
        redirect_url = "/login"
        return _redirect_with_session(
            request=request,
            session=session,
            url=redirect_url,
        )

    form: FormData = await request.form()
    raw_csrf = form.get("csrf_token")
    _validate_csrf(
        request=request,
        session=session,
        csrf_token=raw_csrf if isinstance(raw_csrf, str) else None,
    )

    user_data = userdb.get_user(userid)
    if user_data is None:
        raise HTTPException(status_code=404)

    if "user" in form:
        update = _parse_profile_update(form)
        ok = _check_old_password(
            session=session,
            user_data=user_data,
            old_password=update.old_password,
        )
        ok = ok and _maybe_update_password(
            session=session,
            user_data=user_data,
            username=userid,
            update=update,
        )
        ok = ok and _update_tests_repo(
            session=session,
            user_data=user_data,
            tests_repo=update.tests_repo,
        )
        ok = ok and _maybe_update_email(
            session=session,
            user_data=user_data,
            new_email=update.new_email,
        )

        if ok:
            userdb.save_user(user_data)

    return _redirect_with_session(request=request, session=session, url=redirect_url)


@router.get("/user/{username}", response_class=HTMLResponse)
async def user_get(request: Request, username: str) -> Response:
    """Show user details for approvers, otherwise redirect."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    userid = authenticated_user(session)
    if not userid:
        session.flash("Please login")
        response = RedirectResponse(
            url=f"/login?next={request.url.path}",
            status_code=303,
        )
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    if not _is_approver(userdb=userdb, username=userid):
        session.flash("You cannot inspect users", "error")
        return _redirect_with_session(request=request, session=session, url="/tests")

    user_data = userdb.get_user(username)
    if user_data is None:
        raise HTTPException(status_code=404)

    return _render_user(
        request=request,
        session=session,
        userdb=userdb,
        page=_UserPage(
            user_data=user_data,
            profile=False,
            extract_repo_from_link=_extract_repo_from_link(
                str(user_data.get("tests_repo", "")),
            ),
            hours=_user_cpu_hours(userdb=userdb, username=username),
            limit=int(userdb.get_machine_limit(username)),
        ),
    )


@router.post("/user/{username}")
async def user_post(request: Request, username: str) -> RedirectResponse:
    """Handle approver actions for a user (pending/block)."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    userid = authenticated_user(session)
    if not userid:
        session.flash("Please login")
        return _redirect_with_session(request=request, session=session, url="/login")

    form: FormData = await request.form()
    raw_csrf = form.get("csrf_token")
    _validate_csrf(
        request=request,
        session=session,
        csrf_token=raw_csrf if isinstance(raw_csrf, str) else None,
    )

    if not _is_approver(userdb=userdb, username=userid):
        session.flash("You cannot inspect users", "error")
        return _redirect_with_session(request=request, session=session, url="/tests")

    user_data = userdb.get_user(username)
    if user_data is None:
        raise HTTPException(status_code=404)

    if "user" not in form:
        return _redirect_with_session(request=request, session=session, url="/tests")

    if "blocked" in form and str(form.get("blocked", "")).isdigit():
        user_data["blocked"] = bool(int(str(form.get("blocked"))))
        session.flash(
            (
                ("Blocked" if user_data.get("blocked") else "Unblocked")
                + " user "
                + username
            ),
        )
        cast("_UserDbThrottle", userdb).last_blocked_time = 0
        userdb.save_user(user_data)
        actiondb.block_user(
            username=userid,
            user=username,
            message="blocked" if user_data.get("blocked") else "unblocked",
        )
        return _redirect_with_session(request=request, session=session, url="/tests")

    if "pending" in form and bool(user_data.get("pending")):
        cast("_UserDbThrottle", userdb).last_pending_time = 0
        if str(form.get("pending")) == "0":
            user_data["pending"] = False
            userdb.save_user(user_data)
            actiondb.accept_user(
                username=userid,
                user=username,
                message="accepted",
            )
        else:
            userdb.remove_user(user_data, userid)
        return _redirect_with_session(request=request, session=session, url="/tests")

    return _redirect_with_session(request=request, session=session, url="/tests")
