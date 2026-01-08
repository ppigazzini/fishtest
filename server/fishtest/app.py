"""FastAPI application factory for the Pyramid -> FastAPI migration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import fishtest.github_api as gh
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fishtest.errors import install_error_handlers
from fishtest.router import include_routers
from fishtest.rundb import RunDb
from fishtest.settings import DEFAULT_APP_TITLE, AppSettings, default_static_dir
from starlette.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


def create_app() -> FastAPI:
    """Create and configure the FastAPI app."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = AppSettings.from_env()
        app.state.settings = settings

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
            rundb._shutdown = True  # noqa: SLF001
            rundb.conn.close()

    app = FastAPI(title=DEFAULT_APP_TITLE, lifespan=lifespan)
    install_error_handlers(app)

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
