"""Test the Phase 2 Typesense `/tests/finished` service."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from bson.objectid import ObjectId

from fishtest.search_finished import FinishedRunsSearchUnavailableError
from fishtest.typesense_client import TypesenseUnavailableError
from fishtest.typesense_finished import (
    TypesenseFinishedRunsService,
    build_finished_runs_count_params,
    build_finished_runs_filter_by,
    build_finished_runs_tab_counts_params,
    finished_runs_tab_counts_from_payloads,
    mongo_finished_run_to_typesense_document,
)


class _TypesenseClientStub:
    def __init__(
        self,
        *,
        search_payloads=None,
        alias_info=None,
        collection_exists=False,
        existing_collections=None,
        search_exception=None,
    ):
        self.search_payloads = list(search_payloads or [])
        self.alias_info = alias_info
        self.search_exception = search_exception
        self.existing_collections = set(existing_collections or [])
        if collection_exists and isinstance(alias_info, dict):
            collection_name = str(alias_info.get("collection_name") or "")
            if collection_name:
                self.existing_collections.add(collection_name)
        self.search_calls = []
        self.created_schema = None
        self.created_schemas = []
        self.upserted_aliases = []
        self.import_calls = []
        self.closed = False

    def close(self):
        self.closed = True

    def search(self, collection, search_params):
        if self.search_exception is not None:
            raise self.search_exception
        self.search_calls.append((collection, dict(search_params)))
        return self.search_payloads.pop(0)

    def import_documents(self, collection, documents, *, action="upsert"):
        self.import_calls.append((collection, list(documents), action))
        return [{"success": True} for _ in documents]

    def get_alias(self, alias, *, allow_missing=False):
        return self.alias_info

    def get_collection(self, collection, *, allow_missing=False):
        if collection in self.existing_collections:
            return {"name": collection}
        return None

    def create_collection(self, schema):
        self.created_schema = dict(schema)
        self.created_schemas.append(dict(schema))
        self.existing_collections.add(schema["name"])
        return {"name": schema["name"]}

    def upsert_alias(self, alias, collection_name):
        self.alias_info = {"name": alias, "collection_name": collection_name}
        self.upserted_aliases.append((alias, collection_name))
        return dict(self.alias_info)


class _CursorStub:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, _spec):
        self._docs.sort(
            key=lambda doc: (
                _sort_last_updated_value(doc.get("last_updated")),
                doc["_id"],
            ),
        )
        return self

    def limit(self, limit):
        self._docs = self._docs[:limit]
        return self

    def __iter__(self):
        return iter(self._docs)


class _RunsCollectionStub:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query, projection=None):
        docs = list(self._docs)
        if "$or" in query:
            last_updated_clause = _sort_last_updated_value(
                query["$or"][0]["last_updated"]["$gt"]
            )
            object_id_clause = query["$or"][1]["_id"]["$gt"]
            equal_last_updated_clause = _sort_last_updated_value(
                query["$or"][1]["last_updated"]
            )
            docs = [
                doc
                for doc in docs
                if _sort_last_updated_value(doc.get("last_updated"))
                > last_updated_clause
                or (
                    _sort_last_updated_value(doc.get("last_updated"))
                    == equal_last_updated_clause
                    and doc["_id"] > object_id_clause
                )
            ]
        elif "_id" in query and "$in" in query["_id"]:
            requested = {str(value) for value in query["_id"]["$in"]}
            docs = [doc for doc in docs if str(doc.get("_id") or "") in requested]
        if projection is not None:
            projected_docs = []
            excluded_keys = {key for key, include in projection.items() if include == 0}
            for doc in docs:
                projected_doc = {
                    key: value for key, value in doc.items() if key not in excluded_keys
                }
                projected_docs.append(projected_doc)
            docs = projected_docs
        return _CursorStub(docs)


class _SchedulerStub:
    def __init__(self):
        self.calls = []

    def create_task(self, period, worker, **kwargs):
        self.calls.append((period, worker, kwargs))
        return object()


def _sort_last_updated_value(value):
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, int | float):
        return float(value)
    return 0.0


class TypesenseFinishedRunsServiceTests(unittest.TestCase):
    def test_build_finished_runs_filter_by_preserves_route_filters(self):
        filter_by = build_finished_runs_filter_by(
            usernames=["alice", "bob"],
            success_only=True,
            ltc_only=True,
            ltc_lower_bound=20.0,
        )

        self.assertIn("finished:=true", filter_by)
        self.assertIn("deleted:=false", filter_by)
        self.assertIn("args.username:=[`alice`, `bob`]", filter_by)
        self.assertIn("is_green:=true", filter_by)
        self.assertIn("tc_base:>=20.0", filter_by)

    def test_get_finished_runs_uses_alias_and_hydrates_mongo_rows(self):
        run_id = ObjectId("64e74776a170cb1f26fa3930")
        client = _TypesenseClientStub(
            search_payloads=[
                {
                    "found": 1,
                    "hits": [
                        {
                            "document": {
                                "id": str(run_id),
                            }
                        }
                    ],
                }
            ],
            alias_info={
                "name": "finished_runs_current",
                "collection_name": "finished_runs_20260528",
            },
            collection_exists=True,
        )
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(
                runs=_RunsCollectionStub(
                    [
                        {
                            "_id": run_id,
                            "args": {
                                "username": "typesense-user",
                                "info": "branch search",
                            },
                            "last_updated": datetime.now(UTC),
                            "finished": True,
                            "deleted": False,
                        }
                    ]
                ),
                ltc_lower_bound=20.0,
            ),
            kvstore={},
            alias="finished_runs_current",
            enabled=True,
            shadow_reads_enabled=False,
            fallback_to_mongo=True,
            sync_batch_size=250,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        rows, total = service.get_finished_runs(
            text='"branch search"',
            limit=25,
            skip=0,
        )

        self.assertEqual(total, 1)
        self.assertEqual(str(rows[0]["_id"]), str(run_id))
        self.assertEqual(rows[0]["args"]["username"], "typesense-user")
        self.assertEqual(client.search_calls[0][0], "finished_runs_current")
        self.assertEqual(client.search_calls[0][1]["q"], '"branch search"')
        self.assertEqual(
            client.search_calls[0][1]["sort_by"],
            "last_updated:desc,_text_match:desc",
        )

    def test_build_finished_runs_count_params_keeps_count_only_contract(self):
        params = build_finished_runs_count_params(
            success_only=True,
            ltc_lower_bound=20.0,
        )

        self.assertEqual(params["q"], "*")
        self.assertEqual(params["query_by"], "args.info")
        self.assertEqual(params["per_page"], 0)
        self.assertIn("is_green:=true", params["filter_by"])

    def test_build_finished_runs_tab_counts_params_uses_status_facets(self):
        params = build_finished_runs_tab_counts_params()

        self.assertEqual(params["facet_by"], "is_green,is_yellow")
        self.assertEqual(params["facet_strategy"], "exhaustive")
        self.assertEqual(params["per_page"], 0)
        self.assertIn("finished:=true", params["filter_by"])
        self.assertIn("deleted:=false", params["filter_by"])

    def test_finished_runs_tab_counts_from_payloads_extracts_counts(self):
        counts = finished_runs_tab_counts_from_payloads(
            base_payload={
                "found": 12,
                "facet_counts": [
                    {
                        "field_name": "is_green",
                        "counts": [{"value": "true", "count": 5}],
                    },
                    {
                        "field_name": "is_yellow",
                        "counts": [{"value": "true", "count": 3}],
                    },
                ],
            },
            ltc_payload={"found": 4},
        )

        self.assertEqual(counts, {"all": 12, "green": 5, "yellow": 3, "ltc": 4})

    def test_get_finished_runs_tab_counts_uses_alias(self):
        client = _TypesenseClientStub(
            search_payloads=[
                {
                    "found": 12,
                    "facet_counts": [
                        {
                            "field_name": "is_green",
                            "counts": [{"value": "true", "count": 5}],
                        },
                        {
                            "field_name": "is_yellow",
                            "counts": [{"value": "true", "count": 3}],
                        },
                    ],
                    "hits": [],
                },
                {"found": 4, "facet_counts": [], "hits": []},
            ],
            alias_info={
                "name": "finished_runs_current",
                "collection_name": "finished_runs_20260528",
            },
            collection_exists=True,
        )
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(runs=_RunsCollectionStub([]), ltc_lower_bound=20.0),
            kvstore={},
            alias="finished_runs_current",
            enabled=True,
            shadow_reads_enabled=False,
            fallback_to_mongo=True,
            sync_batch_size=250,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        counts = service.get_finished_runs_tab_counts()

        self.assertEqual(counts, {"all": 12, "green": 5, "yellow": 3, "ltc": 4})
        self.assertEqual(client.search_calls[0][0], "finished_runs_current")
        self.assertEqual(client.search_calls[0][1]["facet_by"], "is_green,is_yellow")
        self.assertIn("tc_base:>=20.0", client.search_calls[1][1]["filter_by"])

    def test_sync_finished_runs_once_imports_batch_and_persists_watermark(self):
        now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
        docs = [
            {
                "_id": ObjectId("64e74776a170cb1f26fa3930"),
                "args": {"username": "alice", "info": "branch search"},
                "finished": True,
                "deleted": False,
                "last_updated": now,
                "tc_base": 30.0,
            },
            {
                "_id": ObjectId("64e74776a170cb1f26fa3931"),
                "args": {"username": "bob", "info": "ltc regression"},
                "finished": True,
                "deleted": False,
                "last_updated": now.replace(second=1),
                "is_green": True,
            },
        ]
        kvstore = {}
        client = _TypesenseClientStub()
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(
                runs=_RunsCollectionStub(docs),
                ltc_lower_bound=20.0,
            ),
            kvstore=kvstore,
            alias="finished_runs_current",
            enabled=False,
            shadow_reads_enabled=True,
            fallback_to_mongo=True,
            sync_batch_size=2,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        imported = service.sync_finished_runs_once()

        self.assertEqual(imported, 2)
        self.assertEqual(client.import_calls[0][0], "finished_runs_current")
        self.assertEqual(client.import_calls[0][2], "upsert")
        self.assertEqual(client.import_calls[0][1][0]["id"], str(docs[0]["_id"]))
        self.assertEqual(
            kvstore["typesense.finished_runs.sync_state"]["last_id"],
            str(docs[-1]["_id"]),
        )
        self.assertEqual(
            kvstore["typesense.finished_runs.sync_state"]["last_updated"],
            docs[-1]["last_updated"],
        )
        self.assertTrue(client.upserted_aliases)

    def test_sync_finished_runs_once_resumes_from_legacy_float_watermark(self):
        now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
        docs = [
            {
                "_id": ObjectId("64e74776a170cb1f26fa3930"),
                "args": {"username": "alice", "info": "first"},
                "finished": True,
                "deleted": False,
                "last_updated": now,
            },
            {
                "_id": ObjectId("64e74776a170cb1f26fa3931"),
                "args": {"username": "bob", "info": "second"},
                "finished": True,
                "deleted": False,
                "last_updated": now.replace(second=1),
            },
            {
                "_id": ObjectId("64e74776a170cb1f26fa3932"),
                "args": {"username": "carol", "info": "third"},
                "finished": True,
                "deleted": False,
                "last_updated": now.replace(second=2),
            },
        ]
        kvstore = {
            "typesense.finished_runs.sync_state": {
                "last_updated": docs[1]["last_updated"].timestamp(),
                "last_id": str(docs[1]["_id"]),
            }
        }
        client = _TypesenseClientStub(
            alias_info={
                "name": "finished_runs_current",
                "collection_name": "finished_runs_20260528",
            },
            collection_exists=True,
        )
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(
                runs=_RunsCollectionStub(docs),
                ltc_lower_bound=20.0,
            ),
            kvstore=kvstore,
            alias="finished_runs_current",
            enabled=False,
            shadow_reads_enabled=True,
            fallback_to_mongo=True,
            sync_batch_size=2,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        imported = service.sync_finished_runs_once()

        self.assertEqual(imported, 1)
        self.assertEqual(len(client.import_calls), 1)
        self.assertEqual(client.import_calls[0][1][0]["id"], str(docs[-1]["_id"]))
        self.assertEqual(
            kvstore["typesense.finished_runs.sync_state"]["last_updated"],
            docs[-1]["last_updated"],
        )

    def test_mongo_finished_run_to_typesense_document_keeps_nested_args(self):
        run = {
            "_id": ObjectId("64e74776a170cb1f26fa3930"),
            "args": {
                "username": "approver",
                "info": "LTC branch search",
            },
            "finished": True,
            "deleted": False,
            "last_updated": datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
            "tc_base": 60.0,
        }

        document = mongo_finished_run_to_typesense_document(run)

        self.assertEqual(document["id"], str(run["_id"]))
        self.assertEqual(document["args"]["username"], "approver")
        self.assertEqual(document["args"]["info"], "LTC branch search")
        self.assertEqual(document["tc_base"], 60.0)

    def test_register_scheduler_adds_backfill_and_polling_tasks_once(self):
        client = _TypesenseClientStub()
        scheduler = _SchedulerStub()
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(runs=_RunsCollectionStub([]), ltc_lower_bound=20.0),
            kvstore={},
            alias="finished_runs_current",
            enabled=False,
            shadow_reads_enabled=True,
            fallback_to_mongo=True,
            sync_batch_size=2,
            sync_interval_seconds=45,
            reindex_interval_seconds=0,
        )

        service.register_scheduler(scheduler)
        service.register_scheduler(scheduler)

        self.assertEqual(len(scheduler.calls), 2)
        self.assertEqual(scheduler.calls[0][0], 1.0)
        self.assertEqual(scheduler.calls[0][2]["one_shot"], True)
        self.assertEqual(scheduler.calls[1][0], 45)
        self.assertEqual(scheduler.calls[1][2]["background"], True)

    def test_register_scheduler_adds_optional_reindex_task(self):
        client = _TypesenseClientStub()
        scheduler = _SchedulerStub()
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(runs=_RunsCollectionStub([]), ltc_lower_bound=20.0),
            kvstore={},
            alias="finished_runs_current",
            enabled=False,
            shadow_reads_enabled=True,
            fallback_to_mongo=True,
            sync_batch_size=2,
            sync_interval_seconds=45,
            reindex_interval_seconds=3600,
        )

        service.register_scheduler(scheduler)

        self.assertEqual(len(scheduler.calls), 3)
        self.assertEqual(scheduler.calls[2][0], 3600)
        self.assertEqual(scheduler.calls[2][1], service.rebuild_index)

    def test_get_finished_runs_tracks_backend_unavailable_count(self):
        client = _TypesenseClientStub(
            search_exception=TypesenseUnavailableError("typesense down"),
        )
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(runs=_RunsCollectionStub([]), ltc_lower_bound=20.0),
            kvstore={},
            alias="finished_runs_current",
            enabled=True,
            shadow_reads_enabled=False,
            fallback_to_mongo=True,
            sync_batch_size=250,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        with self.assertRaises(FinishedRunsSearchUnavailableError):
            service.get_finished_runs(text="branch", limit=1)

        snapshot = service.status_snapshot()
        self.assertEqual(snapshot["backend_unavailable_count"], 1)
        self.assertIn("typesense down", snapshot["last_error"])

    def test_status_snapshot_tracks_fallbacks_and_shadow_mismatches(self):
        search_run_id = ObjectId("64e74776a170cb1f26fa3930")
        client = _TypesenseClientStub(
            search_payloads=[
                {
                    "found": 1,
                    "hits": [{"document": {"id": str(search_run_id)}}],
                }
            ],
            alias_info={
                "name": "finished_runs_current",
                "collection_name": "finished_runs_20260528",
            },
            collection_exists=True,
        )
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(
                runs=_RunsCollectionStub(
                    [
                        {
                            "_id": search_run_id,
                            "args": {
                                "username": "typesense-user",
                                "info": "branch search",
                            },
                            "last_updated": datetime.now(UTC),
                            "finished": True,
                            "deleted": False,
                        }
                    ]
                ),
                ltc_lower_bound=20.0,
            ),
            kvstore={},
            alias="finished_runs_current",
            enabled=False,
            shadow_reads_enabled=True,
            fallback_to_mongo=True,
            sync_batch_size=250,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        service.record_fallback()
        service.shadow_compare(
            mongo_result=(
                [
                    {
                        "_id": ObjectId("64e74776a170cb1f26fa3931"),
                        "args": {"username": "mongo-user", "info": "branch search"},
                        "last_updated": datetime.now(UTC),
                        "finished": True,
                        "deleted": False,
                    }
                ],
                2,
            ),
            text="branch",
            limit=1,
        )

        snapshot = service.status_snapshot()
        self.assertEqual(snapshot["fallback_count"], 1)
        self.assertEqual(snapshot["count_mismatch_count"], 1)
        self.assertEqual(snapshot["result_mismatch_count"], 1)

    def test_rebuild_index_creates_new_collection_and_swaps_alias(self):
        old_collection = "finished_runs_20260528000000"
        now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
        docs = [
            {
                "_id": ObjectId("64e74776a170cb1f26fa3930"),
                "args": {"username": "alice", "info": "branch search"},
                "finished": True,
                "deleted": False,
                "last_updated": now,
                "tc_base": 30.0,
            },
            {
                "_id": ObjectId("64e74776a170cb1f26fa3931"),
                "args": {"username": "bob", "info": "ltc regression"},
                "finished": True,
                "deleted": False,
                "last_updated": now.replace(second=1),
                "tc_base": 60.0,
            },
        ]
        kvstore = {"typesense.finished_runs.collection_name": old_collection}
        client = _TypesenseClientStub(
            alias_info={
                "name": "finished_runs_current",
                "collection_name": old_collection,
            },
            existing_collections={old_collection},
        )
        service = TypesenseFinishedRunsService(
            client=client,
            rundb=SimpleNamespace(
                runs=_RunsCollectionStub(docs),
                ltc_lower_bound=20.0,
            ),
            kvstore=kvstore,
            alias="finished_runs_current",
            enabled=False,
            shadow_reads_enabled=True,
            fallback_to_mongo=True,
            sync_batch_size=2,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        imported = service.rebuild_index()

        new_collection = client.upserted_aliases[-1][1]
        snapshot = service.status_snapshot()
        self.assertEqual(imported, 2)
        self.assertNotEqual(new_collection, old_collection)
        self.assertEqual(client.import_calls[0][0], new_collection)
        self.assertEqual(
            kvstore["typesense.finished_runs.collection_name"],
            new_collection,
        )
        self.assertEqual(snapshot["alias_swap_count"], 1)
        self.assertEqual(snapshot["last_reindex_document_count"], 2)


if __name__ == "__main__":
    unittest.main()
