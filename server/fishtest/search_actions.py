"""Phase 1 helpers for serving `/actions` from a search backend.

Keep the route policy in `views_actions.py`, while allowing a backend adapter to
provide action rows and fall back to MongoDB when the adapter is unavailable.
"""

from __future__ import annotations

from typing import Any, Protocol


class ActionsSearchUnavailableError(RuntimeError):
    """Raised when the search backend cannot serve `/actions` queries."""


class ActionsSearchService(Protocol):
    """Protocol for a Phase 1 `/actions` search backend service."""

    enabled: bool
    fallback_to_mongo: bool
    shadow_reads_enabled: bool

    def get_actions(
        self,
        *,
        username: str | None = None,
        usernames: list[str] | None = None,
        action: str | None = None,
        text: str | None = None,
        limit: int = 0,
        skip: int = 0,
        utc_before: float | None = None,
        run_id: str | None = None,
        max_count: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def shadow_compare(
        self,
        *,
        mongo_result: tuple[list[dict[str, Any]], int],
        username: str | None = None,
        usernames: list[str] | None = None,
        action: str | None = None,
        text: str | None = None,
        limit: int = 0,
        skip: int = 0,
        utc_before: float | None = None,
        run_id: str | None = None,
        max_count: int | None = None,
    ) -> None: ...

    def record_fallback(self) -> None: ...

    def status_snapshot(self) -> dict[str, Any]: ...


__all__ = [
    "ActionsSearchService",
    "ActionsSearchUnavailableError",
]
