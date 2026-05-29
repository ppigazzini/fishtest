"""Test the Phase 1 Typesense `/actions` service."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from bson.objectid import ObjectId

from fishtest.search_actions import ActionsSearchUnavailableError
from fishtest.typesense_actions import (
    TypesenseActionsService,
    action_facet_counts_from_payload,
    build_action_facet_params,
    build_actions_filter_by,
    build_actions_search_params,
    mongo_action_to_typesense_document,
)
from fishtest.typesense_client import TypesenseUnavailableError


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
        self._docs.sort(key=lambda doc: (float(doc.get("time") or 0.0), doc["_id"]))
        return self

    def limit(self, limit):
        self._docs = self._docs[:limit]
        return self

    def __iter__(self):
        return iter(self._docs)


class _ActionCollectionStub:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query):
        docs = list(self._docs)
        if query:
            time_clause = query["$or"][0]["time"]["$gt"]
            object_id_clause = query["$or"][1]["_id"]["$gt"]
            docs = [
                doc
                for doc in docs
                if float(doc.get("time") or 0.0) > time_clause
                or (
                    float(doc.get("time") or 0.0) == time_clause
                    and doc["_id"] > object_id_clause
                )
            ]
        return _CursorStub(docs)


class _SchedulerStub:
    def __init__(self):
        self.calls = []

    def create_task(self, period, worker, **kwargs):
        self.calls.append((period, worker, kwargs))
        return object()


class TypesenseActionsServiceTests(unittest.TestCase):
    def test_build_actions_filter_by_preserves_default_exclusions(self):
        filter_by = build_actions_filter_by(
            usernames=["alice", "bob"],
            utc_before=42.0,
            run_id="64e74776a170cb1f26fa3930",
        )

        self.assertIn("username:=[`alice`, `bob`]", filter_by)
        self.assertIn("action:!=`system_event`", filter_by)
        self.assertIn("action:!=`update_stats`", filter_by)
        self.assertIn("action:!=`dead_task`", filter_by)
        self.assertIn("time:<=42.0", filter_by)
        self.assertIn("run_id:=`64e74776a170cb1f26fa3930`", filter_by)

    def test_get_actions_uses_alias_and_returns_mongo_shaped_rows(self):
        client = _TypesenseClientStub(
            search_payloads=[
                {
                    "found": 1,
                    "hits": [
                        {
                            "document": {
                                "id": "search-1",
                                "time": 123.0,
                                "action": "new_run",
                                "username": "typesense-user",
                                "run_id": "64e74776a170cb1f26fa3930",
                                "run": "typesense-run",
                            }
                        }
                    ],
                }
            ],
            alias_info={
                "name": "actions_current",
                "collection_name": "actions_20260528",
            },
            collection_exists=True,
        )
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub([])),
            kvstore={},
            alias="actions_current",
            enabled=True,
            shadow_reads_enabled=False,
            fallback_to_mongo=True,
            sync_batch_size=250,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        rows, total = service.get_actions(text='"branch search"', limit=25, skip=0)

        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["_id"], "search-1")
        self.assertEqual(rows[0]["username"], "typesense-user")
        self.assertEqual(client.search_calls[0][0], "actions_current")
        self.assertEqual(client.search_calls[0][1]["q"], '"branch search"')
        self.assertEqual(
            client.search_calls[0][1]["sort_by"], "time:desc,_text_match:desc"
        )
        self.assertNotIn("exhaustive_search", client.search_calls[0][1])

    def test_build_actions_search_params_does_not_force_exhaustive_search(self):
        params = build_actions_search_params(text="branch", limit=25, offset=0)

        self.assertEqual(params["q"], "branch")
        self.assertNotIn("exhaustive_search", params)

    def test_build_action_facet_params_uses_raw_action_facets(self):
        params = build_action_facet_params(
            usernames=["alice", "bob"],
            text='"branch search"',
            utc_before=42.0,
            run_id="64e74776a170cb1f26fa3930",
        )

        self.assertEqual(params["facet_by"], "action")
        self.assertEqual(params["facet_strategy"], "exhaustive")
        self.assertEqual(params["per_page"], 0)
        self.assertEqual(params["q"], '"branch search"')
        self.assertNotIn("exhaustive_search", params)
        self.assertIn("username:=[`alice`, `bob`]", params["filter_by"])
        self.assertIn("time:<=42.0", params["filter_by"])
        self.assertIn("run_id:=`64e74776a170cb1f26fa3930`", params["filter_by"])
        self.assertNotIn("action:!=`system_event`", params["filter_by"])

    def test_action_facet_counts_from_payload_reads_action_counts(self):
        counts = action_facet_counts_from_payload(
            {
                "facet_counts": [
                    {
                        "field_name": "action",
                        "counts": [
                            {"value": "new_run", "count": 5},
                            {"value": "update_stats", "count": 2},
                        ],
                    }
                ]
            }
        )

        self.assertEqual(counts, {"new_run": 5, "update_stats": 2})

    def test_get_action_facet_counts_uses_alias_and_returns_total(self):
        client = _TypesenseClientStub(
            search_payloads=[
                {
                    "found": 20,
                    "facet_counts": [
                        {
                            "field_name": "action",
                            "counts": [
                                {"value": "new_run", "count": 5},
                                {"value": "system_event", "count": 2},
                                {"value": "update_stats", "count": 3},
                                {"value": "dead_task", "count": 4},
                            ],
                        }
                    ],
                    "hits": [],
                }
            ],
            alias_info={
                "name": "actions_current",
                "collection_name": "actions_20260528",
            },
            collection_exists=True,
        )
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub([])),
            kvstore={},
            alias="actions_current",
            enabled=True,
            shadow_reads_enabled=False,
            fallback_to_mongo=True,
            sync_batch_size=250,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        counts, total = service.get_action_facet_counts(
            usernames=["alice", "bob"],
            text='"branch search"',
            utc_before=42.0,
            run_id="64e74776a170cb1f26fa3930",
        )

        self.assertEqual(total, 20)
        self.assertEqual(counts["new_run"], 5)
        self.assertEqual(counts["update_stats"], 3)
        self.assertEqual(client.search_calls[0][0], "actions_current")
        self.assertEqual(client.search_calls[0][1]["facet_by"], "action")

    def test_sync_actions_once_imports_batch_and_persists_watermark(self):
        docs = [
            {
                "_id": ObjectId("64e74776a170cb1f26fa3930"),
                "time": 10.0,
                "action": "new_run",
                "username": "alice",
            },
            {
                "_id": ObjectId("64e74776a170cb1f26fa3931"),
                "time": 11.0,
                "action": "upload_nn",
                "username": "bob",
                "nn": "nn-bob.nnue",
            },
        ]
        kvstore = {}
        client = _TypesenseClientStub()
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub(docs)),
            kvstore=kvstore,
            alias="actions_current",
            enabled=False,
            shadow_reads_enabled=True,
            fallback_to_mongo=True,
            sync_batch_size=2,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        imported = service.sync_actions_once()

        self.assertEqual(imported, 2)
        self.assertEqual(client.import_calls[0][0], "actions_current")
        self.assertEqual(client.import_calls[0][2], "upsert")
        self.assertEqual(client.import_calls[0][1][0]["id"], str(docs[0]["_id"]))
        self.assertEqual(
            kvstore["typesense.actions.sync_state"]["last_id"],
            str(docs[-1]["_id"]),
        )
        self.assertEqual(kvstore["typesense.actions.sync_state"]["last_time"], 11.0)
        self.assertTrue(client.upserted_aliases)

    def test_mongo_action_to_typesense_document_keeps_optional_fields(self):
        action = {
            "_id": ObjectId("64e74776a170cb1f26fa3930"),
            "time": 10.0,
            "action": "block_worker",
            "username": "approver",
            "worker": "worker-1",
            "task_id": 4,
        }

        document = mongo_action_to_typesense_document(action)

        self.assertEqual(document["id"], str(action["_id"]))
        self.assertEqual(document["worker"], "worker-1")
        self.assertEqual(document["task_id"], 4)

    def test_register_scheduler_adds_backfill_and_polling_tasks_once(self):
        client = _TypesenseClientStub()
        scheduler = _SchedulerStub()
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub([])),
            kvstore={},
            alias="actions_current",
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
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub([])),
            kvstore={},
            alias="actions_current",
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

    def test_get_actions_tracks_backend_unavailable_count(self):
        client = _TypesenseClientStub(
            search_exception=TypesenseUnavailableError("typesense down"),
        )
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub([])),
            kvstore={},
            alias="actions_current",
            enabled=True,
            shadow_reads_enabled=False,
            fallback_to_mongo=True,
            sync_batch_size=250,
            sync_interval_seconds=30,
            reindex_interval_seconds=0,
        )

        with self.assertRaises(ActionsSearchUnavailableError):
            service.get_actions(text="branch", limit=1)

        snapshot = service.status_snapshot()
        self.assertEqual(snapshot["backend_unavailable_count"], 1)
        self.assertIn("typesense down", snapshot["last_error"])

    def test_status_snapshot_tracks_fallbacks_and_shadow_mismatches(self):
        client = _TypesenseClientStub(
            search_payloads=[
                {
                    "found": 1,
                    "hits": [
                        {
                            "document": {
                                "id": "search-2",
                                "time": 123.0,
                                "action": "new_run",
                                "username": "typesense-user",
                            }
                        }
                    ],
                }
            ],
            alias_info={
                "name": "actions_current",
                "collection_name": "actions_20260528",
            },
            collection_exists=True,
        )
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub([])),
            kvstore={},
            alias="actions_current",
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
                    {"_id": "mongo-1", "time": 120.0},
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
        old_collection = "actions_20260528000000"
        docs = [
            {
                "_id": ObjectId("64e74776a170cb1f26fa3930"),
                "time": 10.0,
                "action": "new_run",
                "username": "alice",
            },
            {
                "_id": ObjectId("64e74776a170cb1f26fa3931"),
                "time": 11.0,
                "action": "upload_nn",
                "username": "bob",
            },
        ]
        kvstore = {"typesense.actions.collection_name": old_collection}
        client = _TypesenseClientStub(
            alias_info={"name": "actions_current", "collection_name": old_collection},
            existing_collections={old_collection},
        )
        service = TypesenseActionsService(
            client=client,
            actiondb=SimpleNamespace(actions=_ActionCollectionStub(docs)),
            kvstore=kvstore,
            alias="actions_current",
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
        self.assertEqual(kvstore["typesense.actions.collection_name"], new_collection)
        self.assertEqual(snapshot["alias_swap_count"], 1)
        self.assertEqual(snapshot["last_reindex_document_count"], 2)


if __name__ == "__main__":
    unittest.main()
