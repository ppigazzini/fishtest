"""UI request context helpers for FastAPI glue views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fishtest.glue.cookie_session import authenticated_user
from fishtest.glue.dependencies import (
    get_actiondb,
    get_rundb,
    get_userdb,
    get_workerdb,
)
from fishtest.glue.ui_pipeline import build_template_request

if TYPE_CHECKING:
    from fastapi import Request
    from fishtest.actiondb import ActionDb
    from fishtest.glue.cookie_session import CookieSession
    from fishtest.glue.template_request import TemplateRequest
    from fishtest.rundb import RunDb
    from fishtest.userdb import UserDb
    from fishtest.workerdb import WorkerDb


@dataclass(frozen=True)
class UIRequestContext:
    """Typed, request-scoped context for UI routes."""

    session: CookieSession
    authenticated_userid: str | None
    rundb: RunDb
    userdb: UserDb
    actiondb: ActionDb
    workerdb: WorkerDb
    url: str
    base_url: str
    template_request: TemplateRequest


def build_ui_context(request: Request, session: CookieSession) -> UIRequestContext:
    """Build a UI request context with DB handles and template request."""
    return UIRequestContext(
        session=session,
        authenticated_userid=authenticated_user(session),
        rundb=get_rundb(request),
        userdb=get_userdb(request),
        actiondb=get_actiondb(request),
        workerdb=get_workerdb(request),
        url=str(request.url),
        base_url=str(request.base_url).rstrip("/"),
        template_request=build_template_request(request, session),
    )
