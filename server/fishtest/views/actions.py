"""FastAPI UI routes for the actions log.

Ports Pyramid route:
- GET `/actions`

Renders the existing `actions.mak` template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.views.auth import TemplateRequest
from fishtest.views.common import authenticated_user, is_https
from fishtest.views.tests import _pagination

if TYPE_CHECKING:
    from fishtest.actiondb import ActionDb
    from fishtest.userdb import UserDb


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()

_PAGE_SIZE = 25


def _is_approver(*, userdb: UserDb, username: str | None) -> bool:
    if not username:
        return False
    groups = userdb.get_user_groups(username) or []
    return "group:approvers" in groups


# Different LOCALES may have different quotation marks.
# See https://op.europa.eu/en/web/eu-vocabularies/formex/physical-specifications/character-encoding/quotation-marks
_QUOTATION_MARKS = "".join(
    chr(c)
    for c in (
        0x0022,
        0x0027,
        0x00AB,
        0x00BB,
        0x2018,
        0x2019,
        0x201A,
        0x201B,
        0x201C,
        0x201D,
        0x201E,
        0x201F,
        0x2039,
        0x203A,
    )
)
_QUOTATION_MARKS_TRANSLATION = str.maketrans(
    _QUOTATION_MARKS,
    len(_QUOTATION_MARKS) * '"',
)


def _sanitize_quotation_marks(text: str) -> str:
    return text.translate(_QUOTATION_MARKS_TRANSLATION)


def _parse_optional_float(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_optional_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _actions_query_params(*, values: dict[str, object]) -> str:
    query_params = ""

    username = str(values.get("username", ""))
    search_action = str(values.get("search_action", ""))
    text = str(values.get("text", ""))
    run_id = str(values.get("run_id", ""))

    before = values.get("before")
    max_actions = values.get("max_actions")
    raw_num_actions = values.get("num_actions", 0)
    num_actions = raw_num_actions if isinstance(raw_num_actions, int) else 0

    if username:
        query_params += f"&user={username}"
    if search_action:
        query_params += f"&action={search_action}"
    if text:
        query_params += f"&text={text}"
    if isinstance(max_actions, int) and max_actions:
        # Match Pyramid behavior (even though it looks like a typo there).
        query_params += f"&max_actions={num_actions}"
    if isinstance(before, float) and before:
        query_params += f"&before={before}"
    if run_id:
        query_params += f"&run_id={run_id}"
    return query_params


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


@router.get("/actions", response_class=HTMLResponse)
async def actions(request: Request) -> HTMLResponse:
    """Render the actions log page."""
    actiondb = cast("ActionDb", request.app.state.actiondb)
    userdb = cast("UserDb", request.app.state.userdb)

    session = load_session(request)
    viewer = authenticated_user(session)

    search_action = request.query_params.get("action", "")
    username = request.query_params.get("user", "")
    text = _sanitize_quotation_marks(request.query_params.get("text", ""))
    run_id = request.query_params.get("run_id", "")

    before = _parse_optional_float(request.query_params.get("before"))
    max_actions = _parse_optional_int(request.query_params.get("max_actions"))

    page_param = request.query_params.get("page", "")
    page_idx = max(0, int(page_param) - 1) if page_param.isdigit() else 0

    actions_list, num_actions = actiondb.get_actions(
        username=username,
        action=search_action,
        text=text,
        skip=page_idx * _PAGE_SIZE,
        limit=_PAGE_SIZE,
        utc_before=before,
        max_actions=max_actions,
        run_id=run_id,
    )

    query_params = _actions_query_params(
        values={
            "username": username,
            "search_action": search_action,
            "text": text,
            "before": before,
            "max_actions": max_actions,
            "run_id": run_id,
            "num_actions": num_actions,
        },
    )

    pages = _pagination(
        page_idx=page_idx,
        num=num_actions,
        page_size=_PAGE_SIZE,
        query_params=query_params,
    )

    context: dict[str, object] = {
        "actions": actions_list,
        "approver": _is_approver(userdb=userdb, username=viewer),
        "pages": pages,
        "action_param": search_action,
        "username_param": username,
        "text_param": text,
        "run_id_param": run_id,
    }

    return _render(request=request, template_name="actions.mak", context=context)
