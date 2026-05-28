"""Phase 1 Typesense service for `/actions` shadow indexing and reads."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bson.objectid import ObjectId
from pymongo import ASCENDING

from fishtest.search_actions import ActionsSearchUnavailableError
from fishtest.search_contract import ACTIONS_SEARCH_CONTRACT
from fishtest.typesense_client import (
    TypesenseApiError,
    TypesenseClient,
    TypesenseUnavailableError,
)
from fishtest.typesense_runtime import TypesenseRuntimeState

if TYPE_CHECKING:
    from fishtest.actiondb import ActionDb
    from fishtest.kvstore import KeyValueStore
    from fishtest.scheduler import Scheduler


logger = logging.getLogger(__name__)

_ACTIONS_SYNC_COLLECTION_KEY = "typesense.actions.collection_name"
_ACTIONS_SYNC_STATE_KEY = "typesense.actions.sync_state"
_MAX_SEARCH_PAGE_SIZE = 250
_ACTION_FACET_MAX_VALUES = 32
_DEFAULT_EXCLUDED_ACTIONS = (
    "system_event",
    "update_stats",
    "dead_task",
)


@dataclass(frozen=True, slots=True)
class ActionsSyncState:
    """Persistent watermark for the `/actions` polling sync."""

    last_time: float | None = None
    last_id: str = ""

    @classmethod
    def from_value(cls, value: object) -> ActionsSyncState:
        if not isinstance(value, dict):
            return cls()
        last_time = value.get("last_time")
        last_id = value.get("last_id")
        return cls(
            last_time=float(last_time) if isinstance(last_time, int | float) else None,
            last_id=str(last_id or ""),
        )

    def as_value(self) -> dict[str, Any]:
        return {
            "last_time": self.last_time,
            "last_id": self.last_id,
        }


class TypesenseActionsService:
    """Serve and synchronize `/actions` data against Typesense."""

    def __init__(
        self,
        *,
        client: TypesenseClient,
        actiondb: ActionDb,
        kvstore: KeyValueStore,
        alias: str,
        enabled: bool,
        shadow_reads_enabled: bool,
        fallback_to_mongo: bool,
        sync_batch_size: int,
        sync_interval_seconds: int,
        reindex_interval_seconds: int,
    ) -> None:
        self._client = client
        self._actiondb = actiondb
        self._kvstore = kvstore
        self._alias = alias
        self.enabled = enabled
        self.shadow_reads_enabled = shadow_reads_enabled
        self.fallback_to_mongo = fallback_to_mongo
        self._sync_batch_size = max(1, sync_batch_size)
        self._sync_interval_seconds = max(1, sync_interval_seconds)
        self._reindex_interval_seconds = max(0, reindex_interval_seconds)
        self._sync_lock = threading.Lock()
        self._scheduler_registered = False
        self._runtime = TypesenseRuntimeState(
            route="/actions",
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
            1.0,
            self.backfill_actions,
            initial_delay=1.0,
            one_shot=True,
            background=True,
        )
        scheduler.create_task(
            self._sync_interval_seconds,
            self.sync_actions_once,
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

    def backfill_actions(self) -> int:
        """Run the initial backfill until the current collection is caught up."""
        imported = 0
        while True:
            batch_count = self.sync_actions_once()
            if batch_count <= 0:
                return imported
            imported += batch_count

    def sync_actions_once(self) -> int:
        """Poll MongoDB for the next batch of action documents and upsert them."""
        with self._sync_lock:
            collection_name = self.ensure_actions_collection()
            state = self._load_sync_state()
            batch = self._next_actions_batch_after(state)
            if not batch:
                self._runtime.note_sync(
                    imported_count=0,
                    indexed_through=state.last_time,
                    collection_name=collection_name,
                )
                return 0

            documents = [mongo_action_to_typesense_document(action) for action in batch]
            self._client.import_documents(self._alias, documents, action="upsert")

            last_action = batch[-1]
            next_state = ActionsSyncState(
                last_time=float(last_action.get("time") or 0.0),
                last_id=str(last_action.get("_id") or ""),
            )
            self._store_sync_state(next_state)
            self._runtime.note_sync(
                imported_count=len(batch),
                indexed_through=next_state.last_time,
                collection_name=collection_name,
            )
            return len(batch)

    def rebuild_index(self) -> int:
        """Backfill a fresh collection and atomically move the alias to it."""
        with self._sync_lock:
            collection_name = self._fresh_actions_collection_name()
            self._client.create_collection(actions_collection_schema(collection_name))

            imported = 0
            state = ActionsSyncState()
            while True:
                batch = self._next_actions_batch_after(state)
                if not batch:
                    break
                documents = [
                    mongo_action_to_typesense_document(action) for action in batch
                ]
                self._client.import_documents(
                    collection_name, documents, action="upsert"
                )
                imported += len(batch)
                last_action = batch[-1]
                state = ActionsSyncState(
                    last_time=float(last_action.get("time") or 0.0),
                    last_id=str(last_action.get("_id") or ""),
                )

            self._client.upsert_alias(self._alias, collection_name)
            self._kvstore[_ACTIONS_SYNC_COLLECTION_KEY] = collection_name
            self._store_sync_state(state)
            snapshot = self._runtime.note_reindex(
                imported_count=imported,
                indexed_through=state.last_time,
                collection_name=collection_name,
            )
            logger.info(
                "Typesense /actions reindex complete: collection=%s alias_swap_count=%s indexed_lag_seconds=%s imported=%s",
                collection_name,
                snapshot["alias_swap_count"],
                snapshot["indexed_lag_seconds"],
                imported,
            )
            return imported

    def get_actions(  # noqa: PLR0913
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
    ) -> tuple[list[dict[str, Any]], int]:
        """Search the `/actions` alias and return Mongo-shaped action rows."""
        try:
            self.ensure_actions_collection()
            total_requested = max(0, limit)
            if max_count is not None:
                capped_total = max(0, max_count - skip)
                if capped_total <= 0:
                    return [], max_count
                total_requested = min(total_requested, capped_total)

            hits: list[dict[str, Any]] = []
            found = 0
            next_offset = skip
            remaining = total_requested
            while remaining > 0:
                page_limit = min(remaining, _MAX_SEARCH_PAGE_SIZE)
                payload = self._client.search(
                    self._alias,
                    build_actions_search_params(
                        username=username,
                        usernames=usernames,
                        action=action,
                        text=text,
                        limit=page_limit,
                        offset=next_offset,
                        utc_before=utc_before,
                        run_id=run_id,
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
            return [typesense_hit_to_action(hit) for hit in hits], total
        except (TypesenseUnavailableError, TypesenseApiError) as exc:
            snapshot = self._runtime.note_backend_unavailable(exc)
            logger.warning(
                "Typesense /actions backend unavailable: backend_unavailable_count=%s error=%s",
                snapshot["backend_unavailable_count"],
                snapshot["last_error"],
            )
            raise ActionsSearchUnavailableError(str(exc)) from exc

    def shadow_compare(  # noqa: PLR0913
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
    ) -> None:
        """Compare a Mongo-served result with the equivalent Typesense query."""
        try:
            search_rows, search_total = self.get_actions(
                username=username,
                usernames=usernames,
                action=action,
                text=text,
                limit=limit,
                skip=skip,
                utc_before=utc_before,
                run_id=run_id,
                max_count=max_count,
            )
        except ActionsSearchUnavailableError:
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
                "Typesense /actions shadow mismatch: mongo_total=%s search_total=%s count_mismatch_count=%s result_mismatch_count=%s params=%s",
                mongo_total,
                search_total,
                snapshot["count_mismatch_count"],
                snapshot["result_mismatch_count"],
                {
                    "username": username,
                    "usernames": usernames,
                    "action": action,
                    "text": text,
                    "limit": limit,
                    "skip": skip,
                    "utc_before": utc_before,
                    "run_id": run_id,
                    "max_count": max_count,
                    "mongo_ids": mongo_ids,
                    "search_ids": search_ids,
                },
            )

    def record_fallback(self) -> None:
        """Record a request that fell back to MongoDB."""
        snapshot = self._runtime.note_fallback()
        logger.warning(
            "Typesense /actions fallback to MongoDB: fallback_count=%s",
            snapshot["fallback_count"],
        )

    def status_snapshot(self) -> dict[str, Any]:
        """Return operational counters and current sync lag."""
        return self._runtime.snapshot()

    def get_action_facet_counts(
        self,
        *,
        username: str | None = None,
        usernames: list[str] | None = None,
        text: str | None = None,
        utc_before: float | None = None,
        run_id: str | None = None,
    ) -> tuple[dict[str, int], int]:
        """Return raw action facet counts for the current `/actions` query."""
        try:
            self.ensure_actions_collection()
            payload = self._client.search(
                self._alias,
                build_action_facet_params(
                    username=username,
                    usernames=usernames,
                    text=text,
                    utc_before=utc_before,
                    run_id=run_id,
                ),
            )
            return action_facet_counts_from_payload(payload), int(
                payload.get("found") or 0,
            )
        except (TypesenseUnavailableError, TypesenseApiError) as exc:
            snapshot = self._runtime.note_backend_unavailable(exc)
            logger.warning(
                "Typesense /actions backend unavailable: backend_unavailable_count=%s error=%s",
                snapshot["backend_unavailable_count"],
                snapshot["last_error"],
            )
            raise ActionsSearchUnavailableError(str(exc)) from exc

    def ensure_actions_collection(self) -> str:
        """Ensure the current `/actions` alias resolves to a live collection."""
        alias_info = self._client.get_alias(self._alias, allow_missing=True)
        if isinstance(alias_info, dict):
            collection_name = str(alias_info.get("collection_name") or "")
            if collection_name:
                self._kvstore[_ACTIONS_SYNC_COLLECTION_KEY] = collection_name
                self._runtime.note_collection(collection_name)
                return collection_name

        stored_collection = self._kvstore.get(_ACTIONS_SYNC_COLLECTION_KEY, "")
        collection_name = str(
            stored_collection or timestamped_actions_collection_name()
        )
        if self._client.get_collection(collection_name, allow_missing=True) is None:
            self._client.create_collection(actions_collection_schema(collection_name))
        self._client.upsert_alias(self._alias, collection_name)
        self._kvstore[_ACTIONS_SYNC_COLLECTION_KEY] = collection_name
        self._runtime.note_collection(collection_name)
        return collection_name

    def _next_actions_batch_after(
        self, state: ActionsSyncState
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if state.last_time is not None and state.last_id:
            query = {
                "$or": [
                    {"time": {"$gt": state.last_time}},
                    {
                        "time": state.last_time,
                        "_id": {"$gt": ObjectId(state.last_id)},
                    },
                ]
            }
        cursor = self._actiondb.actions.find(query)
        cursor = cursor.sort([("time", ASCENDING), ("_id", ASCENDING)])
        cursor = cursor.limit(self._sync_batch_size)
        return list(cursor)

    def _fresh_actions_collection_name(self) -> str:
        base_time = datetime.now(UTC)
        for offset_seconds in range(120):
            candidate = timestamped_actions_collection_name(
                base_time + timedelta(seconds=offset_seconds),
            )
            if self._client.get_collection(candidate, allow_missing=True) is None:
                return candidate
        return f"{timestamped_actions_collection_name(base_time)}_reindex"

    def _load_sync_state(self) -> ActionsSyncState:
        return ActionsSyncState.from_value(
            self._kvstore.get(_ACTIONS_SYNC_STATE_KEY, {}),
        )

    def _store_sync_state(self, state: ActionsSyncState) -> None:
        self._kvstore[_ACTIONS_SYNC_STATE_KEY] = state.as_value()


def timestamped_actions_collection_name(now: datetime | None = None) -> str:
    """Return a timestamped collection name for alias-based reindexing."""
    current = now or datetime.now(UTC)
    return f"{ACTIONS_SEARCH_CONTRACT.collection_prefix}_{current.strftime('%Y%m%d%H%M%S')}"


def actions_collection_schema(collection_name: str) -> dict[str, Any]:
    """Return the Phase 1 schema for the shadow `/actions` collection."""
    return {
        "name": collection_name,
        "fields": [
            {"name": "time", "type": "float", "sort": True},
            {"name": "action", "type": "string", "facet": True},
            {"name": "username", "type": "string", "facet": True},
            {"name": "worker", "type": "string", "optional": True},
            {"name": "message", "type": "string", "optional": True},
            {"name": "run", "type": "string", "optional": True},
            {"name": "run_id", "type": "string", "facet": True, "optional": True},
            {"name": "user", "type": "string", "optional": True},
            {"name": "nn", "type": "string", "optional": True},
        ],
        "default_sorting_field": ACTIONS_SEARCH_CONTRACT.default_sort_field,
    }


def mongo_action_to_typesense_document(action: dict[str, Any]) -> dict[str, Any]:
    """Map a MongoDB action document to a Typesense document."""
    document = {
        "id": str(action.get("_id") or ""),
        "time": float(action.get("time") or 0.0),
        "action": str(action.get("action") or ""),
        "username": str(action.get("username") or ""),
    }
    for field_name in ("worker", "message", "run", "run_id", "user", "nn", "task_id"):
        value = action.get(field_name)
        if value is not None:
            document[field_name] = value
    return document


def typesense_hit_to_action(hit: dict[str, Any]) -> dict[str, Any]:
    """Map a Typesense search hit back to the Mongo-like action row shape."""
    document = dict(hit.get("document") or {})
    if "_id" not in document:
        document["_id"] = str(document.pop("id", ""))
    return document


def build_actions_search_params(  # noqa: PLR0913
    *,
    username: str | None = None,
    usernames: list[str] | None = None,
    action: str | None = None,
    text: str | None = None,
    limit: int,
    offset: int,
    utc_before: float | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Translate the Phase 1 `/actions` route contract to Typesense search params."""
    query_fields = ",".join(ACTIONS_SEARCH_CONTRACT.query_fields)
    field_count = len(ACTIONS_SEARCH_CONTRACT.query_fields)
    filters = build_actions_filter_by(
        username=username,
        usernames=usernames,
        action=action,
        utc_before=utc_before,
        run_id=run_id,
    )
    params: dict[str, Any] = {
        "q": text or "*",
        "query_by": query_fields,
        "sort_by": "time:desc,_text_match:desc" if text else "time:desc",
        "limit": limit,
        "offset": offset,
        "include_fields": "id,time,action,username,worker,message,run,run_id,user,nn,task_id",
        "prefix": ",".join("false" for _ in range(field_count)),
        "num_typos": ",".join("0" for _ in range(field_count)),
        "drop_tokens_threshold": 0,
        "typo_tokens_threshold": 0,
        "split_join_tokens": "off",
        "exhaustive_search": "true",
    }
    if filters:
        params["filter_by"] = filters
    return params


