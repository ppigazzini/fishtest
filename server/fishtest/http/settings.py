"""Parse runtime settings for the FastAPI server.

Centralize environment parsing, shared UI constants, and derived runtime flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Threadpool and scheduling-throttle constants.
#
# THREADPOOL_TOKENS: AnyIO threadpool size (tokens) for all blocking work.
# TASK_SEMAPHORE_SIZE: Max concurrent /api/request_task calls admitted.
# Invariant: TASK_SEMAPHORE_SIZE << THREADPOOL_TOKENS to avoid starving
# /api/beat and /api/update_task during reconnection bursts.
# Details: docs/2-threading-model.md (section: "Task scheduling throttle").

THREADPOOL_TOKENS: int = 200
TASK_SEMAPHORE_SIZE: int = 5

# htmx polling intervals (seconds), used via Jinja2 global `poll`.
POLL_MACHINES_HOMEPAGE_S: int = 60
POLL_TESTS_RUN_TABLES_S: int = 20
POLL_TESTS_VIEW_DETAIL_S: int = 15
POLL_TESTS_STATS_S: int = 15
POLL_TASKS_DETAIL_S: int = 60
POLL_LIVE_ELO_S: int = 10
POLL_RATE_LIMITS_GITHUB_S: int = 10
POLL_RATE_LIMITS_SERVER_S: int = 60
POLL_PENDING_USERS_NAV_S: int = 10

# htmx UI timing defaults.
# Keep this generic so multiple pages can share one debounce baseline.
HTMX_INPUT_CHANGED_DELAY_MS: int = 350

# Shared cookie and session policy for the UI and HTTP boundary.
# Keep session cookie values below the practical 4 KB browser limit, leaving
# room for the cookie name and attributes.
SESSION_COOKIE_VALUE_MAX_BYTES: int = 3800
SESSION_REMEMBER_ME_MAX_AGE_SECONDS: int = 60 * 60 * 24 * 400
UI_STATE_COOKIE_MAX_AGE_SECONDS: int = 60 * 60 * 24 * 400

# Template and UI view defaults.
WORKERS_PAGE_SIZE: int = 25
WORKERS_MAX_ALL: int = 5000
USER_MANAGEMENT_PAGE_SIZE: int = 25
USER_MANAGEMENT_MAX_ALL: int = 5000
TASKS_PAGE_SIZE: int = 25
TASKS_MAX_ALL: int = 5000
NNS_PAGE_SIZE: int = 25
NNS_MAX_ALL: int = 5000
ACTIONS_PAGE_SIZE: int = 25
CONTRIBUTORS_PAGE_SIZE: int = 100
CONTRIBUTORS_MAX_ALL: int = 5000
MACHINES_PAGE_SIZE: int = 500
FINISHED_FILTER_MAX_COUNT_AUTH: int = 10000
FINISHED_FILTER_MAX_COUNT_ANON: int = 1000

# Request/form limits for legacy sync UI handlers.
UI_HTTP_TIMEOUT_SECONDS: float = 15.0
UI_FORM_MAX_FILES: int = 2
UI_FORM_MAX_FIELDS: int = 200
UI_FORM_MAX_PART_SIZE_BYTES: int = 200 * 1024 * 1024

TYPESENSE_ACTIONS_ALIAS: str = "actions_current"
TYPESENSE_FINISHED_RUNS_ALIAS: str = "finished_runs_current"


def env_int(name: str, *, default: int) -> int:
    """Parse an environment variable as an integer, with a fallback default."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_bool(name: str, *, default: bool) -> bool:
    """Parse an environment variable as a boolean, with a fallback default."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def env_str(name: str, *, default: str) -> str:
    """Parse an environment variable as a trimmed string, with a fallback default."""
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True, slots=True)
class TypesenseSettings:
    """Derived feature flags and aliases for the Typesense read model."""

    enabled: bool = False
    actions_enabled: bool = False
    finished_runs_enabled: bool = False
    fallback_to_mongo: bool = True
    actions_alias: str = TYPESENSE_ACTIONS_ALIAS
    finished_runs_alias: str = TYPESENSE_FINISHED_RUNS_ALIAS

    @classmethod
    def from_env(cls) -> TypesenseSettings:
        """Build Typesense settings from environment variables."""
        enabled = env_bool("FISHTEST_TYPESENSE_ENABLED", default=False)
        return cls(
            enabled=enabled,
            actions_enabled=env_bool(
                "FISHTEST_TYPESENSE_ACTIONS_ENABLED",
                default=enabled,
            ),
            finished_runs_enabled=env_bool(
                "FISHTEST_TYPESENSE_FINISHED_RUNS_ENABLED",
                default=enabled,
            ),
            fallback_to_mongo=env_bool(
                "FISHTEST_TYPESENSE_FALLBACK_TO_MONGO",
                default=True,
            ),
            actions_alias=env_str(
                "FISHTEST_TYPESENSE_ACTIONS_ALIAS",
                default=TYPESENSE_ACTIONS_ALIAS,
            ),
            finished_runs_alias=env_str(
                "FISHTEST_TYPESENSE_FINISHED_RUNS_ALIAS",
                default=TYPESENSE_FINISHED_RUNS_ALIAS,
            ),
        )


def default_static_dir() -> Path:
    """Return the default static directory path for `/static` mounting."""
    env_value = os.environ.get("FISHTEST_STATIC_DIR", "").strip()
    if env_value:
        return Path(env_value).expanduser()

    # Package-relative resolution works for both source checkouts and wheels.
    return Path(__file__).resolve().parents[1] / "static"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Derived runtime settings for the FastAPI server process."""

    port: int
    primary_port: int
    is_primary_instance: bool
    openapi_url: str | None = None
    typesense: TypesenseSettings = TypesenseSettings()

    @classmethod
    def from_env(cls) -> AppSettings:
        """Build settings from environment variables."""
        port = env_int("FISHTEST_PORT", default=-1)
        primary_port = env_int("FISHTEST_PRIMARY_PORT", default=-1)

        # Legacy behavior: if the port number cannot be determined,
        # assume the instance is primary for backward compatibility.
        if port < 0 or primary_port < 0:
            is_primary_instance = True
        else:
            is_primary_instance = port == primary_port

        # OpenAPI docs are disabled in production by default.
        # Set OPENAPI_URL=/openapi.json in development to re-enable
        # /docs, /redoc, and /openapi.json.
        openapi_url_raw = os.environ.get("OPENAPI_URL", "").strip()
        openapi_url: str | None = openapi_url_raw or None

        return cls(
            port=port,
            primary_port=primary_port,
            is_primary_instance=is_primary_instance,
            openapi_url=openapi_url,
            typesense=TypesenseSettings.from_env(),
        )
