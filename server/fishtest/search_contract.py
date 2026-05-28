"""Freeze the Phase 0 search adapter and Typesense index contract.

Keep the public route contract separate from the future Typesense client so
later phases can implement shadow reads and cutovers without rediscovering the
same alias, field, and parity assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fishtest.http.settings import (
    TYPESENSE_ACTIONS_ALIAS,
    TYPESENSE_FINISHED_RUNS_ALIAS,
)


@dataclass(frozen=True, slots=True)
class SortMapping:
    """Map a public route sort name to the backing document field."""

    public_name: str
    document_field: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CollectionField:
    """Describe one field in the Phase 0 read-model contract."""

    document_field: str
    source_paths: tuple[str, ...]
    indexed: bool = True
    sortable: bool = False
    infix: bool = False
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CollectionContract:
    """Describe the route-facing contract for one search collection."""

    route: str
    alias: str
    collection_prefix: str
    id_field: str
    default_sort_field: str
    default_sort_order: str
    query_fields: tuple[str, ...]
    filter_fields: tuple[str, ...]
    sort_mappings: tuple[SortMapping, ...]
    fields: tuple[CollectionField, ...]


@dataclass(frozen=True, slots=True)
class ParityCase:
    """Record one representative route query that later phases must preserve."""

    name: str
    route: str
    params: tuple[tuple[str, str], ...]
    expectation: str


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Route-normalized search request passed to a backend adapter."""

    route: str
    params: tuple[tuple[str, str], ...]
    limit: int | None
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Backend-agnostic search result payload."""

    rows: list[dict[str, Any]]
    total: int


class SearchBackend(Protocol):
    """Protocol for a future server-side search backend adapter."""

    def search_actions(self, request: SearchRequest) -> SearchResult: ...

    def search_finished_runs(self, request: SearchRequest) -> SearchResult: ...


ACTIONS_SEARCH_CONTRACT = CollectionContract(
    route="/actions",
    alias=TYPESENSE_ACTIONS_ALIAS,
    collection_prefix="actions",
    id_field="_id",
    default_sort_field="time",
    default_sort_order="desc",
    query_fields=(
        "action",
        "username",
        "worker",
        "message",
        "run",
        "user",
        "nn",
    ),
    filter_fields=("action", "username", "run_id"),
    sort_mappings=(
        SortMapping("time", "time"),
        SortMapping("event", "action"),
        SortMapping(
            "source",
            "username",
            notes="The UI source column may render the worker name when present.",
        ),
        SortMapping(
            "target",
            "run",
            notes="The UI target column is derived from run, user, worker, or nn.",
        ),
        SortMapping("comment", "message"),
    ),
    fields=(
        CollectionField("_id", ("_id",)),
        CollectionField("time", ("time",), sortable=True),
        CollectionField("action", ("action",), sortable=True),
        CollectionField(
            "username",
            ("username",),
            infix=True,
            notes="Username fragment ranking stays route-owned in phase 1.",
        ),
        CollectionField("worker", ("worker",), infix=True),
        CollectionField("message", ("message",), infix=True),
        CollectionField("run", ("run",), infix=True),
        CollectionField("run_id", ("run_id",)),
        CollectionField("user", ("user",), infix=True),
        CollectionField("nn", ("nn",), infix=True),
        CollectionField(
            "task_id",
            ("task_id",),
            indexed=False,
            notes="Task ids are display detail, not a Phase 1 query surface.",
        ),
    ),
)


FINISHED_RUNS_SEARCH_CONTRACT = CollectionContract(
    route="/tests/finished",
    alias=TYPESENSE_FINISHED_RUNS_ALIAS,
    collection_prefix="finished_runs",
    id_field="_id",
    default_sort_field="last_updated",
    default_sort_order="desc",
    query_fields=("args.info",),
    filter_fields=(
        "args.username",
        "finished",
        "deleted",
        "is_green",
        "is_yellow",
        "tc_base",
    ),
    sort_mappings=(SortMapping("time", "last_updated"),),
    fields=(
        CollectionField("_id", ("_id",)),
        CollectionField("last_updated", ("last_updated",), sortable=True),
        CollectionField(
            "args.username",
            ("args.username",),
            infix=True,
            notes="Username fragment ranking stays route-owned in phase 1.",
        ),
        CollectionField("args.info", ("args.info",), infix=True),
        CollectionField("finished", ("finished",)),
        CollectionField("deleted", ("deleted",)),
        CollectionField("is_green", ("is_green",)),
        CollectionField("is_yellow", ("is_yellow",)),
        CollectionField(
            "tc_base",
            ("tc_base",),
            sortable=True,
            notes="Supports the existing LTC filter boundary.",
        ),
    ),
)


ACTIONS_PARITY_CASES = (
    ParityCase(
        name="actions_text_phrase_search",
        route="/actions",
        params=(("text", '"branch search"'),),
        expectation="Preserve phrase semantics and route-owned result caps.",
    ),
    ParityCase(
        name="actions_ranked_username_substring",
        route="/actions",
        params=(("user", "mockuser"),),
        expectation="Prefix username matches stay ahead of inner-substring matches.",
    ),
    ParityCase(
        name="actions_alt_sort_scope",
        route="/actions",
        params=(("user", "vin"), ("sort", "event"), ("order", "asc")),
        expectation="Alternate sorts apply to the capped working set, not the full history.",
    ),
    ParityCase(
        name="actions_run_scoped_filter",
        route="/actions",
        params=(("run_id", "64e74776a170cb1f26fa3930"), ("action", "new_run")),
        expectation="Run scoping survives combined filter queries.",
    ),
)


FINISHED_RUNS_PARITY_CASES = (
    ParityCase(
        name="finished_search_text_only",
        route="/tests/finished",
        params=(("mode", "search"), ("text", '"branch search"')),
        expectation="Text-only search keeps the search-mode caps and canonical URL.",
    ),
    ParityCase(
        name="finished_ranked_username_substring",
        route="/tests/finished",
        params=(("mode", "search"), ("user", "vin")),
        expectation="Prefix username matches stay ahead of inner-substring matches.",
    ),
    ParityCase(
        name="finished_navigation_redirect_to_search",
        route="/tests/finished",
        params=(("user", "Auth"), ("text", "branch")),
        expectation="Navigation mode redirects filtered requests to canonical search mode.",
    ),
    ParityCase(
        name="finished_search_drops_status_tabs",
        route="/tests/finished",
        params=(("mode", "search"), ("success_only", "1"), ("user", "Auth")),
        expectation="Search mode strips status-tab filters from the canonical URL.",
    ),
)


__all__ = [
    "ACTIONS_PARITY_CASES",
    "ACTIONS_SEARCH_CONTRACT",
    "CollectionContract",
    "CollectionField",
    "FINISHED_RUNS_PARITY_CASES",
    "FINISHED_RUNS_SEARCH_CONTRACT",
    "ParityCase",
    "SearchBackend",
    "SearchRequest",
    "SearchResult",
    "SortMapping",
]