def build_action_facet_params(
    *,
    username: str | None = None,
    usernames: list[str] | None = None,
    text: str | None = None,
    utc_before: float | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build a raw action facet query for the current `/actions` search."""
    query_fields = ",".join(ACTIONS_SEARCH_CONTRACT.query_fields)
    field_count = len(ACTIONS_SEARCH_CONTRACT.query_fields)
    filters = build_actions_filter_by(
        username=username,
        usernames=usernames,
        utc_before=utc_before,
        run_id=run_id,
        exclude_default_actions=False,
    )
    params: dict[str, Any] = {
        "q": text or "*",
        "query_by": query_fields,
        "facet_by": "action",
        "facet_strategy": "exhaustive",
        "max_facet_values": _ACTION_FACET_MAX_VALUES,
        "per_page": 0,
        "prefix": ",".join("false" for _ in range(field_count)),
        "num_typos": ",".join("0" for _ in range(field_count)),
        "drop_tokens_threshold": 0,
        "typo_tokens_threshold": 0,
        "split_join_tokens": "off",
        "exhaustive_search": "true",
    }
    if filters:
        params["filter_by"] = filters
    return params


def build_actions_filter_by(  # noqa: PLR0913
    *,
    username: str | None = None,
    usernames: list[str] | None = None,
    action: str | None = None,
    utc_before: float | None = None,
    run_id: str | None = None,
    exclude_default_actions: bool = True,
) -> str:
    """Build the exact-match Typesense filter clause for `/actions`."""
    clauses: list[str] = []
    if usernames:
        values = ", ".join(_quote_typesense_value(name) for name in usernames)
        clauses.append(f"username:=[{values}]")
    elif username:
        clauses.append(f"username:={_quote_typesense_value(username)}")

    if action:
        if action == "system_event":
            clauses.append(
                "action:=[`system_event`, `update_stats`]",
            )
        else:
            clauses.append(f"action:={_quote_typesense_value(action)}")
    elif exclude_default_actions:
        clauses.extend(
            f"action:!={_quote_typesense_value(name)}"
            for name in _DEFAULT_EXCLUDED_ACTIONS
        )

    if utc_before is not None:
        clauses.append(f"time:<={float(utc_before)}")
    if run_id:
        clauses.append(f"run_id:={_quote_typesense_value(run_id)}")

    return " && ".join(clauses)


def _quote_typesense_value(value: str) -> str:
    escaped = value.replace("`", "\\`")
    return f"`{escaped}`"


def action_facet_counts_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    """Extract raw per-action facet counts from a Typesense search payload."""
    for facet in payload.get("facet_counts") or []:
        if str(facet.get("field_name") or "") != "action":
            continue
        counts: dict[str, int] = {}
        for item in facet.get("counts") or []:
            value = str(item.get("value") or "")
            if not value:
                continue
            counts[value] = int(item.get("count") or 0)
        return counts
    return {}


__all__ = [
    "ActionsSyncState",
    "TypesenseActionsService",
    "action_facet_counts_from_payload",
    "actions_collection_schema",
    "build_action_facet_params",
    "build_actions_filter_by",
    "build_actions_search_params",
    "mongo_action_to_typesense_document",
    "timestamped_actions_collection_name",
    "typesense_hit_to_action",
]
