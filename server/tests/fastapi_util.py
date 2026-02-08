# ruff: noqa: EM101, EM102, TRY003

import os
import re
import unittest
from typing import Any


def require_fastapi() -> tuple[Any, Any]:
    """Return (FastAPI, TestClient) or skip the test module."""
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        return FastAPI, TestClient
    except (ModuleNotFoundError, RuntimeError) as exc:  # pragma: no cover
        # `starlette.testclient` raises a RuntimeError when the optional `httpx`
        # dependency is missing.
        name = getattr(exc, "name", None)
        raise unittest.SkipTest(
            f"FastAPI test dependencies missing ({name or exc}); skipping FastAPI HTTP tests",
        )


def build_test_app(*, rundb: Any, include_api: bool, include_views: bool):
    """Create a minimal FastAPI app wired like production (minus lifespan)."""
    os.environ.setdefault("FISHTEST_AUTHENTICATION_SECRET", "test-secret")

    FastAPI, _TestClient = require_fastapi()

    try:
        from fishtest.http.errors import install_error_handlers
        from fishtest.http.middleware import (
            AttachRequestStateMiddleware,
            RedirectBlockedUiUsersMiddleware,
            ShutdownGuardMiddleware,
        )
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise unittest.SkipTest(
            f"Server dependencies missing ({exc.name}); skipping FastAPI HTTP tests",
        )

    app = FastAPI()

    # Mimic production app.state wiring.
    app.state.rundb = rundb
    app.state.userdb = getattr(rundb, "userdb", None)
    app.state.actiondb = getattr(rundb, "actiondb", None)
    app.state.workerdb = getattr(rundb, "workerdb", None)

    install_error_handlers(app)

    app.add_middleware(ShutdownGuardMiddleware)
    app.add_middleware(AttachRequestStateMiddleware)
    app.add_middleware(RedirectBlockedUiUsersMiddleware)

    if include_api:
        try:
            from fishtest.http.api import router as api_router
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise unittest.SkipTest(
                f"Server dependencies missing ({exc.name}); skipping FastAPI HTTP tests",
            )
        app.include_router(api_router)

    if include_views:
        try:
            from fishtest.http.views import router as views_router
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise unittest.SkipTest(
                f"Server dependencies missing ({exc.name}); skipping FastAPI HTTP tests",
            )
        app.include_router(views_router)

    return app


def make_test_client(*, rundb: Any, include_api: bool, include_views: bool):
    """Create a starlette TestClient for the HTTP routers."""
    _FastAPI, TestClient = require_fastapi()
    app = build_test_app(
        rundb=rundb,
        include_api=include_api,
        include_views=include_views,
    )
    return TestClient(app)


_CSRF_META_RE = re.compile(
    r"<meta\s+name=\"csrf-token\"\s+content=\"([^\"]+)\"",
    re.IGNORECASE,
)


def extract_csrf_token(html: str) -> str:
    match = _CSRF_META_RE.search(html)
    if not match:
        raise AssertionError("Could not find csrf-token meta tag")
    return match.group(1)
