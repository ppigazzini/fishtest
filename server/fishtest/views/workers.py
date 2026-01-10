"""FastAPI UI routes for worker management.

Ports Pyramid route:
- GET `/workers/{worker_name}`
- POST `/workers/{worker_name}`

Renders the existing `workers.mak` template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fishtest.cookie_session import commit_session, load_session
from fishtest.csrf import csrf_or_403
from fishtest.mako import default_template_lookup, render_template
from fishtest.schemas import short_worker_name
from fishtest.template_request import TemplateRequest
from fishtest.views.common import authenticated_user, is_https
from vtjson import ValidationError, union, validate

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fishtest.actiondb import ActionDb
    from fishtest.userdb import UserDb
    from fishtest.workerdb import WorkerDb


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()

_MAX_MESSAGE_CHARS = 500
_WORKER_NAME_PARTS = 3


def _is_approver(*, userdb: UserDb, username: str | None) -> bool:
    if not username:
        return False
    groups = userdb.get_user_groups(username) or []
    return "group:approvers" in groups


def _normalize_lf(message: str) -> str:
    return message.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def _host_url(request: Request) -> str:
    scheme = "https" if is_https(request) else "http"
    forwarded_host = (
        request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    )
    host = forwarded_host or request.headers.get("host", "")
    if not host:
        return str(request.base_url).rstrip("/")
    return f"{scheme}://{host}"


def _worker_email(
    *,
    worker_name: str,
    blocker_name: str | None,
    message: str,
    host_url: str,
) -> str:
    owner_name = worker_name.split("-")[0]
    blocker_name = blocker_name or "fishtest"

    return (
        f"Dear {owner_name},\n\n"
        "Thank you for contributing to the development of Stockfish. "
        "Unfortunately, it seems your Fishtest worker "
        f"{worker_name} has some issue(s). More specifically the following "
        "has been reported:\n\n"
        f"{message}\n\n"
        "You may possibly find more information about this in our event log at "
        f"{host_url}/actions\n\n"
        "Feel free to reply to this email if you require any help, or else "
        "contact the #fishtest-dev channel on the Stockfish Discord server: "
        "https://discord.com/invite/awnh2qZfTT\n\n"
        "Enjoy your day,\n\n"
        f"{blocker_name} (Fishtest approver)\n\n"
    )


def _render(
    *,
    request: Request,
    template_name: str,
    context: Mapping[str, object],
    remember: bool,
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
        context={"request": template_request, **dict(context)},
    )

    response = HTMLResponse(rendered.html)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Expires"] = "0"

    commit_session(
        response=response,
        session=session,
        remember=remember,
        secure=is_https(request),
    )
    return response


def _render_show(
    *,
    request: Request,
    blocked_workers: list[dict[str, object]],
    is_approver: bool,
    show_admin: bool,
    admin_context: dict[str, object] | None = None,
) -> HTMLResponse:
    context: dict[str, object] = {
        "show_admin": show_admin,
        "show_email": is_approver,
        "blocked_workers": blocked_workers,
    }
    if show_admin and admin_context:
        context.update(admin_context)
    return _render(
        request=request,
        template_name="workers.mak",
        context=context,
        remember=True,
    )


@router.get("/workers/{worker_name}", response_class=HTMLResponse)
async def workers_get(request: Request, worker_name: str) -> HTMLResponse:
    """Render the workers page (list view + per-worker admin view)."""
    userdb = cast("UserDb", request.app.state.userdb)
    workerdb = cast("WorkerDb", request.app.state.workerdb)

    session = load_session(request)
    blocker_name = authenticated_user(session)
    is_approver = _is_approver(userdb=userdb, username=blocker_name)

    blocked_workers = workerdb.get_blocked_workers()

    if is_approver:
        host_url = _host_url(request)
        for worker in blocked_workers:
            maybe_name = worker.get("worker_name")
            if not isinstance(maybe_name, str):
                continue
            owner_name = maybe_name.split("-")[0]
            owner = userdb.get_user(owner_name)
            worker["owner_email"] = "" if owner is None else str(owner.get("email", ""))
            worker["body"] = _worker_email(
                worker_name=maybe_name,
                blocker_name=blocker_name,
                message=str(worker.get("message", "")),
                host_url=host_url,
            )
            worker["subject"] = f"Issue(s) with worker {maybe_name}"

    try:
        validate(union(short_worker_name, "show"), worker_name, name="worker_name")
    except ValidationError as exc:
        session.flash(str(exc), "error")
        return _render_show(
            request=request,
            blocked_workers=blocked_workers,
            is_approver=is_approver,
            show_admin=False,
        )

    parts = worker_name.split("-")
    if len(parts) != _WORKER_NAME_PARTS or worker_name == "show":
        return _render_show(
            request=request,
            blocked_workers=blocked_workers,
            is_approver=is_approver,
            show_admin=False,
        )

    if not blocker_name:
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
        return _render_show(
            request=request,
            blocked_workers=blocked_workers,
            is_approver=is_approver,
            show_admin=False,
        )

    owner_name = parts[0]
    if not is_approver and blocker_name != owner_name:
        session.flash("Only owners and approvers can block/unblock", "error")
        return _render_show(
            request=request,
            blocked_workers=blocked_workers,
            is_approver=is_approver,
            show_admin=False,
        )

    worker = workerdb.get_worker(worker_name)
    return _render_show(
        request=request,
        blocked_workers=blocked_workers,
        is_approver=is_approver,
        show_admin=True,
        admin_context={
            "worker_name": worker_name,
            "blocked": bool(worker.get("blocked")),
            "message": str(worker.get("message", "")),
            "last_updated": worker.get("last_updated"),
        },
    )


@router.post("/workers/{worker_name}")
async def workers_post(request: Request, worker_name: str) -> Response:
    """Handle worker block/unblock POST from the workers page."""
    userdb = cast("UserDb", request.app.state.userdb)
    workerdb = cast("WorkerDb", request.app.state.workerdb)
    actiondb = cast("ActionDb", request.app.state.actiondb)

    session = load_session(request)
    blocker_name = authenticated_user(session)
    is_approver = _is_approver(userdb=userdb, username=blocker_name)

    form = await request.form()
    form_token = form.get("csrf_token")
    csrf_or_403(
        request=request,
        session=session,
        form_token=form_token if isinstance(form_token, str) else None,
    )

    if not blocker_name:
        session.flash("Please login")
        response = RedirectResponse(url="/login", status_code=303)
        commit_session(
            response=response,
            session=session,
            remember=False,
            secure=is_https(request),
        )
        return response

    try:
        validate(union(short_worker_name, "show"), worker_name, name="worker_name")
    except ValidationError as exc:
        session.flash(str(exc), "error")
        response = RedirectResponse(url="/workers/show", status_code=303)
        commit_session(
            response=response,
            session=session,
            remember=True,
            secure=is_https(request),
        )
        return response

    parts = worker_name.split("-")
    if len(parts) != _WORKER_NAME_PARTS:
        response = RedirectResponse(url="/workers/show", status_code=303)
        commit_session(
            response=response,
            session=session,
            remember=True,
            secure=is_https(request),
        )
        return response

    owner_name = parts[0]
    if not is_approver and blocker_name != owner_name:
        session.flash("Only owners and approvers can block/unblock", "error")
        response = RedirectResponse(url="/workers/show", status_code=303)
        commit_session(
            response=response,
            session=session,
            remember=True,
            secure=is_https(request),
        )
        return response

    button = form.get("submit")
    if button == "Submit":
        blocked = form.get("blocked") is not None
        message = form.get("message")
        message_str = message if isinstance(message, str) else ""

        if len(message_str) > _MAX_MESSAGE_CHARS:
            session.flash(
                "Warning: your description of the issue has been truncated to "
                f"{_MAX_MESSAGE_CHARS} characters",
                "error",
            )
            message_str = message_str[:_MAX_MESSAGE_CHARS]

        message_str = _normalize_lf(message_str)
        was_blocked = bool(workerdb.get_worker(worker_name).get("blocked"))

        workerdb.update_worker(
            worker_name,
            blocked=blocked,
            message=message_str,
        )

        if blocked != was_blocked:
            session.flash(
                f"Worker {worker_name} {'blocked' if blocked else 'unblocked'}!",
            )
            actiondb.block_worker(
                username=blocker_name,
                worker=worker_name,
                message="blocked" if blocked else "unblocked",
            )

    response = RedirectResponse(url="/workers/show", status_code=303)
    commit_session(
        response=response,
        session=session,
        remember=True,
        secure=is_https(request),
    )
    return response
