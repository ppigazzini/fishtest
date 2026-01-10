"""FastAPI UI route for the SPRT calculator.

Ports Pyramid route:
- GET `/sprt_calc`

Renders the existing `sprt_calc.mak` template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.template_request import TemplateRequest
from fishtest.views.common import authenticated_user, is_https

if TYPE_CHECKING:
    from fishtest.userdb import UserDb


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()


@router.get("/sprt_calc", response_class=HTMLResponse)
async def sprt_calc(request: Request) -> HTMLResponse:
    """Render the SPRT calculator page."""
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
        template_name="sprt_calc.mak",
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
