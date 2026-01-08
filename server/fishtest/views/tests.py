"""FastAPI UI routes for test listings.

Ports the Pyramid `/tests` family using the existing Mako templates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.views.auth import TemplateRequest
from fishtest.views.common import authenticated_user, is_https

if TYPE_CHECKING:
    from fishtest.rundb import RunDb
    from fishtest.userdb import UserDb


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()

PAGE_SIZE: Final[int] = 25
APPROVERS_GROUP: Final[str] = "group:approvers"

PAGINATION_VISIBLE_EDGE: Final[int] = 5
PAGINATION_ACTIVE_EDGE: Final[int] = 4
PAGINATION_NEARBY: Final[int] = 2


@dataclass(frozen=True)
class _FinishedRunsParams:
    username: str
    success_only: bool
    yellow_only: bool
    ltc_only: bool
    page_idx: int


def _is_approver(*, userdb: UserDb, username: str) -> bool:
    groups = userdb.get_user_groups(username) or []
    return APPROVERS_GROUP in groups


def _pagination(
    *,
    page_idx: int,
    num: int,
    page_size: int,
    query_params: str,
) -> list[dict[str, object]]:
    pages: list[dict[str, object]] = [
        {
            "idx": "Prev",
            "url": f"?page={page_idx}",
            "state": "disabled" if page_idx == 0 else "",
        },
    ]

    last_idx = (num - 1) // page_size
    for idx, _ in enumerate(range(0, num, page_size)):
        if (
            idx < 1
            or (idx < PAGINATION_VISIBLE_EDGE and page_idx < PAGINATION_ACTIVE_EDGE)
            or abs(idx - page_idx) < PAGINATION_NEARBY
            or (
                idx > last_idx - PAGINATION_VISIBLE_EDGE
                and page_idx > last_idx - PAGINATION_ACTIVE_EDGE
            )
        ):
            pages.append(
                {
                    "idx": idx + 1,
                    "url": f"?page={idx + 1}" + query_params,
                    "state": "active" if page_idx == idx else "",
                },
            )
        elif pages[-1]["idx"] != "...":
            pages.append({"idx": "...", "url": "", "state": "disabled"})

    pages.append(
        {
            "idx": "Next",
            "url": f"?page={page_idx + 2}" + query_params,
            "state": "disabled" if page_idx >= (num - 1) // page_size else "",
        },
    )
    return pages


def _parse_finished_runs_params(
    request: Request,
    *,
    username: str,
) -> _FinishedRunsParams:
    page_param = request.query_params.get("page", "")
    page_idx = max(0, int(page_param) - 1) if page_param.isdigit() else 0

    success_only = bool(request.query_params.get("success_only", False))
    yellow_only = bool(request.query_params.get("yellow_only", False))
    ltc_only = bool(request.query_params.get("ltc_only", False))

    return _FinishedRunsParams(
        username=username,
        success_only=success_only,
        yellow_only=yellow_only,
        ltc_only=ltc_only,
        page_idx=page_idx,
    )


def _get_paginated_finished_runs(
    *,
    rundb: RunDb,
    params: _FinishedRunsParams,
) -> dict[str, object]:
    finished_runs, num_finished_runs = rundb.get_finished_runs(
        username=params.username,
        success_only=params.success_only,
        yellow_only=params.yellow_only,
        ltc_only=params.ltc_only,
        skip=params.page_idx * PAGE_SIZE,
        limit=PAGE_SIZE,
    )

    query_params = ""
    if params.success_only:
        query_params += "&success_only=1"
    if params.yellow_only:
        query_params += "&yellow_only=1"
    if params.ltc_only:
        query_params += "&ltc_only=1"

    pages = _pagination(
        page_idx=params.page_idx,
        num=num_finished_runs,
        page_size=PAGE_SIZE,
        query_params=query_params,
    )

    failed_runs: list[object] = []
    if params.page_idx == 0:
        failed_runs = [run for run in finished_runs if run.get("failed")]

    return {
        "finished_runs": finished_runs,
        "finished_runs_pages": pages,
        "num_finished_runs": num_finished_runs,
        "failed_runs": failed_runs,
        "page_idx": params.page_idx,
    }


def _homepage_results(*, rundb: RunDb, request: Request) -> dict[str, object]:
    runs, pending_hours, cores, nps, games_per_minute, machines_count = (
        rundb.aggregate_unfinished_runs()
    )

    finished = _get_paginated_finished_runs(
        rundb=rundb,
        params=_parse_finished_runs_params(request, username=""),
    )

    return {
        **finished,
        "runs": runs,
        "machines_count": machines_count,
        "pending_hours": f"{pending_hours:.1f}",
        "cores": cores,
        "nps": nps,
        "games_per_minute": int(games_per_minute),
    }


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


@router.get("/tests", response_class=HTMLResponse)
async def tests(request: Request) -> HTMLResponse:
    """Render the main tests queue page."""
    rundb = cast("RunDb", request.app.state.rundb)

    page_param = request.query_params.get("page", "")
    if page_param.isdigit() and int(page_param) > 1:
        finished = _get_paginated_finished_runs(
            rundb=rundb,
            params=_parse_finished_runs_params(request, username=""),
        )
        return _render(request=request, template_name="tests.mak", context=finished)

    last_tests = _homepage_results(rundb=rundb, request=request)
    context: dict[str, object] = {
        **last_tests,
        "machines_shown": request.cookies.get("machines_state") == "Hide",
    }
    return _render(request=request, template_name="tests.mak", context=context)


@router.get("/tests/finished", response_class=HTMLResponse)
async def tests_finished(request: Request) -> HTMLResponse:
    """Render the finished tests listing."""
    rundb = cast("RunDb", request.app.state.rundb)
    finished = _get_paginated_finished_runs(
        rundb=rundb,
        params=_parse_finished_runs_params(request, username=""),
    )
    return _render(
        request=request,
        template_name="tests_finished.mak",
        context=finished,
    )


@router.get("/tests/machines", response_class=HTMLResponse)
async def tests_machines(request: Request) -> HTMLResponse:
    """Return the machines table HTML (used by the `/tests` page)."""
    rundb = cast("RunDb", request.app.state.rundb)
    rendered = render_template(
        lookup=TEMPLATE_LOOKUP,
        template_name="machines.mak",
        context={"machines_list": rundb.get_machines()},
    )
    response = HTMLResponse(rendered.html)
    response.headers["Cache-Control"] = "max-age=10"
    return response


@router.get("/tests/user/{username}", response_class=HTMLResponse)
async def tests_user(request: Request, username: str) -> HTMLResponse:
    """Render tests for a specific user."""
    rundb = cast("RunDb", request.app.state.rundb)
    userdb = cast("UserDb", request.app.state.userdb)

    user_data = userdb.get_user(username)
    if user_data is None:
        raise HTTPException(status_code=404)

    page_param = request.query_params.get("page", "")

    finished = _get_paginated_finished_runs(
        rundb=rundb,
        params=_parse_finished_runs_params(request, username=username),
    )

    context: dict[str, object] = {
        **finished,
        "username": username,
    }

    if not page_param.isdigit() or int(page_param) <= 1:
        context["runs"] = rundb.aggregate_unfinished_runs(username=username)[0]

    viewer = authenticated_user(load_session(request))
    context["is_approver"] = bool(viewer) and _is_approver(
        userdb=userdb,
        username=viewer,
    )

    return _render(request=request, template_name="tests_user.mak", context=context)
