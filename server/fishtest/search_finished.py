"""Phase 2 helpers for serving `/tests/finished` from a search backend.

Keep the route policy in `views_finished.py`, while allowing a backend adapter to
provide ordered finished-run rows and fall back to MongoDB when the adapter is
unavailable.
"""

from __future__ import annotations

from typing import Any, Protocol


class FinishedRunsSearchUnavailableError(RuntimeError):
    """Raised when the search backend cannot serve `/tests/finished` queries."""


class FinishedRunsSearchService(Protocol):
    """Protocol for a Phase 2 `/tests/finished` search backend service."""

    enabled: bool
    fallback_to_mongo: bool
    shadow_reads_enabled: bool

    def get_finished_runs(
        self,
        *,
        username: str | None = None,
        usernames: list[str] | None = None,
        text: str | None = None,
        success_only: bool = False,
        yellow_only: bool = False,
        ltc_only: bool = False,
        skip: int = 0,
        limit: int | None = None,
        max_count: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def shadow_compare(
        self,
        *,
        mongo_result: tuple[list[dict[str, Any]], int],
        username: str | None = None,
        usernames: list[str] | None = None,
        text: str | None = None,
        success_only: bool = False,
        yellow_only: bool = False,
        ltc_only: bool = False,
        skip: int = 0,
        limit: int | None = None,
        max_count: int | None = None,
    ) -> None: ...


__all__ = [
    "FinishedRunsSearchService",
    "FinishedRunsSearchUnavailableError",
]
