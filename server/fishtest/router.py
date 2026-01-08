"""Central FastAPI router registration.

The inclusion order matters for legacy route matching expectations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

from fishtest.api.public import router as api_public_router
from fishtest.api.worker import router as api_worker_router
from fishtest.views.actions import router as views_actions_router
from fishtest.views.auth import router as views_auth_router
from fishtest.views.contributors import router as views_contributors_router
from fishtest.views.nns import router as views_nns_router
from fishtest.views.rate_limits import router as views_rate_limits_router
from fishtest.views.sprt_calc import router as views_sprt_calc_router
from fishtest.views.tests import router as views_tests_router
from fishtest.views.tests_manage import router as tests_manage_router
from fishtest.views.tests_view import router as views_tests_view_router
from fishtest.views.user import router as views_user_router
from fishtest.views.user_management import router as views_user_management_router
from fishtest.views.workers import router as views_workers_router


def include_routers(app: FastAPI) -> None:
    """Register all API + UI routers in the intended order."""
    app.include_router(api_public_router)
    app.include_router(views_auth_router)
    app.include_router(views_contributors_router)
    app.include_router(views_actions_router)
    app.include_router(views_user_management_router)
    app.include_router(views_nns_router)
    app.include_router(views_sprt_calc_router)
    app.include_router(views_rate_limits_router)
    app.include_router(views_tests_router)
    app.include_router(tests_manage_router)
    app.include_router(views_tests_view_router)
    app.include_router(views_user_router)
    app.include_router(views_workers_router)
    app.include_router(api_worker_router)
