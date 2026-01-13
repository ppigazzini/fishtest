import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fishtest.app import _require_single_worker_on_primary
from fishtest.glue import cookie_session
from fishtest.glue.api import WORKER_API_PATHS
from fishtest.glue.errors import _WORKER_API_PATHS
from fishtest.glue.middleware import _get_blocked_cached
from fishtest.glue.settings import AppSettings
from fishtest.glue.template_request import TemplateRequest
from starlette.responses import Response


class TemplateRequestStaticUrlTests(unittest.TestCase):
    def test_static_url_blocks_traversal(self):
        from fishtest.glue import template_request

        with tempfile.TemporaryDirectory() as tmpdir:
            static_dir = Path(tmpdir) / "static"
            static_dir.mkdir(parents=True, exist_ok=True)

            outside = Path(tmpdir) / "secret.txt"
            outside.write_text("nope", encoding="utf-8")

            original_dir = template_request._STATIC_DIR
            original_cache = dict(template_request._STATIC_TOKEN_CACHE)
            template_request._STATIC_DIR = static_dir
            template_request._STATIC_TOKEN_CACHE.clear()
            try:
                req = TemplateRequest(
                    headers={},
                    cookies={},
                    query_params={},
                    session=cookie_session.CookieSession(data={}),
                    authenticated_userid=None,
                    userdb=object(),
                    url="/tests",
                )
                url = req.static_url("fishtest:static/../secret.txt")
                self.assertTrue(url.startswith("/static/"))
                self.assertNotIn("?x=", url)
            finally:
                template_request._STATIC_DIR = original_dir
                template_request._STATIC_TOKEN_CACHE.clear()
                template_request._STATIC_TOKEN_CACHE.update(original_cache)

    def test_static_url_token_is_urlsafe(self):
        from fishtest.glue import template_request

        with tempfile.TemporaryDirectory() as tmpdir:
            static_dir = Path(tmpdir) / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            target = static_dir / "css" / "site.css"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("body{}", encoding="utf-8")

            original_dir = template_request._STATIC_DIR
            original_cache = dict(template_request._STATIC_TOKEN_CACHE)
            template_request._STATIC_DIR = static_dir
            template_request._STATIC_TOKEN_CACHE.clear()
            try:
                req = TemplateRequest(
                    headers={},
                    cookies={},
                    query_params={},
                    session=cookie_session.CookieSession(data={}),
                    authenticated_userid=None,
                    userdb=object(),
                    url="/tests",
                )
                url = req.static_url("fishtest:static/css/site.css")
                self.assertIn("?x=", url)
                token = url.split("?x=", 1)[1]
                self.assertRegex(token, r"^[A-Za-z0-9_-]+$")
                self.assertNotIn("=", token)
            finally:
                template_request._STATIC_DIR = original_dir
                template_request._STATIC_TOKEN_CACHE.clear()
                template_request._STATIC_TOKEN_CACHE.update(original_cache)

    def test_static_token_cache_eviction(self):
        from fishtest.glue import template_request

        with tempfile.TemporaryDirectory() as tmpdir:
            static_dir = Path(tmpdir) / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            (static_dir / "a.txt").write_text("a", encoding="utf-8")
            (static_dir / "b.txt").write_text("b", encoding="utf-8")

            original_dir = template_request._STATIC_DIR
            original_cache = dict(template_request._STATIC_TOKEN_CACHE)
            original_max = template_request._STATIC_TOKEN_CACHE_MAX
            template_request._STATIC_DIR = static_dir
            template_request._STATIC_TOKEN_CACHE.clear()
            template_request._STATIC_TOKEN_CACHE_MAX = 1
            try:
                template_request._static_file_token("a.txt")
                template_request._static_file_token("b.txt")
                self.assertLessEqual(
                    len(template_request._STATIC_TOKEN_CACHE),
                    template_request._STATIC_TOKEN_CACHE_MAX,
                )
            finally:
                template_request._STATIC_DIR = original_dir
                template_request._STATIC_TOKEN_CACHE.clear()
                template_request._STATIC_TOKEN_CACHE.update(original_cache)
                template_request._STATIC_TOKEN_CACHE_MAX = original_max


