"""FastAPI UI route for the GitHub rate limit page.

Ports Pyramid `rate_limits` view (template `rate_limits.mak`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.template_request import TemplateRequest
from fishtest.views.common import authenticated_user, is_https

if TYPE_CHECKING:
    from fishtest.userdb import UserDb

router = APIRouter(tags=["ui"], include_in_schema=False)

TEMPLATE_LOOKUP: Final = default_template_lookup()


def _render(*, request: Request, template_name: str) -> HTMLResponse:
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
        context={"request": template_request},
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


@router.get("/rate_limits", response_class=HTMLResponse)
async def rate_limits(request: Request) -> HTMLResponse:
    """Render the rate limits page."""
    return _render(request=request, template_name="rate_limits.mak")
