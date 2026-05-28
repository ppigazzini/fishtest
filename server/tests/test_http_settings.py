"""Test HTTP settings parsing and derived runtime flags."""

import unittest
from pathlib import Path
from unittest import mock

from fishtest.http.settings import (
    HTMX_INPUT_CHANGED_DELAY_MS,
    TASK_SEMAPHORE_SIZE,
    THREADPOOL_TOKENS,
    TYPESENSE_ACTIONS_ALIAS,
    TYPESENSE_FINISHED_RUNS_ALIAS,
    UI_STATE_COOKIE_MAX_AGE_SECONDS,
    AppSettings,
    default_static_dir,
    env_bool,
    env_float,
    env_int,
)

DEFAULT_ENV_VALUE = 17
INVALID_ENV_VALUE = "not-an-int"
CUSTOM_STATIC_DIR = "/tmp/fishtest-static"
CUSTOM_OPENAPI_URL = "/openapi.json"


class SettingsContractTests(unittest.TestCase):
    def test_env_int_uses_default_for_blank_or_invalid_values(self):
        with mock.patch.dict("os.environ", {"FISHTEST_SAMPLE_INT": ""}, clear=False):
            self.assertEqual(
                env_int("FISHTEST_SAMPLE_INT", default=DEFAULT_ENV_VALUE),
                DEFAULT_ENV_VALUE,
            )

        with mock.patch.dict(
            "os.environ",
            {"FISHTEST_SAMPLE_INT": INVALID_ENV_VALUE},
            clear=False,
        ):
            self.assertEqual(
                env_int("FISHTEST_SAMPLE_INT", default=DEFAULT_ENV_VALUE),
                DEFAULT_ENV_VALUE,
            )

    def test_env_bool_uses_default_for_blank_or_invalid_values(self):
        with mock.patch.dict("os.environ", {"FISHTEST_SAMPLE_BOOL": ""}, clear=False):
            self.assertTrue(env_bool("FISHTEST_SAMPLE_BOOL", default=True))

        with mock.patch.dict(
            "os.environ",
            {"FISHTEST_SAMPLE_BOOL": "maybe"},
            clear=False,
        ):
            self.assertFalse(env_bool("FISHTEST_SAMPLE_BOOL", default=False))

    def test_env_float_uses_default_for_blank_or_invalid_values(self):
        with mock.patch.dict("os.environ", {"FISHTEST_SAMPLE_FLOAT": ""}, clear=False):
            self.assertEqual(env_float("FISHTEST_SAMPLE_FLOAT", default=1.5), 1.5)

        with mock.patch.dict(
            "os.environ",
            {"FISHTEST_SAMPLE_FLOAT": "invalid"},
            clear=False,
        ):
            self.assertEqual(env_float("FISHTEST_SAMPLE_FLOAT", default=2.5), 2.5)

    def test_default_static_dir_uses_env_override(self):
        with mock.patch.dict(
            "os.environ",
            {"FISHTEST_STATIC_DIR": CUSTOM_STATIC_DIR},
            clear=False,
        ):
            self.assertEqual(default_static_dir(), Path(CUSTOM_STATIC_DIR))

    def test_default_static_dir_defaults_to_package_static_directory(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            static_dir = default_static_dir()

        self.assertEqual(static_dir.name, "static")
        self.assertEqual(static_dir.parent.name, "fishtest")

    def test_app_settings_from_env_reads_openapi_url(self):
        with mock.patch.dict(
            "os.environ",
            {
                "FISHTEST_PORT": "8001",
                "FISHTEST_PRIMARY_PORT": "8000",
                "OPENAPI_URL": CUSTOM_OPENAPI_URL,
                "FISHTEST_TYPESENSE_ENABLED": "1",
                "FISHTEST_TYPESENSE_HOST": "http://localhost:8108",
                "FISHTEST_TYPESENSE_API_KEY": "typesense-key",
                "FISHTEST_TYPESENSE_FALLBACK_TO_MONGO": "0",
                "FISHTEST_TYPESENSE_ACTIONS_ALIAS": "actions_shadow",
                "FISHTEST_TYPESENSE_ACTIONS_SYNC_BATCH_SIZE": "125",
                "FISHTEST_TYPESENSE_ACTIONS_SYNC_INTERVAL_SECONDS": "45",
            },
            clear=True,
        ):
            settings = AppSettings.from_env()

        self.assertEqual(settings.port, 8001)
        self.assertEqual(settings.primary_port, 8000)
        self.assertFalse(settings.is_primary_instance)
        self.assertEqual(settings.openapi_url, CUSTOM_OPENAPI_URL)
        self.assertTrue(settings.typesense.enabled)
        self.assertFalse(settings.typesense.actions_enabled)
        self.assertTrue(settings.typesense.actions_shadow_reads_enabled)
        self.assertFalse(settings.typesense.finished_runs_enabled)
        self.assertFalse(settings.typesense.fallback_to_mongo)
        self.assertEqual(settings.typesense.host, "http://localhost:8108")
        self.assertEqual(settings.typesense.api_key, "typesense-key")
        self.assertEqual(settings.typesense.actions_sync_batch_size, 125)
        self.assertEqual(settings.typesense.actions_sync_interval_seconds, 45)
        self.assertTrue(settings.typesense.actions_service_enabled)
        self.assertEqual(settings.typesense.actions_alias, "actions_shadow")
        self.assertEqual(
            settings.typesense.finished_runs_alias,
            TYPESENSE_FINISHED_RUNS_ALIAS,
        )

    def test_app_settings_from_env_keeps_typesense_defaults_when_unset(self):
        with mock.patch.dict(
            "os.environ",
            {
                "FISHTEST_PORT": "8000",
                "FISHTEST_PRIMARY_PORT": "8000",
            },
            clear=True,
        ):
            settings = AppSettings.from_env()

        self.assertFalse(settings.typesense.enabled)
        self.assertFalse(settings.typesense.actions_enabled)
        self.assertFalse(settings.typesense.actions_shadow_reads_enabled)
        self.assertFalse(settings.typesense.finished_runs_enabled)
        self.assertTrue(settings.typesense.fallback_to_mongo)
        self.assertEqual(settings.typesense.host, "")
        self.assertEqual(settings.typesense.api_key, "")
        self.assertFalse(settings.typesense.actions_service_enabled)
        self.assertEqual(settings.typesense.actions_alias, TYPESENSE_ACTIONS_ALIAS)
        self.assertEqual(
            settings.typesense.finished_runs_alias,
            TYPESENSE_FINISHED_RUNS_ALIAS,
        )

    def test_runtime_limits_keep_headroom_for_http_work(self):
        self.assertGreater(THREADPOOL_TOKENS, TASK_SEMAPHORE_SIZE)
        self.assertGreater(HTMX_INPUT_CHANGED_DELAY_MS, 0)
        self.assertEqual(
            UI_STATE_COOKIE_MAX_AGE_SECONDS,
            60 * 60 * 24 * 400,
        )
