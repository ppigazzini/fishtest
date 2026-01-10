"""FastAPI UI routes for user management.

Ports Pyramid route:
- GET `/user_management`

Renders the existing `user_management.mak` template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.template_request import TemplateRequest
from fishtest.views.common import authenticated_user, is_https

if TYPE_CHECKING:
    from fishtest.userdb import UserDb


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()


def _is_approver(*, userdb: UserDb, username: str | None) -> bool:
    if not username:
        return False
    groups = userdb.get_user_groups(username) or []
    return "group:approvers" in groups


def _get_idle_users(
    *,
    users: list[dict[str, object]],
    userdb: UserDb,
) -> list[dict[str, object]]:
    idle: dict[str, dict[str, object]] = {}
    for user in users:
        username = user.get("username")
        if isinstance(username, str):
            idle[username] = user

    for cached in userdb.user_cache.find():
        cached_username = cached.get("username")
        if isinstance(cached_username, str):
            idle.pop(cached_username, None)

    return list(idle.values())


def _render(
    *,
    request: Request,
    template_name: str,
    context: dict[str, object],
) -> HTMLResponse:
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

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
        template_name=template_name,
        context={"request": template_request, **context},
    )

    response = HTMLResponse(rendered.html)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Expires"] = "0"

    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


@router.get("/user_management", response_class=HTMLResponse)
async def user_management(request: Request) -> Response:
    """Render the user management page (approvers only)."""
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    viewer = authenticated_user(session)
    if not _is_approver(userdb=userdb, username=viewer):
        session.flash("You cannot view user management", "error")
        response = RedirectResponse(url="/tests", status_code=303)
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    users = list(userdb.get_users())

    context: dict[str, object] = {
        "all_users": users,
        "pending_users": userdb.get_pending(),
        "blocked_users": userdb.get_blocked(),
        "approvers_users": [
            user for user in users if "group:approvers" in user.get("groups", [])
        ],
        "idle_users": _get_idle_users(users=users, userdb=userdb),
    }

    return _render(
        request=request,
        template_name="user_management.mak",
        context=context,
    )
