"""FastAPI dependency helpers.

This module provides a typed way to access DB handles attached to the app.
During migration we keep storing them on `app.state`, but prefer `request.state`
when available (set by middleware).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request

if TYPE_CHECKING:
    from fishtest.actiondb import ActionDb
    from fishtest.rundb import RunDb
    from fishtest.userdb import UserDb
    from fishtest.workerdb import WorkerDb


def _state_attr(request: Request, name: str) -> object | None:
    value = getattr(request.state, name, None)
    if value is not None:
        return value
    return getattr(request.app.state, name, None)


def get_rundb(request: Request) -> RunDb:
    value = _state_attr(request, "rundb")
    if value is None:
        raise RuntimeError("RunDb not initialized")
    return cast("RunDb", value)


def get_userdb(request: Request) -> UserDb:
    value = _state_attr(request, "userdb")
    if value is None:
        raise RuntimeError("UserDb not initialized")
    return cast("UserDb", value)


def get_actiondb(request: Request) -> ActionDb:
    value = _state_attr(request, "actiondb")
    if value is None:
        raise RuntimeError("ActionDb not initialized")
    return cast("ActionDb", value)


def get_workerdb(request: Request) -> WorkerDb:
    value = _state_attr(request, "workerdb")
    if value is None:
        raise RuntimeError("WorkerDb not initialized")
    return cast("WorkerDb", value)
