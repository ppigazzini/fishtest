"""Operational runtime tracking for Typesense-backed search services."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any


def _utc_timestamp(now: datetime | None = None) -> float:
    current = now or datetime.now(UTC)
    return current.timestamp()


class TypesenseRuntimeState:
    """Track operational counters and watermarks for one search service."""

    def __init__(
        self,
        *,
        route: str,
        alias: str,
        enabled: bool,
        shadow_reads_enabled: bool,
        fallback_to_mongo: bool,
        sync_interval_seconds: int,
        reindex_interval_seconds: int,
    ) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "route": route,
            "alias": alias,
            "collection_name": "",
            "enabled": enabled,
            "shadow_reads_enabled": shadow_reads_enabled,
            "fallback_to_mongo": fallback_to_mongo,
            "sync_interval_seconds": sync_interval_seconds,
            "reindex_interval_seconds": reindex_interval_seconds,
            "last_sync_completed_at": None,
            "last_reindex_completed_at": None,
            "last_fallback_at": None,
            "last_backend_unavailable_at": None,
            "last_indexed_through": None,
            "sync_batches": 0,
            "synced_document_count": 0,
            "last_reindex_document_count": 0,
            "alias_swap_count": 0,
            "fallback_count": 0,
            "backend_unavailable_count": 0,
            "count_mismatch_count": 0,
            "result_mismatch_count": 0,
            "last_error": "",
        }

    def note_collection(self, collection_name: str) -> None:
        with self._lock:
            self._state["collection_name"] = collection_name

    def note_sync(
        self,
        *,
        imported_count: int,
        indexed_through: float | None,
        collection_name: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._state["collection_name"] = collection_name
            self._state["last_sync_completed_at"] = _utc_timestamp(now)
            self._state["last_indexed_through"] = indexed_through
            self._state["sync_batches"] += 1
            self._state["synced_document_count"] += imported_count
            return self._snapshot_locked(now)

    def note_reindex(
        self,
        *,
        imported_count: int,
        indexed_through: float | None,
        collection_name: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            current_time = _utc_timestamp(now)
            self._state["collection_name"] = collection_name
            self._state["last_reindex_completed_at"] = current_time
            self._state["last_sync_completed_at"] = current_time
            self._state["last_indexed_through"] = indexed_through
            self._state["last_reindex_document_count"] = imported_count
            self._state["alias_swap_count"] += 1
            return self._snapshot_locked(now)

    def note_backend_unavailable(
        self,
        error: Exception | str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._state["last_backend_unavailable_at"] = _utc_timestamp(now)
            self._state["backend_unavailable_count"] += 1
            self._state["last_error"] = str(error)
            return self._snapshot_locked(now)

    def note_fallback(self, *, now: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            self._state["last_fallback_at"] = _utc_timestamp(now)
            self._state["fallback_count"] += 1
            return self._snapshot_locked(now)

    def note_shadow_mismatch(
        self,
        *,
        count_mismatch: bool,
        result_mismatch: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if count_mismatch:
                self._state["count_mismatch_count"] += 1
            if result_mismatch:
                self._state["result_mismatch_count"] += 1
            return self._snapshot_locked(now)

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(now)

    def _snapshot_locked(self, now: datetime | None) -> dict[str, Any]:
        snapshot = dict(self._state)
        last_indexed_through = snapshot.get("last_indexed_through")
        if isinstance(last_indexed_through, int | float):
            snapshot["indexed_lag_seconds"] = max(
                0.0,
                _utc_timestamp(now) - float(last_indexed_through),
            )
        else:
            snapshot["indexed_lag_seconds"] = None
        return snapshot


__all__ = ["TypesenseRuntimeState"]
