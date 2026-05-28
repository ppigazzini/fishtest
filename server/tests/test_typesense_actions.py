"""Test the Phase 1 Typesense `/actions` service."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from bson.objectid import ObjectId

from fishtest.typesense_actions import (
    TypesenseActionsService,
    build_actions_filter_by,
    mongo_action_to_typesense_document,
)


class _TypesenseClientStub:
    def __init__(
        self, *, search_payloads=None, alias_info=None, collection_exists=False
    ):
        self.search_payloads = list(search_payloads or [])
        self.alias_info = alias_info
        self.collection_exists = collection_exists
        self.search_calls = []
        self.created_schema = None
        self.upserted_aliases = []
        self.import_calls = []
        self.closed = False

    def close(self):
        self.closed = True

    def search(self, collection, search_params):
        self.search_calls.append((collection, dict(search_params)))
        return self.search_payloads.pop(0)

    def import_documents(self, collection, documents, *, action="upsert"):
        self.import_calls.append((collection, list(documents), action))
        return [{"success": True} for _ in documents]

    def get_alias(self, alias, *, allow_missing=False):
        return self.alias_info

    def get_collection(self, collection, *, allow_missing=False):
        if self.collection_exists or (
            self.created_schema is not None
            and self.created_schema.get("name") == collection
        ):
            return {"name": collection}
        return None

    def create_collection(self, schema):
        self.created_schema = dict(schema)
        self.collection_exists = True
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
        )

        service.register_scheduler(scheduler)
        service.register_scheduler(scheduler)

        self.assertEqual(len(scheduler.calls), 2)
        self.assertEqual(scheduler.calls[0][0], 1.0)
        self.assertEqual(scheduler.calls[0][2]["one_shot"], True)
        self.assertEqual(scheduler.calls[1][0], 45)
        self.assertEqual(scheduler.calls[1][2]["background"], True)


if __name__ == "__main__":
    unittest.main()
