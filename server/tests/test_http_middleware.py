# ruff: noqa: ANN201, ANN206, B904, D100, D101, D102, E501, EM101, EM102, INP001, PLC0415, PT009, S105, S106, TRY003

import os
import unittest
from typing import Any

try:
    import fastapi_util
except ModuleNotFoundError:  # pragma: no cover
    from tests import fastapi_util


class _UserDbStub:
    def __init__(self, *, blocked_username: str | None = None):
        self._blocked_username = blocked_username

    def get_blocked(self):
        if self._blocked_username:
            return [{"username": self._blocked_username, "blocked": True}]
        return []


class _RunDbStub:
    def __init__(
        self,
        *,
        userdb: Any | None = None,
        shutdown: bool = False,
        is_primary: bool = True,
    ) -> None:
        self.userdb = userdb
        self.actiondb = None
        self.workerdb = None
        self._shutdown = shutdown
        self._base_url_set = True
        self.base_url = None
        self._is_primary = is_primary

    def is_primary_instance(self) -> bool:
        return self._is_primary


class TestHttpMiddleware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.FastAPI, cls.TestClient = fastapi_util.require_fastapi()

    def test_shutdown_guard_returns_503(self):
        rundb = _RunDbStub(shutdown=True)
        app = fastapi_util.build_test_app(
            rundb=rundb,
            include_api=False,
            include_views=False,
        )

        @app.get("/ping")
        async def _ping():
            return {"ok": True}

        client = self.TestClient(app)
        response = client.get("/ping")
        self.assertEqual(response.status_code, 503)

    def test_attach_request_state_sets_base_url(self):
        from fastapi import Request

        rundb = _RunDbStub()
        rundb._base_url_set = False
        app = fastapi_util.build_test_app(
            rundb=rundb,
            include_api=False,
            include_views=False,
        )

        @app.get("/state")
        async def _state(request: Request):
            return {
                "has_rundb": request.state.rundb is rundb,
                "has_started_at": hasattr(request.state, "request_started_at"),
                "base_url": rundb.base_url,
            }

        client = self.TestClient(app)
        response = client.get("/state", headers={"host": "example.com"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["has_rundb"])
        self.assertTrue(payload["has_started_at"])
        self.assertEqual(payload["base_url"], "http://example.com")

    def test_reject_non_primary_worker_api(self):
        from fishtest.http.middleware import RejectNonPrimaryWorkerApiMiddleware

        app = self.FastAPI()
        app.add_middleware(RejectNonPrimaryWorkerApiMiddleware)
        app.state.rundb = _RunDbStub(is_primary=False)

        @app.post("/api/request_task")
        async def _request_task():
            return {"ok": True}

        client = self.TestClient(app)
        response = client.post("/api/request_task", json={})
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertIn("error", body)
        self.assertIn("/api/request_task", body["error"])

    def test_redirect_blocked_ui_users(self):
        from fishtest.http.cookie_session import SESSION_COOKIE_NAME, _encode_cookie

        os.environ.setdefault("FISHTEST_AUTHENTICATION_SECRET", "test-secret")

        userdb = _UserDbStub(blocked_username="blocked_user")
        rundb = _RunDbStub(userdb=userdb)

        app = fastapi_util.build_test_app(
            rundb=rundb,
            include_api=False,
            include_views=False,
        )

        @app.get("/tests")
        async def _tests():
            return {"ok": True}

        client = self.TestClient(app)
        cookie_value = _encode_cookie({"user": "blocked_user"})
        client.cookies.set(SESSION_COOKIE_NAME, cookie_value)
        response = client.get("/tests", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tests", response.headers.get("location", ""))
        set_cookie = response.headers.get("set-cookie", "")
        self.assertIn(f"{SESSION_COOKIE_NAME}=", set_cookie)


if __name__ == "__main__":
    unittest.main()
