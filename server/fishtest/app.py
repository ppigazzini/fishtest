"""FastAPI application factory for the Pyramid -> FastAPI migration."""

from __future__ import annotations

import asyncio
import faulthandler
import os
import signal
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import fishtest.github_api as gh
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fishtest.cookie_session import clear_session_cookie, load_session
from fishtest.errors import install_error_handlers
from fishtest.router import include_routers
from fishtest.rundb import RunDb
from fishtest.settings import DEFAULT_APP_TITLE, AppSettings, default_static_dir
from fishtest.views.common import authenticated_user, is_https
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.responses import Response


def _install_thread_dump_signal_handler() -> None:
    """Install a signal handler to dump Python stack traces for all threads.

    This provides a FastAPI/Uvicorn equivalent of the legacy Pyramid handler:

        kill -USR1 <pid>

    Notes:
    - This is process-local. If Uvicorn is started with multiple workers
      (multiple processes), the signal must be sent to each worker PID.
    - On platforms without SIGUSR1 (e.g. Windows), this is a no-op.

    """
    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is None:
        return

    try:
        faulthandler.register(sigusr1, file=sys.stderr, all_threads=True)
    except (RuntimeError, ValueError):
        # RuntimeError: faulthandler disabled or not available.
        # ValueError: signal already registered.
        return


async def _shutdown_rundb(rundb: RunDb) -> None:
    """Gracefully shut down the server state.

    This mirrors the intent of Pyramid's SIGINT/SIGTERM handler (RunDb.exit_run),
    but is executed from FastAPI's lifespan shutdown so we don't conflict with
    Uvicorn's signal handling.
    """
    print("Stop handling requests... ", flush=True)
    rundb._shutdown = True  # noqa: SLF001

    # Small delay to let in-flight requests finish (matches legacy behavior).
    await asyncio.sleep(0.5)

    try:
        if rundb.scheduler is not None:
            print("Stopping scheduler... ", flush=True)
            rundb.scheduler.stop()
    except Exception as exc:
        print(
            f"Shutdown: error stopping scheduler: {exc!s}",
            file=sys.stderr,
            flush=True,
        )

    try:
        if rundb.is_primary_instance():
            print("Flushing run cache... ", flush=True)
            rundb.run_cache.flush_all()
            print("Saving persistent data...", flush=True)
            rundb.save_persistent_data()
    except Exception as exc:
        print(
            f"Shutdown: error flushing/saving: {exc!s}",
            file=sys.stderr,
            flush=True,
        )

    try:
        if rundb.port >= 0:
            rundb.actiondb.system_event(message=f"stop fishtest@{rundb.port}")
    except Exception as exc:
        print(
            f"Shutdown: error writing system_event: {exc!s}",
            file=sys.stderr,
            flush=True,
        )


def create_app() -> FastAPI:
    """Create and configure the FastAPI app."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _install_thread_dump_signal_handler()
        settings = AppSettings.from_env()
        app.state.settings = settings

        if not os.environ.get("FISHTEST_AUTHENTICATION_SECRET", "").strip():
            print(
                "FISHTEST_AUTHENTICATION_SECRET is missing, using an insecure default for authentication.",
                flush=True,
            )

        rundb = RunDb(
            port=settings.port,
            is_primary_instance=settings.is_primary_instance,
        )
        app.state.rundb = rundb
        app.state.userdb = rundb.userdb
        app.state.actiondb = rundb.actiondb
        app.state.workerdb = rundb.workerdb

        if settings.is_primary_instance:
            gh.init(rundb.kvstore, rundb.actiondb)
            rundb.update_aggregated_data()
            rundb.schedule_tasks()

        try:
            yield
        finally:
            await _shutdown_rundb(rundb)
            rundb.conn.close()

    app = FastAPI(title=DEFAULT_APP_TITLE, lifespan=lifespan)
    install_error_handlers(app)

    def _external_base_url_from_request(request: Request) -> str:
        forwarded = (
            request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        )
        scheme = forwarded or request.url.scheme
        host = request.headers.get("host")
        if host:
            return f"{scheme}://{host}".rstrip("/")
        return str(request.base_url).rstrip("/")

    @app.middleware("http")
    async def _attach_request_state_and_base_url(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        rundb = getattr(request.app.state, "rundb", None)
        if rundb is not None:
            request.state.rundb = rundb
            request.state.userdb = getattr(request.app.state, "userdb", None)
            request.state.actiondb = getattr(request.app.state, "actiondb", None)
            request.state.workerdb = getattr(request.app.state, "workerdb", None)

            if not getattr(rundb, "_base_url_set", True):
                rundb.base_url = _external_base_url_from_request(request)
                rundb._base_url_set = True  # noqa: SLF001

        return await call_next(request)

    @app.middleware("http")
    async def _redirect_blocked_ui_users(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Match Pyramid's behavior: if an authenticated UI user becomes blocked,
        # invalidate the session and redirect to the tests page.
        path = request.url.path
        if path.startswith("/api") or path.startswith("/static"):
            return await call_next(request)

        userdb = getattr(request.app.state, "userdb", None)
        if userdb is None:
            return await call_next(request)

        session = load_session(request)
        username = authenticated_user(session)
        if not username:
            return await call_next(request)

        blocked_users = userdb.get_blocked()
        is_blocked = any(
            isinstance(user, dict)
            and user.get("username") == username
            and user.get("blocked")
            for user in blocked_users
        )
        if is_blocked:
            session.invalidate()
            response = RedirectResponse(url="/tests", status_code=302)
            clear_session_cookie(response=response, secure=is_https(request))
            return response

        return await call_next(request)

    @app.middleware("http")
    async def _reject_requests_while_shutting_down(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        rundb = getattr(request.app.state, "rundb", None)
        if rundb is not None and getattr(rundb, "_shutdown", False):
            return PlainTextResponse("", status_code=503)
        return await call_next(request)

    static_dir: Final[Path] = default_static_dir()
    app.mount(
        "/static",
        StaticFiles(directory=str(static_dir)),
        name="static",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return a minimal health payload."""
        return {"status": "ok"}

    @app.get("/")
    def home() -> RedirectResponse:
        """Redirect to the main tests page (matches Pyramid home)."""
        return RedirectResponse(url="/tests", status_code=303)

    include_routers(app)
    return app


app = create_app()
