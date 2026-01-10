"""FastAPI application factory for the Pyramid -> FastAPI migration."""

from __future__ import annotations

import asyncio
import faulthandler
import os
import signal
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Final, cast

import fishtest.github_api as gh
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fishtest.errors import install_error_handlers
from fishtest.middleware import (
    AttachRequestStateMiddleware,
    RedirectBlockedUiUsersMiddleware,
    ShutdownGuardMiddleware,
)
from fishtest.router import include_routers
from fishtest.rundb import RunDb
from fishtest.settings import DEFAULT_APP_TITLE, AppSettings, default_static_dir
from starlette.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


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

    # Middleware order is explicit:
    # - Shutdown guard runs first (outermost).
    # - Blocked-user redirect applies to UI routes only.
    # - Request state wiring/base_url init runs closest to handlers.
    app.add_middleware(cast(Any, AttachRequestStateMiddleware))
    app.add_middleware(cast(Any, RedirectBlockedUiUsersMiddleware))
    app.add_middleware(cast(Any, ShutdownGuardMiddleware))

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
