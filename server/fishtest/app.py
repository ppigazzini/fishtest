"""FastAPI application entrypoint.

This module is the single stable ASGI entrypoint: `uvicorn fishtest.app:app`.

The implementation depends on the small FastAPI glue layer under `fishtest.glue`
(middleware, error handling, settings parsing, template/session shims).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import fishtest.github_api as gh
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fishtest.glue.api import router as api_router
from fishtest.glue.errors import install_error_handlers
from fishtest.glue.middleware import (
    AttachRequestStateMiddleware,
    RedirectBlockedUiUsersMiddleware,
    ShutdownGuardMiddleware,
)
from fishtest.glue.settings import AppSettings, default_static_dir
from fishtest.glue.views import router as views_router
from fishtest.rundb import RunDb
from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


logger = logging.getLogger(__name__)


async def _shutdown_rundb(rundb: RunDb) -> None:
    rundb._shutdown = True  # noqa: SLF001
    await asyncio.sleep(0.5)

    try:
        if rundb.scheduler is not None:
            await run_in_threadpool(rundb.scheduler.stop)
    except Exception:
        logger.exception("Shutdown: error stopping scheduler")

    try:
        if rundb.is_primary_instance():
            await run_in_threadpool(rundb.run_cache.flush_all)
            await run_in_threadpool(rundb.save_persistent_data)
    except Exception:
        logger.exception("Shutdown: error flushing/saving")

    try:
        if rundb.port >= 0:
            await run_in_threadpool(
                rundb.actiondb.system_event,
                message=f"stop fishtest@{rundb.port}",
            )
    except Exception:
        logger.exception("Shutdown: error writing system_event")

    try:
        await run_in_threadpool(rundb.conn.close)
    except Exception:
        logger.exception("Shutdown: error closing MongoDB connection")


def _require_single_worker_on_primary(settings: AppSettings) -> None:
    if not settings.is_primary_instance:
        return

    workers_raw = (
        os.environ.get("UVICORN_WORKERS", "").strip()
        or os.environ.get("WEB_CONCURRENCY", "").strip()
    )
    if not workers_raw:
        return

    try:
        workers = int(workers_raw)
    except ValueError:
        return

    if workers != 1:
        message = (
            "Primary instance must run with a single Uvicorn worker "
            "(to avoid duplicated scheduler/GitHub side effects)."
        )
        raise RuntimeError(message)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = AppSettings.from_env()
        app.state.settings = settings

        _require_single_worker_on_primary(settings)

        rundb = await run_in_threadpool(
            RunDb,
            port=settings.port,
            is_primary_instance=settings.is_primary_instance,
        )

        app.state.rundb = rundb
        app.state.userdb = rundb.userdb
        app.state.actiondb = rundb.actiondb
        app.state.workerdb = rundb.workerdb

        if settings.is_primary_instance:
            await run_in_threadpool(gh.init, rundb.kvstore, rundb.actiondb)
            await run_in_threadpool(rundb.update_aggregated_data)
            await run_in_threadpool(rundb.schedule_tasks)

        try:
            yield
        finally:
            await _shutdown_rundb(rundb)

    app = FastAPI(lifespan=lifespan)

    install_error_handlers(app)

    app.add_middleware(cast("Any", ShutdownGuardMiddleware))
    app.add_middleware(cast("Any", AttachRequestStateMiddleware))
    app.add_middleware(cast("Any", RedirectBlockedUiUsersMiddleware))

    static_dir = default_static_dir()

    app.mount(
        "/static",
        StaticFiles(directory=str(static_dir)),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse(url="/tests", status_code=302)

    app.include_router(views_router)
    app.include_router(api_router)

    return app


app = create_app()


__all__ = [
    "app",
    "create_app",
]
