"""Test FastAPI application creation and lifecycle wiring."""

import unittest
from unittest import mock

import test_support


class _RunCacheStub:
    def flush_all(self):
        return None


class _ConnStub:
    def close(self):
        return None


class _SchedulerStub:
    def stop(self):
        return None


class _ActionDbStub:
    def system_event(self, message: str):
        _ = message


class _RunDbStub:
    def __init__(self, *, port: int = -1, is_primary_instance: bool = False):
        self.port = port
        self._is_primary_instance = is_primary_instance
        self._shutdown = False
        self.userdb = object()
        self.actiondb = _ActionDbStub()
        self.workerdb = object()
        self.kvstore = {}
        self.run_cache = _RunCacheStub()
        self.conn = _ConnStub()
        self.scheduler = None

    def is_primary_instance(self) -> bool:
        return self._is_primary_instance

    def update_aggregated_data(self):
        return None

    def schedule_tasks(self):
        self.scheduler = _SchedulerStub()
        return None

    def save_persistent_data(self):
        return None


class TestHttpApp(unittest.TestCase):
    def test_create_app_home_redirect_and_middleware(self):
        import fishtest.app as app_module
        from fishtest.http.settings import AppSettings

        _FastAPI, TestClient = test_support.require_fastapi()

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        settings = AppSettings(port=8000, primary_port=8000, is_primary_instance=False)

        with mock.patch.dict("os.environ", {"FISHTEST_INSECURE_DEV": "1"}, clear=False):
            with (
                mock.patch.object(app_module, "RunDb", _RunDbStub),
                mock.patch.object(
                    app_module,
                    "run_in_threadpool",
                    _fake_run_in_threadpool,
                ),
                mock.patch.object(
                    app_module.AppSettings,
                    "from_env",
                    return_value=settings,
                ),
            ):
                app = app_module.create_app()

            middleware_names = {
                getattr(mw.cls, "__name__", str(mw.cls)) for mw in app.user_middleware
            }
            self.assertIn("ShutdownGuardMiddleware", middleware_names)
            self.assertIn("AttachRequestStateMiddleware", middleware_names)
            self.assertIn("RejectNonPrimaryWorkerApiMiddleware", middleware_names)
            self.assertIn("RedirectBlockedUiUsersMiddleware", middleware_names)

            client = TestClient(app)
            response = client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("location"), "/tests")

    def test_secondary_instance_initializes_github_helper_without_refresh(self):
        import fishtest.app as app_module
        from fishtest.http.settings import AppSettings

        _FastAPI, TestClient = test_support.require_fastapi()

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        settings = AppSettings(port=8001, primary_port=8000, is_primary_instance=False)

        with mock.patch.dict("os.environ", {"FISHTEST_INSECURE_DEV": "1"}, clear=False):
            with (
                mock.patch.object(app_module, "RunDb", _RunDbStub),
                mock.patch.object(
                    app_module,
                    "run_in_threadpool",
                    _fake_run_in_threadpool,
                ),
                mock.patch.object(
                    app_module.AppSettings,
                    "from_env",
                    return_value=settings,
                ),
                mock.patch.object(app_module.gh, "init") as init_mock,
            ):
                app = app_module.create_app()

                with TestClient(app) as client:
                    response = client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        init_mock.assert_called_once_with(
            mock.ANY,
            mock.ANY,
            refresh_master_sha=False,
        )

    def test_openapi_url_enables_non_empty_schema_paths(self):
        import fishtest.app as app_module
        from fishtest.http.settings import AppSettings

        _FastAPI, TestClient = test_support.require_fastapi()

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        settings = AppSettings(
            port=8000,
            primary_port=8000,
            is_primary_instance=False,
            openapi_url="/openapi.json",
        )

        with mock.patch.dict("os.environ", {"FISHTEST_INSECURE_DEV": "1"}, clear=False):
            with (
                mock.patch.object(app_module, "RunDb", _RunDbStub),
                mock.patch.object(
                    app_module,
                    "run_in_threadpool",
                    _fake_run_in_threadpool,
                ),
                mock.patch.object(
                    app_module.AppSettings,
                    "from_env",
                    return_value=settings,
                ),
            ):
                app = app_module.create_app()

            client = TestClient(app)
            response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("paths", payload)
        self.assertTrue(payload["paths"])
        self.assertIn("/api/request_version", payload["paths"])

    def test_create_app_attaches_actions_search_service_when_configured(self):
        import fishtest.app as app_module
        from fishtest.http.settings import AppSettings, TypesenseSettings

        _FastAPI, TestClient = test_support.require_fastapi()

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        service_stub = mock.Mock()
        settings = AppSettings(
            port=8001,
            primary_port=8000,
            is_primary_instance=False,
            typesense=TypesenseSettings(
                enabled=False,
                actions_shadow_reads_enabled=True,
                host="http://localhost:8108",
                api_key="typesense-key",
            ),
        )

        with mock.patch.dict("os.environ", {"FISHTEST_INSECURE_DEV": "1"}, clear=False):
            with (
                mock.patch.object(app_module, "RunDb", _RunDbStub),
                mock.patch.object(
                    app_module,
                    "run_in_threadpool",
                    _fake_run_in_threadpool,
                ),
                mock.patch.object(
                    app_module.AppSettings,
                    "from_env",
                    return_value=settings,
                ),
                mock.patch.object(
                    app_module,
                    "_build_actions_search_service",
                    return_value=service_stub,
                ),
            ):
                app = app_module.create_app()

                with TestClient(app) as client:
                    self.assertIs(client.app.state.actions_search_service, service_stub)

        service_stub.close.assert_called_once_with()

    def test_create_app_attaches_finished_runs_search_service_when_configured(self):
        import fishtest.app as app_module
        from fishtest.http.settings import AppSettings, TypesenseSettings

        _FastAPI, TestClient = test_support.require_fastapi()

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        service_stub = mock.Mock()
        settings = AppSettings(
            port=8001,
            primary_port=8000,
            is_primary_instance=False,
            typesense=TypesenseSettings(
                enabled=False,
                finished_runs_shadow_reads_enabled=True,
                host="http://localhost:8108",
                api_key="typesense-key",
            ),
        )

        with mock.patch.dict("os.environ", {"FISHTEST_INSECURE_DEV": "1"}, clear=False):
            with (
                mock.patch.object(app_module, "RunDb", _RunDbStub),
                mock.patch.object(
                    app_module,
                    "run_in_threadpool",
                    _fake_run_in_threadpool,
                ),
                mock.patch.object(
                    app_module.AppSettings,
                    "from_env",
                    return_value=settings,
                ),
                mock.patch.object(
                    app_module,
                    "_build_finished_runs_search_service",
                    return_value=service_stub,
                ),
            ):
                app = app_module.create_app()

                with TestClient(app) as client:
                    self.assertIs(
                        client.app.state.finished_runs_search_service,
                        service_stub,
                    )

        service_stub.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
