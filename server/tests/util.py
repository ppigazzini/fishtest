import atexit

from fastapi import FastAPI
from fishtest.api.public import router as api_public_router
from fishtest.api.worker import router as api_worker_router
from fishtest.rundb import RunDb
from fishtest.views.auth import router as views_auth_router


def get_rundb():
    rundb = RunDb(db_name="fishtest_tests")
    atexit.register(rundb.conn.close)
    return rundb


def get_test_app(rundb: RunDb) -> FastAPI:
    """Create a minimal FastAPI app wired to the provided RunDb.

    This avoids depending on Pyramid (or the production lifespan which starts
    schedulers/side effects) while keeping the real endpoint implementations.
    """

    app = FastAPI(title="fishtest-tests")
    app.state.rundb = rundb
    app.state.userdb = rundb.userdb
    app.state.actiondb = rundb.actiondb
    app.state.workerdb = rundb.workerdb

    app.include_router(api_public_router)
    app.include_router(views_auth_router)
    app.include_router(api_worker_router)
    return app


def extract_csrf_token(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.find(marker)
    if start == -1:
        raise AssertionError("csrf_token input not found")
    start += len(marker)
    end = html.find('"', start)
    if end == -1:
        raise AssertionError("csrf_token value not terminated")
    return html[start:end]


def find_run(arg="username", value="travis"):
    rundb = RunDb(db_name="fishtest_tests")
    atexit.register(rundb.conn.close)
    for run in rundb.get_unfinished_runs():
        if run["args"][arg] == value:
            return run
    return None
