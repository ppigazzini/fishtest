"""FastAPI UI routes for managing tests (create/modify/admin).

Ports Pyramid routes:
- GET/POST `/tests/run`
- POST `/tests/modify`
- POST `/tests/stop`
- POST `/tests/approve`
- POST `/tests/purge`
- POST `/tests/delete`

These endpoints keep the existing HTML templates and DB behavior, including
CSRF validation, flash messages, and approver permissions.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, cast

import fishtest.github_api as gh
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fishtest.cookie_session import CookieSession, commit_session, load_session
from fishtest.csrf import csrf_is_valid, csrf_or_403, csrf_token_from_form
from fishtest.mako import default_template_lookup, render_template
from fishtest.run_cache import Prio
from fishtest.run_form import (
    get_master_info,
    new_run_message,
    update_nets,
    validate_form,
)
from fishtest.schemas import RUN_VERSION, is_undecided, runs_schema
from fishtest.template_request import TemplateRequest
from fishtest.util import is_sprt_ltc_data
from fishtest.views.common import authenticated_user, is_https
from vtjson import validate

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fishtest.actiondb import ActionDb
    from fishtest.rundb import RunDb
    from fishtest.userdb import UserDb
    from starlette.datastructures import FormData


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()

APPROVERS_GROUP: Final[str] = "group:approvers"
RUN_TOO_OLD_DAYS: Final[int] = 30
MAX_GAMES: Final[int] = 3_200_000

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Viewer:
    username: str | None
    is_approver: bool


def _viewer_from_request(*, request: Request) -> _Viewer:
    session = load_session(request)
    username = authenticated_user(session)
    if not username:
        return _Viewer(username=None, is_approver=False)

    userdb = cast("UserDb", request.app.state.userdb)
    groups = userdb.get_user_groups(username) or []
    return _Viewer(username=username, is_approver=APPROVERS_GROUP in groups)


def _validate_csrf(
    *,
    request: Request,
    session: CookieSession,
    form_token: str | None,
) -> bool:
    return csrf_is_valid(request=request, session=session, form_token=form_token)


def _redirect(
    *,
    request: Request,
    session: CookieSession,
    url: str,
) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=303)
    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


def _render_tests_run(
    *,
    request: Request,
    session: CookieSession,
    userdb: UserDb,
    context: Mapping[str, object],
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
        template_name="tests_run.mak",
        context={"request": template_request, **dict(context)},
    )
    response = HTMLResponse(rendered.html)
    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


def _require_login(*, session: CookieSession) -> str | None:
    username = authenticated_user(session)
    if username:
        return username

    session.flash("Please login")
    return None


def _form_as_str_dict(form: FormData) -> dict[str, str]:
    data: dict[str, str] = {}
    for key, value in form.multi_items():
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            data[key] = value
    return data


@dataclass
class _CompatSession:
    """Adapter providing `flash()` used by Pyramid helpers."""

    session: CookieSession

    def flash(self, message: str, queue: str | None = None) -> None:
        self.session.flash(message, queue)


@dataclass
class _CompatRequest:
    """Minimal request adapter for reusing `fishtest.run_form.validate_form()`."""

    POST: Mapping[str, str]
    authenticated_userid: str
    session: _CompatSession
    userdb: UserDb
    rundb: RunDb
    host_url: str


def _validate_modify(
    *,
    session: CookieSession,
    post: Mapping[str, str],
    run: dict[str, Any],
) -> None:
    now = datetime.now(UTC)
    start_time = run.get("start_time")
    if (
        not isinstance(start_time, datetime)
        or (now - start_time).days > RUN_TOO_OLD_DAYS
    ):
        session.flash("Run too old to be modified", "error")
        raise ValueError

    if "num-games" not in post:
        session.flash("Unable to modify with no number of games!", "error")
        raise ValueError

    fields = (post.get("priority"), post.get("num-games"), post.get("throughput"))
    if not all(
        isinstance(value, str) and value and value.replace("-", "").isdigit()
        for value in fields
    ):
        session.flash("Bad values!", "error")
        raise ValueError

    num_games = int(post["num-games"])
    args = cast("dict[str, Any]", run.get("args", {}))

    if (
        num_games > int(args.get("num_games", 0))
        and "sprt" not in args
        and "spsa" not in args
    ):
        session.flash("Unable to modify number of games in a fixed game test!", "error")
        raise ValueError

    if "spsa" in args and num_games != int(args.get("num_games", 0)):
        session.flash(
            "Unable to modify number of games for SPSA tests, "
            "SPSA hyperparams are based off the initial number of games",
            "error",
        )
        raise ValueError

    if num_games > MAX_GAMES:
        session.flash(f"Number of games must be <= {MAX_GAMES}", "error")
        raise ValueError


def _del_tasks(run: dict[str, Any]) -> dict[str, Any]:
    shallow = copy.copy(run)
    shallow.pop("tasks", None)
    return copy.deepcopy(shallow)


def _can_modify_run(*, viewer: _Viewer, run: dict[str, Any]) -> bool:
    if viewer.username is None:
        return False
    args = cast("dict[str, Any]", run.get("args", {}))
    return bool(viewer.is_approver or args.get("username") == viewer.username)


def _same_user(*, viewer: _Viewer, run: dict[str, Any]) -> bool:
    if viewer.username is None:
        return False
    args = cast("dict[str, Any]", run.get("args", {}))
    return args.get("username") == viewer.username


def _tests_run_context(
    *,
    rundb: RunDb,
    userdb: UserDb,
    username: str,
    rerun_id: str | None,
) -> dict[str, object]:
    run_args: dict[str, object] = {}
    rescheduled_from: str | None = None

    if rerun_id:
        run = rundb.get_run(rerun_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run_args = copy.deepcopy(cast("dict[str, object]", run.get("args", {})))
        if "spsa" in run_args and isinstance(run_args["spsa"], dict):
            spsa = cast("dict[str, object]", run_args["spsa"])
            try:
                a_param = _to_float(spsa.get("A"))
                num_games = _to_float(run_args.get("num_games"))
            except (TypeError, ValueError):
                pass
            else:
                if num_games:
                    spsa["A"] = round(1000 * 2 * a_param / num_games) / 1000
        rescheduled_from = rerun_id

    u = userdb.get_user(username)

    return {
        "args": run_args,
        "is_rerun": bool(run_args),
        "rescheduled_from": rescheduled_from,
        "tests_repo": str(u.get("tests_repo", "")),
        "master_info": get_master_info(ignore_rate_limit=True),
        "valid_books": rundb.books.keys(),
        "pt_info": rundb.pt_info,
    }


def _get_run_or_redirect(
    *,
    rundb: RunDb,
    session: CookieSession,
    request: Request,
    run_id: str,
) -> dict[str, Any] | RedirectResponse:
    run = rundb.get_run(run_id)
    if run is not None:
        return run
    session.flash("No run with this id", "error")
    return _redirect(request=request, session=session, url="/tests")


def _csrf_or_403(*, request: Request, session: CookieSession, form: FormData) -> None:
    csrf_or_403(
        request=request,
        session=session,
        form_token=csrf_token_from_form(form),
    )


def _form_str(form: FormData, key: str) -> str | None:
    value = form.get(key)
    return value if isinstance(value, str) and value else None


def _to_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise TypeError


def _needs_unapprove(
    *,
    viewer: _Viewer,
    run: dict[str, Any],
    throughput: int,
    priority: int,
) -> bool:
    if viewer.is_approver:
        return False
    if not bool(run.get("approved")):
        return False

    args = cast("dict[str, Any]", run.get("args", {}))
    old_throughput = int(args.get("throughput", 100))
    old_priority = int(args.get("priority", 0))
    return bool(
        throughput > max(old_throughput, 100)
        or priority > max(old_priority, 0)
        or bool(run.get("failed")),
    )


def _apply_modify(
    *,
    rundb: RunDb,
    viewer: _Viewer,
    run: dict[str, Any],
    post: Mapping[str, str],
) -> None:
    args = cast("dict[str, Any]", run.get("args", {}))
    with rundb.active_run_lock(run["_id"]):
        args["num_games"] = int(post["num-games"])
        args["priority"] = int(post["priority"])
        args["throughput"] = int(post["throughput"])
        args["auto_purge"] = bool(post.get("auto_purge"))
        if _same_user(viewer=viewer, run=run):
            info = post.get("info", "").strip()
            if info:
                args["info"] = info

    if is_undecided(run):
        rundb.set_active_run(run)


def _changed_fields_message(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    keys = ("priority", "num_games", "throughput", "auto_purge")
    before_args = cast("dict[str, Any]", before.get("args", {}))
    after_args = cast("dict[str, Any]", after.get("args", {}))
    return [
        f"{key.replace('_', '-')} changed from {before_args[key]} to {after_args[key]}"
        for key in keys
        if before_args.get(key) != after_args.get(key)
    ]


@router.get("/tests/run", response_class=HTMLResponse)
async def tests_run_get(request: Request) -> Response:
    """Render the create-test page (and reschedule prefill if `?id=` present)."""
    session = load_session(request)
    viewer = _viewer_from_request(request=request)
    if viewer.username is None:
        # keep it simple; login will redirect back via `next`.
        session.flash("Please login")
        next_path = request.url.path
        return _redirect(
            request=request,
            session=session,
            url=f"/login?next={next_path}",
        )

    rundb = cast("RunDb", request.app.state.rundb)
    userdb = cast("UserDb", request.app.state.userdb)

    rerun_id = request.query_params.get("id")
    username = viewer.username

    rundb.update_books()
    gh.update_official_master_sha()

    context = _tests_run_context(
        rundb=rundb,
        userdb=userdb,
        username=username,
        rerun_id=rerun_id,
    )
    return _render_tests_run(
        request=request,
        session=session,
        userdb=userdb,
        context=context,
    )


@router.post("/tests/run")
async def tests_run_post(request: Request) -> Response:
    """Submit a new test from the create-test form."""
    session = load_session(request)
    username = _require_login(session=session)
    if username is None:
        return _redirect(request=request, session=session, url="/login")

    form: FormData = await request.form()
    if not _validate_csrf(
        request=request,
        session=session,
        form_token=csrf_token_from_form(form),
    ):
        session.flash("CSRF validation failed", "error")
        return _redirect(request=request, session=session, url="/tests/run")

    rundb = cast("RunDb", request.app.state.rundb)
    userdb = cast("UserDb", request.app.state.userdb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    try:
        post = _form_as_str_dict(form)
        compat = _CompatRequest(
            POST=post,
            authenticated_userid=username,
            session=_CompatSession(session),
            userdb=userdb,
            rundb=rundb,
            host_url=str(request.base_url).rstrip("/"),
        )
        data = validate_form(compat)
        if is_sprt_ltc_data(data):
            data["info"] = "LTC: " + data["info"]

        run_id = rundb.new_run(**data)
        run = rundb.get_run(run_id)
        if run is None:
            session.flash("Internal error: created run missing", "error")
            return _redirect(request=request, session=session, url="/tests")

        actiondb.new_run(
            username=username,
            run=run,
            message=new_run_message(compat, run),
        )
        session.flash("The test was submitted to the queue. Please wait for approval.")
        return _redirect(
            request=request,
            session=session,
            url=f"/tests/view/{run_id}?follow=1",
        )
    except Exception as exc:  # noqa: BLE001
        session.flash(str(exc), "error")
        # fall back to re-rendering like Pyramid
        return await tests_run_get(request)


@router.post("/tests/modify")
async def tests_modify_post(request: Request) -> Response:
    """Modify an active run (priority/throughput/num-games/auto-purge/info)."""
    session = load_session(request)
    viewer = _viewer_from_request(request=request)
    username = _require_login(session=session)
    if username is None:
        return _redirect(request=request, session=session, url="/login")

    form: FormData = await request.form()
    _csrf_or_403(request=request, session=session, form=form)

    rundb = cast("RunDb", request.app.state.rundb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    run_id = _form_str(form, "run")
    if run_id is None:
        session.flash("No run with this id", "error")
        return _redirect(request=request, session=session, url="/tests")

    run_or_response = _get_run_or_redirect(
        rundb=rundb,
        session=session,
        request=request,
        run_id=run_id,
    )
    if isinstance(run_or_response, RedirectResponse):
        return run_or_response
    run = run_or_response

    if not _can_modify_run(viewer=viewer, run=run):
        session.flash("Unable to modify another user's run!", "error")
        return _redirect(request=request, session=session, url="/tests")

    post = _form_as_str_dict(form)
    try:
        _validate_modify(session=session, post=post, run=run)
    except ValueError:
        return _redirect(request=request, session=session, url="/tests")

    was_approved = bool(run.get("approved"))

    throughput = int(post["throughput"])
    priority = int(post["priority"])
    if _needs_unapprove(
        viewer=viewer,
        run=run,
        throughput=throughput,
        priority=priority,
    ):
        actiondb.approve_run(username=username, run=run, message="unapproved")
        with rundb.active_run_lock(run["_id"]):
            run["approved"] = False
            run["approver"] = ""

    before = _del_tasks(run)
    _apply_modify(rundb=rundb, viewer=viewer, run=run, post=post)

    after = _del_tasks(run)
    changed = _changed_fields_message(before=before, after=after)

    actiondb.modify_run(
        username=username,
        run=before,
        message="modify: " + ", ".join(changed),
    )

    if bool(run.get("approved")):
        session.flash("The test was successfully modified!")
    elif was_approved:
        session.flash(
            "The test was successfully modified but it will have to be reapproved...",
            "warning",
        )
    else:
        session.flash("The test was successfully modified. Please wait for approval.")

    return _redirect(request=request, session=session, url="/tests")


@router.post("/tests/stop")
async def tests_stop_post(request: Request) -> Response:
    """Stop a run (user/approver only)."""
    session = load_session(request)
    viewer = _viewer_from_request(request=request)

    form: FormData = await request.form()
    _csrf_or_403(request=request, session=session, form=form)

    if viewer.username is None:
        session.flash("Please login")
        return _redirect(request=request, session=session, url="/login")

    run_id = _form_str(form, "run-id")
    if run_id is None:
        return _redirect(request=request, session=session, url="/tests")

    rundb = cast("RunDb", request.app.state.rundb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    run = rundb.get_run(run_id)
    if run is None:
        session.flash("No run with this id", "error")
        return _redirect(request=request, session=session, url="/tests")

    if not _can_modify_run(viewer=viewer, run=run):
        session.flash("Unable to modify another users run!", "error")
        return _redirect(request=request, session=session, url="/tests")

    rundb.stop_run(run_id)
    actiondb.stop_run(username=viewer.username, run=run, message="User stop")
    session.flash("Stopped run")
    return _redirect(request=request, session=session, url="/tests")


@router.post("/tests/approve")
async def tests_approve_post(request: Request) -> Response:
    """Approve a pending run (approvers only)."""
    session = load_session(request)
    viewer = _viewer_from_request(request=request)

    form: FormData = await request.form()
    _csrf_or_403(request=request, session=session, form=form)

    if viewer.username is None:
        return _redirect(request=request, session=session, url="/login")

    if not viewer.is_approver:
        session.flash("Please login as approver")
        return _redirect(request=request, session=session, url="/login")

    run_id = _form_str(form, "run-id")
    if run_id is None:
        session.flash("Missing run id", "error")
        return _redirect(request=request, session=session, url="/tests")

    rundb = cast("RunDb", request.app.state.rundb)
    userdb = cast("UserDb", request.app.state.userdb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    run, message = rundb.approve_run(run_id, viewer.username)
    if run is None:
        session.flash(str(message), "error")
        return _redirect(request=request, session=session, url="/tests")

    compat = _CompatRequest(
        POST={},
        authenticated_userid=viewer.username,
        session=_CompatSession(session),
        userdb=userdb,
        rundb=rundb,
        host_url=str(request.base_url).rstrip("/"),
    )
    try:
        update_nets(compat, run)
    except Exception as exc:  # noqa: BLE001
        session.flash(str(exc), "error")

    actiondb.approve_run(username=viewer.username, run=run, message="approved")
    session.flash(str(message))
    return _redirect(request=request, session=session, url="/tests")


@router.post("/tests/purge")
async def tests_purge_post(request: Request) -> Response:
    """Manually purge a run (approver or submitting user)."""
    session = load_session(request)
    viewer = _viewer_from_request(request=request)

    form: FormData = await request.form()
    _csrf_or_403(request=request, session=session, form=form)

    run_id = _form_str(form, "run-id")
    if run_id is None:
        session.flash("Missing run id", "error")
        return _redirect(request=request, session=session, url="/tests")

    rundb = cast("RunDb", request.app.state.rundb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    run = rundb.get_run(run_id)
    if run is None:
        session.flash("No run with this id", "error")
        return _redirect(request=request, session=session, url="/tests")

    if not viewer.is_approver and not _same_user(viewer=viewer, run=run):
        session.flash("Only approvers or the submitting user can purge the run.")
        return _redirect(request=request, session=session, url="/login")

    message = rundb.purge_run(run, p=0.01, res=4.5)

    username = viewer.username or ""
    action_message = (
        f"Manual purge (not performed): {message}" if message else "Manual purge"
    )
    actiondb.purge_run(
        username=username,
        run=run,
        message=action_message,
    )

    if message:
        session.flash(message)
    else:
        session.flash("Purged run")

    return _redirect(request=request, session=session, url="/tests")


@router.post("/tests/delete")
async def tests_delete_post(request: Request) -> Response:
    """Mark a run as deleted (user/approver only)."""
    session = load_session(request)
    viewer = _viewer_from_request(request=request)

    form: FormData = await request.form()
    _csrf_or_403(request=request, session=session, form=form)

    if viewer.username is None:
        session.flash("Please login")
        return _redirect(request=request, session=session, url="/login")

    run_id = _form_str(form, "run-id")
    if run_id is None:
        return _redirect(request=request, session=session, url="/tests")

    rundb = cast("RunDb", request.app.state.rundb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    run = rundb.get_run(run_id)
    if run is None:
        session.flash("No run with this id", "error")
        return _redirect(request=request, session=session, url="/tests")

    if not _can_modify_run(viewer=viewer, run=run):
        session.flash("Unable to modify another users run!", "error")
        return _redirect(request=request, session=session, url="/tests")

    rundb.set_inactive_run(run)
    run["deleted"] = True

    try:
        validate(runs_schema, run, "run")
    except Exception as exc:  # noqa: BLE001
        message = f"The run object {run_id} does not validate: {exc}"
        _LOGGER.warning(message)
        version = run.get("version")
        if isinstance(version, int) and version >= RUN_VERSION:
            actiondb.log_message(username="fishtest.system", message=message)

    rundb.buffer(run, priority=Prio.SAVE_NOW)
    actiondb.delete_run(username=viewer.username, run=run)
    session.flash("Deleted run")
    return _redirect(request=request, session=session, url="/tests")