class CookieSessionTests(unittest.TestCase):
    def test_secret_missing_requires_opt_in(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                cookie_session._secret_key()

    def test_insecure_dev_fallback(self):
        with mock.patch.dict(
            os.environ,
            {cookie_session.INSECURE_DEV_ENV: "1"},
            clear=True,
        ):
            self.assertEqual(cookie_session._secret_key(), "insecure-dev-secret")

    def test_commit_session_trims_flashes(self):
        with mock.patch.dict(
            os.environ,
            {"FISHTEST_AUTHENTICATION_SECRET": "test-secret"},
            clear=True,
        ):
            original_limit = cookie_session.MAX_COOKIE_BYTES
            cookie_session.MAX_COOKIE_BYTES = 200
            try:
                session = cookie_session.CookieSession(data={"flashes": {"": []}})
                session.dirty = True
                for i in range(100):
                    session.flash(f"msg-{i}")

                response = Response()
                cookie_session.commit_session(
                    response=response,
                    session=session,
                    remember=False,
                    secure=False,
                )

                set_cookie = response.headers.get("set-cookie")
                self.assertIsNotNone(set_cookie)
                cookie_value = set_cookie.split("fishtest_session=", 1)[1].split(
                    ";",
                    1,
                )[0]
                self.assertLessEqual(
                    len(cookie_value.encode("utf-8")),
                    cookie_session.MAX_COOKIE_BYTES,
                )
                self.assertLessEqual(
                    len(session.data.get("flashes", {}).get("", [])),
                    100,
                )
            finally:
                cookie_session.MAX_COOKIE_BYTES = original_limit


class SettingsTests(unittest.TestCase):
    def test_primary_when_port_unknown(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = AppSettings.from_env()
        self.assertTrue(settings.is_primary_instance)

    def test_primary_when_primary_port_unknown(self):
        with mock.patch.dict(os.environ, {"FISHTEST_PORT": "8000"}, clear=True):
            settings = AppSettings.from_env()
        self.assertTrue(settings.is_primary_instance)

    def test_primary_when_ports_match(self):
        with mock.patch.dict(
            os.environ,
            {"FISHTEST_PORT": "8000", "FISHTEST_PRIMARY_PORT": "8000"},
            clear=True,
        ):
            settings = AppSettings.from_env()
        self.assertTrue(settings.is_primary_instance)

    def test_secondary_when_ports_differ(self):
        with mock.patch.dict(
            os.environ,
            {"FISHTEST_PORT": "8001", "FISHTEST_PRIMARY_PORT": "8000"},
            clear=True,
        ):
            settings = AppSettings.from_env()
        self.assertFalse(settings.is_primary_instance)


class RuntimeInvariantTests(unittest.TestCase):
    def test_primary_requires_single_worker_uvicorn(self):
        settings = AppSettings(port=8000, primary_port=8000, is_primary_instance=True)
        with mock.patch.dict(os.environ, {"UVICORN_WORKERS": "2"}, clear=True):
            with self.assertRaises(RuntimeError):
                _require_single_worker_on_primary(settings)

    def test_primary_requires_single_worker_web_concurrency(self):
        settings = AppSettings(port=8000, primary_port=8000, is_primary_instance=True)
        with mock.patch.dict(os.environ, {"WEB_CONCURRENCY": "2"}, clear=True):
            with self.assertRaises(RuntimeError):
                _require_single_worker_on_primary(settings)

    def test_secondary_ignores_worker_settings(self):
        settings = AppSettings(port=8001, primary_port=8000, is_primary_instance=False)
        with mock.patch.dict(os.environ, {"UVICORN_WORKERS": "4"}, clear=True):
            _require_single_worker_on_primary(settings)


class ErrorHandlerWorkerPathsTests(unittest.TestCase):
    def test_worker_paths_source_of_truth(self):
        self.assertEqual(set(WORKER_API_PATHS), set(_WORKER_API_PATHS))


class BlockedUserCacheTests(unittest.TestCase):
    def test_blocked_cache_uses_ttl(self):
        class FakeUserDb:
            def __init__(self):
                self.calls = 0

            def get_blocked(self):
                self.calls += 1
                return [{"username": "u", "blocked": True}]

        userdb = FakeUserDb()

        with mock.patch("time.monotonic", side_effect=[1.0, 1.5, 5.0]):
            first = _get_blocked_cached(userdb)
            second = _get_blocked_cached(userdb)
            third = _get_blocked_cached(userdb)

        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(userdb.calls, 2)
