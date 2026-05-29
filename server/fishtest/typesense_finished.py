"""Phase 2 Typesense service for `/tests/finished` shadow indexing and reads."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import ASCENDING, DESCENDING

from fishtest.search_contract import FINISHED_RUNS_SEARCH_CONTRACT
from fishtest.search_finished import FinishedRunsSearchUnavailableError
from fishtest.typesense_client import (
    TypesenseApiError,
    TypesenseClient,
    TypesenseUnavailableError,
)
from fishtest.typesense_runtime import TypesenseRuntimeState

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fishtest.kvstore import KeyValueStore
    from fishtest.rundb import RunDb
    from fishtest.scheduler import Scheduler


class _FinishedRunsCursor(Protocol):
    def hint(self, index: str) -> _FinishedRunsCursor: ...

    def sort(
        self,
        key_or_list: object,
        direction: object = ...,
    ) -> _FinishedRunsCursor: ...

    def limit(self, limit: int) -> _FinishedRunsCursor: ...

    def __iter__(self) -> Iterator[dict[str, Any]]: ...


logger = logging.getLogger(__name__)

_FINISHED_RUNS_SYNC_COLLECTION_KEY = "typesense.finished_runs.collection_name"
_FINISHED_RUNS_SYNC_STATE_KEY = "typesense.finished_runs.sync_state"
_FINISHED_RUNS_BACKFILL_STATE_KEY = "typesense.finished_runs.backfill_state"
_FINISHED_RUNS_SHADOW_READY_KEY = "typesense.finished_runs.shadow_ready"
_FINISHED_RUNS_HYDRATE_PROJECTION = {
    "tasks": 0,
    "bad_tasks": 0,
    "args.spsa.param_history": 0,
}
_FINISHED_RUNS_SYNC_PROJECTION = {
    "args.username": 1,
    "args.info": 1,
    "deleted": 1,
    "finished": 1,
    "is_green": 1,
    "is_yellow": 1,
    "last_updated": 1,
    "tc_base": 1,
}
_MAX_SEARCH_PAGE_SIZE = 250
_FINISHED_TAB_FACET_MAX_VALUES = 4
_SHADOW_COMPARE_LOG_ID_LIMIT = 10
_FINISHED_RUNS_BACKFILL_INTERVAL_SECONDS = 1.0
_FINISHED_RUNS_CURSOR_HINT_NAME = "finished_runs_cursor"
_FINISHED_RUNS_FALLBACK_HINT_NAME = "finished_runs"


@dataclass(frozen=True, slots=True)
class FinishedRunsSyncState:
    """Persistent watermark for the `/tests/finished` polling sync."""

    last_updated: datetime | None = None
    last_id: str = ""

    @classmethod
    def from_value(cls, value: object) -> FinishedRunsSyncState:
        """Build sync state from a persisted key-value payload."""
        if not isinstance(value, dict):
            return cls()
        payload = cast("dict[str, object]", value)
        last_updated = payload.get("last_updated")
        last_id = payload.get("last_id")
        return cls(
            last_updated=_finished_run_last_updated_datetime(last_updated),
            last_id=str(last_id or ""),
        )

    def as_value(self) -> dict[str, Any]:
        """Serialize sync state for key-value storage."""
        return {
            "last_updated": self.last_updated,
            "last_id": self.last_id,
        }

    def is_set(self) -> bool:
        """Return whether the cursor points at a concrete MongoDB row."""
        return self.last_updated is not None and self.last_id != ""


class TypesenseFinishedRunsService:
    """Serve and synchronize `/tests/finished` data against Typesense."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        client: TypesenseClient,
        rundb: RunDb,
        kvstore: KeyValueStore,
        alias: str,
        enabled: bool,
        shadow_reads_enabled: bool,
        fallback_to_mongo: bool,
        sync_batch_size: int,
        sync_interval_seconds: int,
        reindex_interval_seconds: int,
    ) -> None:
        """Store dependencies and runtime settings for finished-run search."""
        self._client = client
        self._rundb = rundb
        self._kvstore = kvstore
        self._alias = alias
        self.enabled = enabled
        self.shadow_reads_enabled = shadow_reads_enabled
        self.fallback_to_mongo = fallback_to_mongo
        self._sync_batch_size = max(1, sync_batch_size)
        self._sync_interval_seconds = max(1, sync_interval_seconds)
        self._reindex_interval_seconds = max(0, reindex_interval_seconds)
        self._ltc_lower_bound = float(rundb.ltc_lower_bound)
        self._sync_lock = threading.Lock()
        self._scheduler_registered = False
        self._runtime = TypesenseRuntimeState(
            route="/tests/finished",
            alias=alias,
            enabled=enabled,
            shadow_reads_enabled=shadow_reads_enabled,
            fallback_to_mongo=fallback_to_mongo,
            sync_interval_seconds=self._sync_interval_seconds,
            reindex_interval_seconds=self._reindex_interval_seconds,
        )

    def close(self) -> None:
        """Close the underlying Typesense client."""
        self._client.close()

    def register_scheduler(self, scheduler: Scheduler | None) -> None:
        """Register the initial backfill and the recurring polling sync."""
        if scheduler is None or self._scheduler_registered:
            return
        scheduler.create_task(
            _FINISHED_RUNS_BACKFILL_INTERVAL_SECONDS,
            self.backfill_finished_runs_once,
            initial_delay=1.0,
            min_delay=_FINISHED_RUNS_BACKFILL_INTERVAL_SECONDS,
            background=True,
        )
        scheduler.create_task(
            self._sync_interval_seconds,
            self.sync_finished_runs_once,
            initial_delay=self._sync_interval_seconds,
            min_delay=self._sync_interval_seconds,
            background=True,
        )
        if self._reindex_interval_seconds > 0:
            scheduler.create_task(
                self._reindex_interval_seconds,
                self.rebuild_index,
                initial_delay=self._reindex_interval_seconds,
                min_delay=self._reindex_interval_seconds,
                background=True,
            )
        self._scheduler_registered = True

    def backfill_finished_runs(self) -> int:
        """Run the initial backfill until the current collection is caught up."""
        imported = 0
        while True:
            batch_count = self.backfill_finished_runs_once()
            if batch_count <= 0:
                return imported
            imported += batch_count

    def backfill_finished_runs_once(self) -> int:
        """Import one historical backfill batch into the current collection."""
        with self._sync_lock:
            collection_name = self._prepare_finished_runs_collection_for_sync()
            seeded = self._seed_recent_finished_runs(collection_name)
            if seeded > 0:
                return seeded

            current_state = self._load_sync_state()
            backfill_state = self._load_backfill_state()
            if not backfill_state.is_set():
                self._store_shadow_compare_ready(ready=True)
                self._runtime.note_sync(
                    imported_count=0,
                    indexed_through=_finished_run_last_updated_timestamp(
                        current_state.last_updated,
                    ),
                    collection_name=collection_name,
                )
                return 0

            batch = self._next_finished_runs_batch_before(backfill_state)
            if not batch:
                self._clear_backfill_state()
                self._store_shadow_compare_ready(ready=True)
                self._runtime.note_sync(
                    imported_count=0,
                    indexed_through=_finished_run_last_updated_timestamp(
                        current_state.last_updated,
                    ),
                    collection_name=collection_name,
                )
                return 0

            documents = [mongo_finished_run_to_typesense_document(run) for run in batch]
            self._client.import_documents(self._alias, documents, action="upsert")

            oldest_run = batch[-1]
            self._store_backfill_state(self._state_from_finished_run(oldest_run))
            self._runtime.note_sync(
                imported_count=len(batch),
                indexed_through=_finished_run_last_updated_timestamp(
                    current_state.last_updated,
                ),
                collection_name=collection_name,
            )
            return len(batch)

    def sync_finished_runs_once(self) -> int:
        """Poll MongoDB for the next batch of finished runs and upsert them."""
        with self._sync_lock:
            collection_name = self._prepare_finished_runs_collection_for_sync()
            seeded = self._seed_recent_finished_runs(collection_name)
            if seeded > 0:
                return seeded

            state = self._load_sync_state()
            batch = self._next_finished_runs_batch_after(state)
            if not batch:
                self._runtime.note_sync(
                    imported_count=0,
                    indexed_through=_finished_run_last_updated_timestamp(
                        state.last_updated,
                    ),
                    collection_name=collection_name,
                )
                return 0

            documents = [mongo_finished_run_to_typesense_document(run) for run in batch]
            self._client.import_documents(self._alias, documents, action="upsert")

            last_run = batch[-1]
            next_state = FinishedRunsSyncState(
                last_updated=_finished_run_last_updated_datetime(
                    last_run.get("last_updated"),
                ),
                last_id=str(last_run.get("_id") or ""),
            )
            self._store_sync_state(next_state)
            self._runtime.note_sync(
                imported_count=len(batch),
                indexed_through=_finished_run_last_updated_timestamp(
                    next_state.last_updated,
                ),
                collection_name=collection_name,
            )
            return len(batch)

    def rebuild_index(self) -> int:
        """Backfill a fresh collection and atomically move the alias to it."""
        with self._sync_lock:
            collection_name = self._fresh_finished_runs_collection_name()
            self._client.create_collection(
                finished_runs_collection_schema(collection_name),
            )

            imported = 0
            state = FinishedRunsSyncState()
            while True:
                batch = self._next_finished_runs_batch_after(state)
                if not batch:
                    break
                documents = [
                    mongo_finished_run_to_typesense_document(run) for run in batch
                ]
                self._client.import_documents(
                    collection_name,
                    documents,
                    action="upsert",
                )
                imported += len(batch)
                last_run = batch[-1]
                state = FinishedRunsSyncState(
                    last_updated=_finished_run_last_updated_datetime(
                        last_run.get("last_updated"),
                    ),
                    last_id=str(last_run.get("_id") or ""),
                )

            self._client.upsert_alias(self._alias, collection_name)
            self._kvstore[_FINISHED_RUNS_SYNC_COLLECTION_KEY] = collection_name
            self._store_sync_state(state)
            self._clear_backfill_state()
            self._store_shadow_compare_ready(ready=True)
            snapshot = self._runtime.note_reindex(
                imported_count=imported,
                indexed_through=_finished_run_last_updated_timestamp(
                    state.last_updated,
                ),
                collection_name=collection_name,
            )
            logger.info(
                "Typesense /tests/finished reindex complete: collection=%s "
                "alias_swap_count=%s indexed_lag_seconds=%s imported=%s",
                collection_name,
                snapshot["alias_swap_count"],
                snapshot["indexed_lag_seconds"],
                imported,
            )
            return imported

    def get_finished_runs(  # noqa: PLR0913
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
    ) -> tuple[list[dict[str, Any]], int]:
        """Search the `/tests/finished` alias and hydrate Mongo-shaped rows."""
        try:
            self.ensure_finished_runs_collection()

            total_requested = limit
            if max_count is not None:
                capped_total = max(0, max_count - skip)
                if capped_total <= 0:
                    return [], max_count
                if total_requested is None:
                    total_requested = capped_total
                else:
                    total_requested = min(total_requested, capped_total)
            if total_requested is None:
                total_requested = _MAX_SEARCH_PAGE_SIZE

            hits: list[dict[str, Any]] = []
            found = 0
            next_offset = skip
            remaining = total_requested
            while remaining > 0:
                page_limit = min(remaining, _MAX_SEARCH_PAGE_SIZE)
                payload = self._client.search(
                    self._alias,
                    build_finished_runs_search_params(
                        username=username,
                        usernames=usernames,
                        text=text,
                        success_only=success_only,
                        yellow_only=yellow_only,
                        ltc_only=ltc_only,
                        ltc_lower_bound=self._ltc_lower_bound,
                        limit=page_limit,
                        offset=next_offset,
                    ),
                )
                found = int(payload.get("found") or 0)
                page_hits = list(payload.get("hits") or [])
                hits.extend(page_hits)
                if len(page_hits) < page_limit:
                    break
                next_offset += len(page_hits)
                remaining -= len(page_hits)

            total = min(found, max_count) if max_count is not None else found
            return self._hydrate_finished_runs(hits), total
        except (TypesenseUnavailableError, TypesenseApiError) as exc:
            snapshot = self._runtime.note_backend_unavailable(exc)
            logger.warning(
                "Typesense /tests/finished backend unavailable: "
                "backend_unavailable_count=%s error=%s",
                snapshot["backend_unavailable_count"],
                snapshot["last_error"],
            )
            raise FinishedRunsSearchUnavailableError(str(exc)) from exc

    def shadow_compare(  # noqa: PLR0913
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
    ) -> None:
        """Compare a Mongo-served result with the equivalent Typesense query."""
        if not self._shadow_compare_ready():
            return

        try:
            search_rows, search_total = self.get_finished_runs(
                username=username,
                usernames=usernames,
                text=text,
                success_only=success_only,
                yellow_only=yellow_only,
                ltc_only=ltc_only,
                skip=skip,
                limit=limit,
                max_count=max_count,
            )
        except FinishedRunsSearchUnavailableError:
            return

        mongo_rows, mongo_total = mongo_result
        mongo_ids = [str(row.get("_id") or "") for row in mongo_rows]
        search_ids = [str(row.get("_id") or "") for row in search_rows]
        count_mismatch = mongo_total != search_total
        result_mismatch = mongo_ids != search_ids
        if count_mismatch or result_mismatch:
            snapshot = self._runtime.note_shadow_mismatch(
                count_mismatch=count_mismatch,
                result_mismatch=result_mismatch,
            )
            logger.warning(
                "Typesense /tests/finished shadow mismatch: "
                "mongo_total=%s search_total=%s count_mismatch_count=%s "
                "result_mismatch_count=%s params=%s",
                mongo_total,
                search_total,
                snapshot["count_mismatch_count"],
                snapshot["result_mismatch_count"],
                _shadow_compare_log_params(
                    username=username,
                    usernames=usernames,
                    text=text,
                    success_only=success_only,
                    yellow_only=yellow_only,
                    ltc_only=ltc_only,
                    skip=skip,
                    limit=limit,
                    max_count=max_count,
                    mongo_ids=mongo_ids,
                    search_ids=search_ids,
                ),
            )

    def record_fallback(self) -> None:
        """Record a request that fell back to MongoDB."""
        snapshot = self._runtime.note_fallback()
        logger.warning(
            "Typesense /tests/finished fallback to MongoDB: fallback_count=%s",
            snapshot["fallback_count"],
        )

    def status_snapshot(self) -> dict[str, Any]:
        """Return operational counters and current sync lag."""
        snapshot = self._runtime.snapshot()
        snapshot["collection_document_count"] = self._collection_document_count(
            str(snapshot.get("collection_name") or ""),
        )
        shadow_compare_ready = self._shadow_compare_ready()
        snapshot["shadow_compare_ready"] = shadow_compare_ready
        backfill_state = self._load_backfill_state()
        if backfill_state.is_set() or not shadow_compare_ready:
            snapshot["backfill_through"] = _finished_run_last_updated_timestamp(
                backfill_state.last_updated,
            )
        return snapshot

    def _collection_document_count(self, collection_name: str) -> int | None:
        if not collection_name:
            return None
        try:
            collection = self._client.get_collection(
                collection_name,
                allow_missing=True,
            )
        except TypesenseUnavailableError, TypesenseApiError:
            return None
        if not isinstance(collection, dict):
            return None
        count = collection.get("num_documents")
        return int(count) if isinstance(count, int | float) else None

    def get_finished_runs_tab_counts(self) -> dict[str, int]:
        """Return additive counts for the `/tests/finished` navigation tabs."""
        try:
            self.ensure_finished_runs_collection()
            base_payload = self._client.search(
                self._alias,
                build_finished_runs_tab_counts_params(),
            )
            ltc_payload = self._client.search(
                self._alias,
                build_finished_runs_count_params(
                    ltc_only=True,
                    ltc_lower_bound=self._ltc_lower_bound,
                ),
            )
            return finished_runs_tab_counts_from_payloads(
                base_payload=base_payload,
                ltc_payload=ltc_payload,
            )
        except (TypesenseUnavailableError, TypesenseApiError) as exc:
            snapshot = self._runtime.note_backend_unavailable(exc)
            logger.warning(
                "Typesense /tests/finished backend unavailable: "
                "backend_unavailable_count=%s error=%s",
                snapshot["backend_unavailable_count"],
                snapshot["last_error"],
            )
            raise FinishedRunsSearchUnavailableError(str(exc)) from exc

    def ensure_finished_runs_collection(self) -> str:
        """Ensure the current `/tests/finished` alias resolves to a collection."""
        alias_info = self._client.get_alias(self._alias, allow_missing=True)
        if isinstance(alias_info, dict):
            collection_name = str(alias_info.get("collection_name") or "")
            if collection_name:
                self._kvstore[_FINISHED_RUNS_SYNC_COLLECTION_KEY] = collection_name
                self._runtime.note_collection(collection_name)
                return collection_name

        stored_collection = self._kvstore.get(_FINISHED_RUNS_SYNC_COLLECTION_KEY, "")
        collection_name = str(
            stored_collection or timestamped_finished_runs_collection_name(),
        )
        if self._client.get_collection(collection_name, allow_missing=True) is None:
            self._client.create_collection(
                finished_runs_collection_schema(collection_name),
            )
        self._client.upsert_alias(self._alias, collection_name)
        self._kvstore[_FINISHED_RUNS_SYNC_COLLECTION_KEY] = collection_name
        self._store_shadow_compare_ready(ready=False)
        self._runtime.note_collection(collection_name)
        return collection_name

    def _prepare_finished_runs_collection_for_sync(self) -> str:
        collection_name = self.ensure_finished_runs_collection()
        if not self._should_reset_legacy_partial_collection():
            return collection_name
        return self._reset_legacy_partial_collection(collection_name)

    @staticmethod
    def _finished_runs_sync_query() -> dict[str, Any]:
        return {"finished": True, "deleted": False}

    def _finished_runs_sync_cursor(self, query: dict[str, Any]) -> _FinishedRunsCursor:
        cursor = self._rundb.runs.find(query, _FINISHED_RUNS_SYNC_PROJECTION)
        hint_name = self._finished_runs_sync_hint_name()
        if not hint_name:
            return cast("_FinishedRunsCursor", cursor)
        apply_hint = getattr(cursor, "hint", None)
        if not callable(apply_hint):
            return cast("_FinishedRunsCursor", cursor)
        return cast("_FinishedRunsCursor", apply_hint(hint_name))

    def _finished_runs_sync_hint_name(self) -> str:
        get_index_names = getattr(self._rundb, "get_runs_index_names", None)
        if not callable(get_index_names):
            return ""
        index_names = set(get_index_names())
        if _FINISHED_RUNS_CURSOR_HINT_NAME in index_names:
            return _FINISHED_RUNS_CURSOR_HINT_NAME
        if _FINISHED_RUNS_FALLBACK_HINT_NAME in index_names:
            return _FINISHED_RUNS_FALLBACK_HINT_NAME
        return ""

    def _next_finished_runs_batch_after(
        self,
        state: FinishedRunsSyncState,
    ) -> list[dict[str, Any]]:
        query = self._finished_runs_sync_query()
        if state.last_updated is not None and state.last_id:
            query["$or"] = [
                {"last_updated": {"$gt": state.last_updated}},
                {
                    "last_updated": state.last_updated,
                    "_id": {"$gt": ObjectId(state.last_id)},
                },
            ]
        cursor = self._finished_runs_sync_cursor(query)
        cursor = cursor.sort([("last_updated", ASCENDING), ("_id", ASCENDING)])
        cursor = cursor.limit(self._sync_batch_size)
        return list(cursor)

    def _next_finished_runs_batch_before(
        self,
        state: FinishedRunsSyncState,
    ) -> list[dict[str, Any]]:
        query = self._finished_runs_sync_query()
        if state.is_set():
            query["$or"] = [
                {"last_updated": {"$lt": state.last_updated}},
                {
                    "last_updated": state.last_updated,
                    "_id": {"$lt": ObjectId(state.last_id)},
                },
            ]
        cursor = self._finished_runs_sync_cursor(query)
        cursor = cursor.sort([("last_updated", DESCENDING), ("_id", DESCENDING)])
        cursor = cursor.limit(self._sync_batch_size)
        return list(cursor)

    def _latest_finished_runs_batch(self) -> list[dict[str, Any]]:
        cursor = self._finished_runs_sync_cursor(self._finished_runs_sync_query())
        cursor = cursor.sort([("last_updated", DESCENDING), ("_id", DESCENDING)])
        cursor = cursor.limit(self._sync_batch_size)
        return list(cursor)

    def _fresh_finished_runs_collection_name(self) -> str:
        base_time = datetime.now(UTC)
        for offset_seconds in range(120):
            candidate = timestamped_finished_runs_collection_name(
                base_time + timedelta(seconds=offset_seconds),
            )
            if self._client.get_collection(candidate, allow_missing=True) is None:
                return candidate
        return f"{timestamped_finished_runs_collection_name(base_time)}_reindex"

    def _hydrate_finished_runs(
        self,
        hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        run_ids = [
            str((hit.get("document") or {}).get("id") or "")
            for hit in hits
            if str((hit.get("document") or {}).get("id") or "")
        ]
        if not run_ids:
            return []

        object_ids = []
        for run_id in run_ids:
            try:
                object_ids.append(ObjectId(run_id))
            except InvalidId:
                logger.warning(
                    "Skipping invalid Typesense finished-run id during hydration: %s",
                    run_id,
                )

        if not object_ids:
            return []

        rows = self._rundb.runs.find(
            {"_id": {"$in": object_ids}},
            _FINISHED_RUNS_HYDRATE_PROJECTION,
        )
        rows_by_id = {str(row.get("_id") or ""): row for row in rows}
        hydrated = [rows_by_id[run_id] for run_id in run_ids if run_id in rows_by_id]
        if len(hydrated) != len(run_ids):
            logger.warning(
                "Typesense /tests/finished hydration mismatch: "
                "requested=%s hydrated=%s ids=%s",
                len(run_ids),
                len(hydrated),
                run_ids,
            )
        return hydrated

    def _load_sync_state(self) -> FinishedRunsSyncState:
        return FinishedRunsSyncState.from_value(
            self._kvstore.get(_FINISHED_RUNS_SYNC_STATE_KEY, {}),
        )

    def _store_sync_state(self, state: FinishedRunsSyncState) -> None:
        self._kvstore[_FINISHED_RUNS_SYNC_STATE_KEY] = state.as_value()

    def _clear_sync_state(self) -> None:
        self._kvstore[_FINISHED_RUNS_SYNC_STATE_KEY] = {}

    def _load_backfill_state(self) -> FinishedRunsSyncState:
        return FinishedRunsSyncState.from_value(
            self._kvstore.get(_FINISHED_RUNS_BACKFILL_STATE_KEY, {}),
        )

    def _store_backfill_state(self, state: FinishedRunsSyncState) -> None:
        self._kvstore[_FINISHED_RUNS_BACKFILL_STATE_KEY] = state.as_value()

    def _clear_backfill_state(self) -> None:
        self._kvstore[_FINISHED_RUNS_BACKFILL_STATE_KEY] = {}

    def _shadow_compare_ready(self) -> bool:
        return bool(self._kvstore.get(_FINISHED_RUNS_SHADOW_READY_KEY, False))

    def _store_shadow_compare_ready(self, *, ready: bool) -> None:
        self._kvstore[_FINISHED_RUNS_SHADOW_READY_KEY] = ready

    def _seed_recent_finished_runs(self, collection_name: str) -> int:
        current_state = self._load_sync_state()
        if current_state.is_set():
            return 0

        batch = self._latest_finished_runs_batch()
        if not batch:
            self._store_shadow_compare_ready(ready=True)
            self._runtime.note_sync(
                imported_count=0,
                indexed_through=None,
                collection_name=collection_name,
            )
            return 0

        documents = [mongo_finished_run_to_typesense_document(run) for run in batch]
        self._client.import_documents(self._alias, documents, action="upsert")

        newest_run = batch[0]
        oldest_run = batch[-1]
        self._store_sync_state(self._state_from_finished_run(newest_run))
        self._store_backfill_state(self._state_from_finished_run(oldest_run))
        self._store_shadow_compare_ready(ready=False)
        self._runtime.note_sync(
            imported_count=len(batch),
            indexed_through=_finished_run_last_updated_timestamp(
                _finished_run_last_updated_datetime(newest_run.get("last_updated")),
            ),
            collection_name=collection_name,
        )
        return len(batch)

    def _should_reset_legacy_partial_collection(self) -> bool:
        return (
            not self._shadow_compare_ready()
            and not self._load_backfill_state().is_set()
            and self._load_sync_state().is_set()
        )

    def _reset_legacy_partial_collection(self, collection_name: str) -> str:
        new_collection_name = self._fresh_finished_runs_collection_name()
        self._client.create_collection(
            finished_runs_collection_schema(new_collection_name),
        )
        self._client.upsert_alias(self._alias, new_collection_name)
        self._kvstore[_FINISHED_RUNS_SYNC_COLLECTION_KEY] = new_collection_name
        self._clear_sync_state()
        self._clear_backfill_state()
        self._store_shadow_compare_ready(ready=False)
        self._runtime.note_collection(new_collection_name)
        logger.info(
            "Typesense /tests/finished reset legacy partial collection: "
            "old_collection=%s new_collection=%s",
            collection_name,
            new_collection_name,
        )
        return new_collection_name

    @staticmethod
    def _state_from_finished_run(run: dict[str, Any]) -> FinishedRunsSyncState:
        return FinishedRunsSyncState(
            last_updated=_finished_run_last_updated_datetime(run.get("last_updated")),
            last_id=str(run.get("_id") or ""),
        )


def timestamped_finished_runs_collection_name(now: datetime | None = None) -> str:
    """Return a timestamped collection name for alias-based reindexing."""
    current = now or datetime.now(UTC)
    return (
        f"{FINISHED_RUNS_SEARCH_CONTRACT.collection_prefix}_"
        f"{current.strftime('%Y%m%d%H%M%S')}"
    )


def finished_runs_collection_schema(collection_name: str) -> dict[str, Any]:
    """Return the Phase 2 schema for the shadow `/tests/finished` collection."""
    return {
        "name": collection_name,
        "enable_nested_fields": True,
        "fields": [
            {"name": "last_updated", "type": "int64", "sort": True},
            {"name": "args.username", "type": "string", "facet": True},
            {"name": "args.info", "type": "string"},
            {"name": "finished", "type": "bool", "facet": True},
            {"name": "deleted", "type": "bool", "facet": True},
            {
                "name": "is_green",
                "type": "bool",
                "facet": True,
                "optional": True,
            },
            {
                "name": "is_yellow",
                "type": "bool",
                "facet": True,
                "optional": True,
            },
            {"name": "tc_base", "type": "float", "optional": True},
        ],
        "default_sorting_field": FINISHED_RUNS_SEARCH_CONTRACT.default_sort_field,
    }


def mongo_finished_run_to_typesense_document(run: dict[str, Any]) -> dict[str, Any]:
    """Map a MongoDB finished run document to a Typesense document."""
    args = dict(run.get("args") or {})
    document: dict[str, Any] = {
        "id": str(run.get("_id") or ""),
        "last_updated": _finished_run_last_updated_index_value(
            run.get("last_updated"),
        ),
        "args": {
            "username": str(args.get("username") or ""),
            "info": str(args.get("info") or ""),
        },
        "finished": bool(run.get("finished", False)),
        "deleted": bool(run.get("deleted", False)),
        "is_green": bool(run.get("is_green", False)),
        "is_yellow": bool(run.get("is_yellow", False)),
    }
    tc_base = run.get("tc_base")
    if tc_base is not None:
        document["tc_base"] = float(tc_base)
    return document


def build_finished_runs_search_params(  # noqa: PLR0913
    *,
    username: str | None = None,
    usernames: list[str] | None = None,
    text: str | None = None,
    success_only: bool = False,
    yellow_only: bool = False,
    ltc_only: bool = False,
    ltc_lower_bound: float,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Translate the `/tests/finished` route contract to Typesense params."""
    params: dict[str, Any] = {
        "q": text or "*",
        "query_by": "args.info",
        "sort_by": (
            "last_updated:desc,_text_match:desc" if text else "last_updated:desc"
        ),
        "limit": limit,
        "offset": offset,
        "include_fields": "id",
        "prefix": "false",
        "num_typos": "0",
        "drop_tokens_threshold": 0,
        "typo_tokens_threshold": 0,
        "split_join_tokens": "off",
        "exhaustive_search": "true",
    }
    filters = build_finished_runs_filter_by(
        username=username,
        usernames=usernames,
        success_only=success_only,
        yellow_only=yellow_only,
        ltc_only=ltc_only,
        ltc_lower_bound=ltc_lower_bound,
    )
    if filters:
        params["filter_by"] = filters
    return params


def build_finished_runs_count_params(
    *,
    success_only: bool = False,
    yellow_only: bool = False,
    ltc_only: bool = False,
    ltc_lower_bound: float,
) -> dict[str, Any]:
    """Build a count-only Typesense query for `/tests/finished`."""
    params: dict[str, Any] = {
        "q": "*",
        "query_by": "args.info",
        "per_page": 0,
        "prefix": "false",
        "num_typos": "0",
        "drop_tokens_threshold": 0,
        "typo_tokens_threshold": 0,
        "split_join_tokens": "off",
        "exhaustive_search": "true",
    }
    filters = build_finished_runs_filter_by(
        success_only=success_only,
        yellow_only=yellow_only,
        ltc_only=ltc_only,
        ltc_lower_bound=ltc_lower_bound,
    )
    if filters:
        params["filter_by"] = filters
    return params


def build_finished_runs_tab_counts_params() -> dict[str, Any]:
    """Build a facet query for `/tests/finished` navigation-tab counts."""
    params = build_finished_runs_count_params(ltc_lower_bound=0.0)
    params.update(
        {
            "facet_by": "is_green,is_yellow",
            "facet_strategy": "exhaustive",
            "max_facet_values": _FINISHED_TAB_FACET_MAX_VALUES,
        },
    )
    return params


def build_finished_runs_filter_by(  # noqa: PLR0913
    *,
    username: str | None = None,
    usernames: list[str] | None = None,
    success_only: bool = False,
    yellow_only: bool = False,
    ltc_only: bool = False,
    ltc_lower_bound: float,
) -> str:
    """Build the exact-match Typesense filter clause for `/tests/finished`."""
    clauses = ["finished:=true", "deleted:=false"]
    if usernames:
        values = ", ".join(_quote_typesense_value(name) for name in usernames)
        clauses.append(f"args.username:=[{values}]")
    elif username:
        clauses.append(f"args.username:={_quote_typesense_value(username)}")

    if success_only:
        clauses.append("is_green:=true")
    if yellow_only:
        clauses.append("is_yellow:=true")
    if ltc_only:
        clauses.append(f"tc_base:>={float(ltc_lower_bound)}")
    return " && ".join(clauses)


def _finished_run_last_updated_sort_value(run: dict[str, Any]) -> float:
    last_updated = run.get("last_updated")
    if isinstance(last_updated, datetime):
        return last_updated.timestamp()
    if isinstance(last_updated, int | float):
        return float(last_updated)
    return 0.0


def _finished_run_last_updated_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), UTC)
    return None


def _finished_run_last_updated_timestamp(value: datetime | None) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    return None


def _finished_run_last_updated_index_value(value: object) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, int | float):
        return int(float(value) * 1000)
    return 0


def _shadow_compare_log_params(  # noqa: PLR0913
    *,
    username: str | None,
    usernames: list[str] | None,
    text: str | None,
    success_only: bool,
    yellow_only: bool,
    ltc_only: bool,
    skip: int,
    limit: int | None,
    max_count: int | None,
    mongo_ids: list[str],
    search_ids: list[str],
) -> dict[str, Any]:
    return {
        "username": username,
        "usernames": usernames,
        "text": text,
        "success_only": success_only,
        "yellow_only": yellow_only,
        "ltc_only": ltc_only,
        "skip": skip,
        "limit": limit,
        "max_count": max_count,
        "mongo_ids": _log_id_preview(mongo_ids),
        "search_ids": _log_id_preview(search_ids),
        "mongo_ids_total": len(mongo_ids),
        "search_ids_total": len(search_ids),
    }


def _log_id_preview(ids: list[str]) -> list[str]:
    return ids[:_SHADOW_COMPARE_LOG_ID_LIMIT]


def _quote_typesense_value(value: str) -> str:
    escaped = value.replace("`", "\\`")
    return f"`{escaped}`"


def _finished_boolean_facet_true_count(
    payload: dict[str, Any],
    *,
    field_name: str,
) -> int:
    for facet in payload.get("facet_counts") or []:
        if str(facet.get("field_name") or "") != field_name:
            continue
        for item in facet.get("counts") or []:
            value = str(item.get("value") or "").lower()
            if value == "true":
                return int(item.get("count") or 0)
        return 0
    return 0


def finished_runs_tab_counts_from_payloads(
    *,
    base_payload: dict[str, Any],
    ltc_payload: dict[str, Any],
) -> dict[str, int]:
    """Extract navigation-tab counts from Typesense search payloads."""
    return {
        "all": int(base_payload.get("found") or 0),
        "green": _finished_boolean_facet_true_count(
            base_payload,
            field_name="is_green",
        ),
        "yellow": _finished_boolean_facet_true_count(
            base_payload,
            field_name="is_yellow",
        ),
        "ltc": int(ltc_payload.get("found") or 0),
    }


__all__ = [
    "FinishedRunsSyncState",
    "TypesenseFinishedRunsService",
    "build_finished_runs_count_params",
    "build_finished_runs_filter_by",
    "build_finished_runs_search_params",
    "build_finished_runs_tab_counts_params",
    "finished_runs_collection_schema",
    "finished_runs_tab_counts_from_payloads",
    "mongo_finished_run_to_typesense_document",
    "timestamped_finished_runs_collection_name",
]
