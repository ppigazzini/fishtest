"""Test FastAPI application creation and lifecycle wiring."""

import json
import unittest
from unittest import mock

import test_support


class _RunCacheStub:
    def flush_all(self):
        return None


class _ConnStub:
    def close(self):
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

    def _start_with_legacy_usernames(self, stored):
        import fishtest.app as app_module
        from fishtest import schemas
        from fishtest.http.settings import AppSettings

        _FastAPI, TestClient = test_support.require_fastapi()

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        rundb = _RunDbStub()
        rundb.kvstore["legacy_usernames"] = stored
        settings = AppSettings(port=8001, primary_port=8000, is_primary_instance=False)
        self.addCleanup(setattr, schemas, "legacy_usernames", schemas.legacy_usernames)

        with mock.patch.dict("os.environ", {"FISHTEST_INSECURE_DEV": "1"}, clear=False):
            with (
                mock.patch.object(app_module, "RunDb", lambda **kwargs: rundb),
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
                mock.patch.object(app_module.gh, "init"),
            ):
                app = app_module.create_app()

                with TestClient(app) as client:
                    response = client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        return schemas.legacy_usernames

    def test_startup_keeps_the_stored_legacy_usernames_that_are_names(self):
        started_with = self._start_with_legacy_usernames(["Old User", 7, None])
        self.assertEqual(started_with, {"Old User"})

    def test_startup_survives_stored_legacy_usernames_that_are_not_a_list(self):
        started_with = self._start_with_legacy_usernames({"Old User": 1})
        self.assertEqual(started_with, set())


class _McpRunDbStub(_RunDbStub):
    def get_run(self, run_id):
        from test_schemas import RUN, RUN_ID

        return dict(RUN) if run_id == RUN_ID else None


