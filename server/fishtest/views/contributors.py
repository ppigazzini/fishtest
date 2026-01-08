"""FastAPI UI routes for contributors listings.

Ports Pyramid routes:
- GET `/contributors`
- GET `/contributors/monthly`

Renders the existing `contributors.mak` template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.views.auth import TemplateRequest
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


@router.get("/contributors", response_class=HTMLResponse)
async def contributors(request: Request) -> HTMLResponse:
    """Render the all-time contributors page."""
    userdb = cast("UserDb", request.app.state.userdb)

    users_list = list(userdb.user_cache.find())
    users_list.sort(key=lambda u: u.get("cpu_hours", 0), reverse=True)

    viewer = authenticated_user(load_session(request))

    context: dict[str, object] = {
        "users": users_list,
        "approver": _is_approver(userdb=userdb, username=viewer),
    }

    return _render(request=request, template_name="contributors.mak", context=context)


@router.get("/contributors/monthly", response_class=HTMLResponse)
async def contributors_monthly(request: Request) -> HTMLResponse:
    """Render the monthly contributors page."""
    userdb = cast("UserDb", request.app.state.userdb)

    users_list = list(userdb.top_month.find())
    users_list.sort(key=lambda u: u.get("cpu_hours", 0), reverse=True)

    viewer = authenticated_user(load_session(request))

    context: dict[str, object] = {
        "users": users_list,
        "approver": _is_approver(userdb=userdb, username=viewer),
    }

    return _render(request=request, template_name="contributors.mak", context=context)
