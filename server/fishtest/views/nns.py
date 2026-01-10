"""FastAPI UI routes for NN listings.

Ports Pyramid route:
- GET `/nns`

Renders the existing `nns.mak` template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.template_request import TemplateRequest
from fishtest.views.common import authenticated_user, is_https
from fishtest.views.tests import _pagination

if TYPE_CHECKING:
    from fishtest.rundb import RunDb
    from fishtest.userdb import UserDb


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()

_PAGE_SIZE = 25


def _truthy_param(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized not in {"", "0", "false", "no", "off"}


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


@router.get("/nns", response_class=HTMLResponse)
async def nns(request: Request) -> HTMLResponse:
    """Render the neural network listing page."""
    rundb = cast("RunDb", request.app.state.rundb)

    user = request.query_params.get("user", "")
    network_name = request.query_params.get("network_name", "")
    master_only = _truthy_param(request.query_params.get("master_only"))

    page_param = request.query_params.get("page", "")
    page_idx = max(0, int(page_param) - 1) if page_param.isdigit() else 0

    nns_list, num_nns = rundb.get_nns(
        user=user,
        network_name=network_name,
        master_only=master_only,
        limit=_PAGE_SIZE,
        skip=page_idx * _PAGE_SIZE,
    )

    query_params = ""
    if user:
        query_params += f"&user={user}"
    if network_name:
        query_params += f"&network_name={network_name}"
    if master_only:
        query_params += f"&master_only={master_only}"

    pages = _pagination(
        page_idx=page_idx,
        num=num_nns,
        page_size=_PAGE_SIZE,
        query_params=query_params,
    )

    context: dict[str, object] = {
        "nns": nns_list,
        "pages": pages,
        "master_only": request.cookies.get("master_only") == "true",
    }

    return _render(request=request, template_name="nns.mak", context=context)