class TestMcpEndpoint(unittest.TestCase):
    """The MCP endpoint is served where MCP_PATH names it, and nowhere else."""

    META = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    def _post(
        self,
        mcp_path,
        method,
        params,
        *,
        era="2026-07-28",
        host="localhost:8002",
        headers=None,
        public_url=None,
    ):
        headers = {"Accept": "application/json, text/event-stream", **(headers or {})}
        if era == "2026-07-28":
            # What an SDK client sends on its own: the version, the method and
            # the tool's name as headers, and the version again in `_meta`.
            headers |= {"MCP-Protocol-Version": era, "Mcp-Method": method}
            if method == "tools/call":
                headers["Mcp-Name"] = params["name"]
            params = {**params, "_meta": self.META}
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        return self._request(
            mcp_path,
            "POST",
            host=host,
            public_url=public_url,
            json=body,
            headers=headers,
        )

    def _request(
        self, mcp_path, verb, *, host="localhost:8002", public_url=None, **kwargs
    ):
        import fishtest.app as app_module
        from fishtest.http.settings import AppSettings

        _FastAPI, TestClient = test_support.require_fastapi()

        async def _fake_run_in_threadpool(func, *args, **kwargs):
            return func(*args, **kwargs)

        settings = AppSettings(
            port=8002,
            primary_port=8000,
            is_primary_instance=False,
            mcp_path=mcp_path,
            public_url=public_url,
        )
        with mock.patch.dict("os.environ", {"FISHTEST_INSECURE_DEV": "1"}, clear=False):
            with (
                mock.patch.object(app_module, "RunDb", _McpRunDbStub),
                mock.patch.object(
                    app_module, "run_in_threadpool", _fake_run_in_threadpool
                ),
                mock.patch.object(
                    app_module.AppSettings, "from_env", return_value=settings
                ),
                mock.patch.object(app_module.gh, "init"),
            ):
                app = app_module.create_app()
                with TestClient(app, base_url=f"http://{host}") as client:
                    return client.request(verb, "/mcp", **kwargs)

    def test_no_endpoint_without_mcp_path(self):
        response = self._post(None, "tools/list", {})
        self.assertEqual(response.status_code, 404)

    def test_the_endpoint_lists_the_tools(self):
        from fishtest.mcp_contracts import TOOLS

        response = self._post("/mcp", "tools/list", {})
        self.assertEqual(response.status_code, 200)
        listed = [tool["name"] for tool in response.json()["result"]["tools"]]
        self.assertEqual(listed, [tool["name"] for tool in TOOLS])
        self.assertNotIn("set-cookie", response.headers)

    def test_the_listing_is_public_for_an_hour_and_names_its_version(self):
        from fishtest.mcp_contracts import TOOLS_DIGEST

        for method in ("tools/list", "server/discover"):
            result = self._post("/mcp", method, {}).json()["result"]
            self.assertEqual(result["ttlMs"], 60 * 60 * 1000, method)
            self.assertEqual(result["cacheScope"], "public", method)
            info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
            self.assertEqual(info["version"], TOOLS_DIGEST, method)

    def test_the_endpoint_answers_a_call_in_either_protocol_era(self):
        from test_schemas import RUN_ID

        for era in ("2026-07-28", "2025-11-25"):
            response = self._post(
                "/mcp",
                "tools/call",
                {"name": "get_run", "arguments": {"run_id": RUN_ID}},
                era=era,
            )
            self.assertEqual(response.status_code, 200, era)
            result = response.json()["result"]
            self.assertFalse(result["isError"], era)
            self.assertEqual(result["structuredContent"]["run_id"], RUN_ID, era)

    def test_a_request_from_another_origin_is_refused(self):
        response = self._post(
            "/mcp", "tools/list", {}, headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(response.status_code, 403)

    def test_a_request_naming_another_host_is_refused(self):
        # A page that rebinds its own name to a local address sends that name.
        response = self._post("/mcp", "tools/list", {}, host="evil.example")
        self.assertEqual(response.status_code, 421)

    def test_the_local_and_the_public_host_and_origin_are_served(self):
        public = "https://tests.example.org"
        for host, origin in (
            ("localhost:8002", None),
            ("127.0.0.1:8002", "http://127.0.0.1:8002"),
            ("tests.example.org", None),
            ("tests.example.org", public),
        ):
            headers = {"Origin": origin} if origin else {}
            response = self._post(
                "/mcp",
                "tools/list",
                {},
                host=host,
                headers=headers,
                public_url=public,
            )
            self.assertEqual(response.status_code, 200, (host, origin))

    def test_the_public_host_is_refused_without_a_public_url(self):
        response = self._post("/mcp", "tools/list", {}, host="tests.example.org")
        self.assertEqual(response.status_code, 421)

    def test_the_endpoint_takes_post_alone(self):
        # Without the 2026-07-28 header, a GET asks for the event stream of
        # protocol 2025-11-25; a stateless server holds none open.
        stream = {"Accept": "text/event-stream"}
        for verb, headers in (
            ("GET", stream),
            ("GET", {**stream, "MCP-Protocol-Version": "2026-07-28"}),
            ("DELETE", {}),
            ("PUT", {}),
        ):
            response = self._request("/mcp", verb, headers=headers)
            self.assertEqual(response.status_code, 405, (verb, headers))
            self.assertEqual(response.headers["allow"], "POST", verb)

    def test_an_unknown_tool_is_a_json_rpc_error_over_http(self):
        # Protocol 2026-07-28 carries the error's class in the HTTP status too.
        for era, status in (("2026-07-28", 400), ("2025-11-25", 200)):
            response = self._post(
                "/mcp", "tools/call", {"name": "nope", "arguments": {}}, era=era
            )
            self.assertEqual(response.status_code, status, era)
            self.assertNotIn("result", response.json(), era)
            self.assertEqual(response.json()["error"]["code"], -32602, era)

    def test_a_body_past_the_api_actions_cap_is_refused(self):
        from fishtest.api import ACTIONS_BODY_MAX

        query = {"filter": {"username": "x" * ACTIONS_BODY_MAX}}
        response = self._post(
            "/mcp", "tools/call", {"name": "search_actions", "arguments": query}
        )
        self.assertEqual(response.status_code, 413)

    def test_a_nan_token_is_refused_before_any_tool_runs(self):
        # The SDK reads the tokens NaN and Infinity, which JSON forbids; the
        # gate refuses NaN in any argument, and an infinity is a JSON number.
        from fishtest import mcp_tools

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": "search_actions",
        }
        meta = json.dumps(self.META)
        for token, refused in (("NaN", True), ("Infinity", False), ("1e400", False)):
            body = (
                '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params":'
                ' {"name": "search_actions", "arguments": {"filter": {"time": %s}},'
                ' "_meta": %s}}' % (token, meta)
            )
            tool = mock.Mock(return_value={"actions": []})
            with mock.patch.dict(mcp_tools.TOOL_FUNCTIONS, {"search_actions": tool}):
                response = self._request(
                    "/mcp", "POST", content=body.encode(), headers=headers
                )
            result = response.json()["result"]
            self.assertEqual(result["isError"], refused, token)
            self.assertEqual(tool.called, not refused, token)
            if refused:
                self.assertIn("at filter.time", result["content"][0]["text"])

    def test_a_refused_call_is_a_tool_error_over_http(self):
        response = self._post(
            "/mcp", "tools/call", {"name": "get_run", "arguments": {"run_id": "x"}}
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("get_run refuses its arguments", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
