"""Phase 2 Typesense service for `/tests/finished` shadow indexing and reads."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import ASCENDING

from fishtest.search_contract import FINISHED_RUNS_SEARCH_CONTRACT
from fishtest.search_finished import FinishedRunsSearchUnavailableError
from fishtest.typesense_client import (
    TypesenseApiError,
    TypesenseClient,
    TypesenseUnavailableError,
)

if TYPE_CHECKING:
    from fishtest.kvstore import KeyValueStore
    from fishtest.rundb import RunDb
    from fishtest.scheduler import Scheduler


logger = logging.getLogger(__name__)

_FINISHED_RUNS_SYNC_COLLECTION_KEY = "typesense.finished_runs.collection_name"
_FINISHED_RUNS_SYNC_STATE_KEY = "typesense.finished_runs.sync_state"
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


@dataclass(frozen=True, slots=True)
class FinishedRunsSyncState:
    """Persistent watermark for the `/tests/finished` polling sync."""

    last_updated: float | None = None
    last_id: str = ""

    @classmethod
    def from_value(cls, value: object) -> FinishedRunsSyncState:
        if not isinstance(value, dict):
            return cls()
        last_updated = value.get("last_updated")
        last_id = value.get("last_id")
        return cls(
            last_updated=(
                float(last_updated) if isinstance(last_updated, int | float) else None
            ),
            last_id=str(last_id or ""),
        )

    def as_value(self) -> dict[str, Any]:
        return {
            "last_updated": self.last_updated,
            "last_id": self.last_id,
        }


class TypesenseFinishedRunsService:
    """Serve and synchronize `/tests/finished` data against Typesense."""

    def __init__(
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
    ) -> None:
        self._client = client
        self._rundb = rundb
        self._kvstore = kvstore
        self._alias = alias
        self.enabled = enabled
        self.shadow_reads_enabled = shadow_reads_enabled
        self.fallback_to_mongo = fallback_to_mongo
        self._sync_batch_size = max(1, sync_batch_size)
        self._sync_interval_seconds = max(1, sync_interval_seconds)
        self._ltc_lower_bound = float(rundb.ltc_lower_bound)
        self._sync_lock = threading.Lock()
        self._scheduler_registered = False

    def close(self) -> None:
        """Close the underlying Typesense client."""
        self._client.close()

    def register_scheduler(self, scheduler: Scheduler | None) -> None:
        """Register the initial backfill and the recurring polling sync."""
        if scheduler is None or self._scheduler_registered:
            return
        scheduler.create_task(
            1.0,
            self.backfill_finished_runs,
            initial_delay=1.0,
            one_shot=True,
            background=True,
        )
        scheduler.create_task(
            self._sync_interval_seconds,
            self.sync_finished_runs_once,
            initial_delay=self._sync_interval_seconds,
            min_delay=self._sync_interval_seconds,
            background=True,
        )
        self._scheduler_registered = True

    def backfill_finished_runs(self) -> int:
        """Run the initial backfill until the current collection is caught up."""
        imported = 0
        while True:
            batch_count = self.sync_finished_runs_once()
            if batch_count <= 0:
                return imported
            imported += batch_count

    def sync_finished_runs_once(self) -> int:
        """Poll MongoDB for the next batch of finished runs and upsert them."""
        with self._sync_lock:
            self.ensure_finished_runs_collection()
            batch = self._next_finished_runs_batch()
            if not batch:
                return 0

            documents = [mongo_finished_run_to_typesense_document(run) for run in batch]
            self._client.import_documents(self._alias, documents, action="upsert")

            last_run = batch[-1]
            self._store_sync_state(
                FinishedRunsSyncState(
                    last_updated=_finished_run_last_updated_sort_value(last_run),
                    last_id=str(last_run.get("_id") or ""),
                ),
            )
            return len(batch)

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
            raise FinishedRunsSearchUnavailableError(str(exc)) from exc

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
    ) -> None:
        """Compare a Mongo-served result with the equivalent Typesense query."""
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
        if mongo_total != search_total or mongo_ids != search_ids:
            logger.warning(
                "Typesense /tests/finished shadow mismatch: mongo_total=%s search_total=%s params=%s",
                mongo_total,
                search_total,
                {
                    "username": username,
                    "usernames": usernames,
                    "text": text,
                    "success_only": success_only,
                    "yellow_only": yellow_only,
                    "ltc_only": ltc_only,
                    "skip": skip,
                    "limit": limit,
                    "max_count": max_count,
                    "mongo_ids": mongo_ids,
                    "search_ids": search_ids,
                },
            )

    def ensure_finished_runs_collection(self) -> str:
        """Ensure the current `/tests/finished` alias resolves to a collection."""
        alias_info = self._client.get_alias(self._alias, allow_missing=True)
        if isinstance(alias_info, dict):
            collection_name = str(alias_info.get("collection_name") or "")
            if collection_name:
                self._kvstore[_FINISHED_RUNS_SYNC_COLLECTION_KEY] = collection_name
                return collection_name

        stored_collection = self._kvstore.get(_FINISHED_RUNS_SYNC_COLLECTION_KEY, "")
        collection_name = str(
            stored_collection or timestamped_finished_runs_collection_name()
        )
        if self._client.get_collection(collection_name, allow_missing=True) is None:
            self._client.create_collection(
                finished_runs_collection_schema(collection_name),
            )
        self._client.upsert_alias(self._alias, collection_name)
        self._kvstore[_FINISHED_RUNS_SYNC_COLLECTION_KEY] = collection_name
        return collection_name

    def _next_finished_runs_batch(self) -> list[dict[str, Any]]:
        state = self._load_sync_state()
        query: dict[str, Any] = {"finished": True}
        if state.last_updated is not None and state.last_id:
            query["$or"] = [
                {"last_updated": {"$gt": state.last_updated}},
                {
                    "last_updated": state.last_updated,
                    "_id": {"$gt": ObjectId(state.last_id)},
                },
            ]
        cursor = self._rundb.runs.find(query, _FINISHED_RUNS_SYNC_PROJECTION)
        cursor = cursor.sort([("last_updated", ASCENDING), ("_id", ASCENDING)])
        cursor = cursor.limit(self._sync_batch_size)
        return list(cursor)

    def _hydrate_finished_runs(
        self, hits: list[dict[str, Any]]
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
                "Typesense /tests/finished hydration mismatch: requested=%s hydrated=%s ids=%s",
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
        "last_updated": int(_finished_run_last_updated_sort_value(run)),
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


def build_finished_runs_search_params(
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


def build_finished_runs_filter_by(
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


def _quote_typesense_value(value: str) -> str:
    escaped = value.replace("`", "\\`")
    return f"`{escaped}`"


__all__ = [
    "FinishedRunsSyncState",
    "TypesenseFinishedRunsService",
    "build_finished_runs_filter_by",
    "build_finished_runs_search_params",
    "finished_runs_collection_schema",
    "mongo_finished_run_to_typesense_document",
    "timestamped_finished_runs_collection_name",
]
