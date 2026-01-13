"""FastAPI dependency helpers.

This module provides a typed way to access DB handles attached to the app.
During migration we keep storing them on `app.state`, but prefer `request.state`
when available (set by middleware).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from fastapi import Request
    from fishtest.actiondb import ActionDb
    from fishtest.rundb import RunDb
    from fishtest.userdb import UserDb
    from fishtest.workerdb import WorkerDb


class DependencyNotInitializedError(RuntimeError):
    """Raised when an app dependency is missing from request/app state."""

    def __init__(self, dependency: str) -> None:
        """Create a DependencyNotInitializedError for the given dependency name."""
        message = f"{dependency} not initialized"
        super().__init__(message)


def _state_attr(request: Request, name: str) -> object | None:
    value = getattr(request.state, name, None)
    if value is not None:
        return value
    return getattr(request.app.state, name, None)


def get_rundb(request: Request) -> RunDb:
    """Return the request-scoped RunDb handle."""
    value = _state_attr(request, "rundb")
    if value is None:
        dependency = "RunDb"
        raise DependencyNotInitializedError(dependency)
    return cast("RunDb", value)


def get_userdb(request: Request) -> UserDb:
    """Return the request-scoped UserDb handle."""
    value = _state_attr(request, "userdb")
    if value is None:
        dependency = "UserDb"
        raise DependencyNotInitializedError(dependency)
    return cast("UserDb", value)


def get_actiondb(request: Request) -> ActionDb:
    """Return the request-scoped ActionDb handle."""
    value = _state_attr(request, "actiondb")
    if value is None:
        dependency = "ActionDb"
        raise DependencyNotInitializedError(dependency)
    return cast("ActionDb", value)


def get_workerdb(request: Request) -> WorkerDb:
    """Return the request-scoped WorkerDb handle."""
    value = _state_attr(request, "workerdb")
    if value is None:
        dependency = "WorkerDb"
        raise DependencyNotInitializedError(dependency)
    return cast("WorkerDb", value)
