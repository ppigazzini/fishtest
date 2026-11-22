"""Test the stored-document audit."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC
from unittest import mock

from bson.codec_options import CodecOptions
from bson.objectid import ObjectId
from pymongo import MongoClient
from valgebra import Validator

from utils.validate_documents import Tally, audit, path_pattern

SCHEMA = Validator({"tasks": [{"nps": float}]})


class PathPatternTest(unittest.TestCase):
    def test_a_list_index_is_a_wildcard(self):
        self.assertEqual(
            path_pattern(("tasks", 3, "worker_info", "nps")),
            "tasks[*].worker_info.nps",
        )

    def test_a_key_that_is_not_a_name_is_quoted(self):
        self.assertEqual(path_pattern(("UHO.epd", "total")), "['UHO.epd'].total")

    def test_the_empty_path_is_the_document(self):
        self.assertEqual(path_pattern(()), "(document)")


class TallyTest(unittest.TestCase):
    def test_failures_count_by_group_path_and_code(self):
        # Two tasks failing the same field count under one path, and the key
        # keeps the first document that failed it.
        first, second = ObjectId(), ObjectId()
        tally = Tally()
        tally.check("runs v24", first, {"tasks": [{"nps": 0}, {"nps": 0}]}, SCHEMA)
        tally.check("runs v24", second, {"tasks": [{"nps": 0}]}, SCHEMA)
        tally.check("runs v25", ObjectId(), {"tasks": [{"nps": 0.0}]}, SCHEMA)
        key = ("runs v24", "tasks[*].nps", "float_type")
        self.assertEqual(tally.checked, {"runs v24": 2, "runs v25": 1})
        self.assertEqual(tally.failing, {"runs v24": 2})
        self.assertEqual(tally.failures, {key: 3})
        self.assertEqual(tally.first, {key: first})

    def test_the_report_names_each_failure(self):
        document_id = ObjectId()
        tally = Tally()
        tally.check("runs v24", document_id, {"tasks": [{"nps": 0}]}, SCHEMA)
        tally.check("users", ObjectId(), {"tasks": []}, SCHEMA)
        output = io.StringIO()
        with redirect_stdout(output):
            tally.report()
        report = output.getvalue()
        for text in (
            "runs v24",
            "users",
            "tasks[*].nps",
            "float_type",
            str(document_id),
        ):
            self.assertIn(text, report)

    def test_a_clean_audit_says_so(self):
        tally = Tally()
        tally.check("users", ObjectId(), {"tasks": []}, SCHEMA)
        output = io.StringIO()
        with redirect_stdout(output):
            tally.report()
        self.assertIn("No document fails its schema.", output.getvalue())


class AuditTest(unittest.TestCase):
    def setUp(self):
        client = MongoClient("localhost")
        self.addCleanup(client.close)
        self.addCleanup(client.drop_database, "fishtest_validate_documents_tests")
        self.db = client["fishtest_validate_documents_tests"].with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=UTC)
        )

    def test_the_audit_reads_unfinished_runs_and_the_kvstore_entries(self):
        self.db["runs"].insert_many(
            [{"finished": False, "version": 24}, {"finished": True, "version": 24}]
        )
        self.db["users"].insert_one({"username": "someone"})
        self.db["kvstore"].insert_many(
            [
                {"_id": "legacy_usernames", "value": ["Old User", 7]},
                {"_id": "official_master_sha", "value": "f" * 40},
            ]
        )
        tally = audit(self.db, 0)
        self.assertEqual(
            tally.checked,
            {"runs v24": 1, "users": 1, "kvstore legacy_usernames": 1},
        )
        self.assertEqual(tally.failing, tally.checked)
        self.assertEqual(
            tally.failures[("kvstore legacy_usernames", "[*]", "string_type")], 1
        )

    def test_the_mcp_audit_reads_every_run_as_the_endpoint_publishes_it(self):
        import copy

        from test_schemas import RUN

        run = copy.deepcopy(RUN)
        old = {"_id": ObjectId(), "version": 1, "args": {"sprt": {"elo0": 0.0}}}
        # Fixed-games tests: one that played and one that did not, which get_elo
        # refuses by design, and an SPSA tune, which it refuses too.
        fixed = copy.deepcopy(RUN)
        del fixed["args"]["sprt"]
        fixed |= {"_id": ObjectId(), "version": 2}
        fixed["results"] |= {"wins": 3, "losses": 2, "draws": 5}
        fixed["results"]["pentanomial"] = [0, 1, 2, 1, 1]
        unplayed = copy.deepcopy(fixed) | {"_id": ObjectId(), "version": 3}
        unplayed["results"] = copy.deepcopy(RUN["results"])
        tune = copy.deepcopy(fixed) | {"_id": ObjectId(), "version": 4}
        tune["args"]["spsa"] = {"iter": 0, "num_iter": 10, "params": []}
        self.db["runs"].insert_many([run, old, fixed, unplayed, tune])
        self.assertNotIn("mcp get_run v1", audit(self.db, 0).checked)

        import utils.validate_documents as validate_documents

        progress = io.StringIO()
        with (
            mock.patch.object(validate_documents, "PROGRESS_EVERY", 1),
            redirect_stderr(progress),
        ):
            tally = audit(self.db, 0, mcp=True)
        # A full database takes minutes, so the audit says how far it is.
        self.assertIn("mcp: reading 5 runs", progress.getvalue())
        self.assertIn("mcp: 5 of 5 runs", progress.getvalue())
        version = run.get("version", "?")
        self.assertEqual(tally.checked[f"mcp get_run v{version}"], 1)
        self.assertEqual(tally.failing[f"mcp get_run v{version}"], 0)
        self.assertEqual(tally.failing[f"mcp get_elo v{version}"], 0)
        self.assertEqual(tally.checked["mcp get_elo fixed v2"], 1)
        self.assertEqual(tally.failing["mcp get_elo fixed v2"], 0)
        for version in (3, 4):
            self.assertNotIn(f"mcp get_elo fixed v{version}", tally.checked)
            self.assertNotIn(f"mcp get_elo v{version}", tally.checked)
        # The tune's parameters are read as get_spsa publishes them.
        self.assertEqual(tally.checked["mcp get_spsa v4"], 1)
        self.assertEqual(tally.failing["mcp get_spsa v4"], 0)
        # A run missing what the reader expects is counted, not raised.
        self.assertEqual(tally.failing["mcp get_run v1"], 1)
        self.assertEqual(tally.failing["mcp get_elo v1"], 1)
        raised = [code for group, _, code in tally.failures if group.endswith("v1")]
        self.assertTrue(all(code.startswith("raises KeyError") for code in raised))

    def test_the_mcp_audit_reads_actions_and_workers(self):
        worker_info = {
            "uname": "Linux 6.6",
            "architecture": ["64bit", "ELF"],
            "concurrency": 8,
            "max_memory": 6000,
            "min_threads": 1,
            "username": "someone",
            "version": 332,
            "python_version": [3, 13, 5],
            "gcc_version": [13, 2, 0],
            "compiler": "g++",
            "unique_key": "abcd1234-5a28-4b7d-b27b-d78d97ecf11a",
            "modified": False,
            "worker_arch": "x86-64-avx2",
            "ARCH": "AVX2",
            "nps": 100000.0,
            "near_github_api_limit": False,
            "remote_addr": "1.2.3.4",
            "country_code": "US",
        }
        self.db["runs"].insert_one(
            {
                "_id": ObjectId(),
                "finished": False,
                "version": 24,
                "args": {"username": "someone"},
                "tasks": [
                    {"active": True, "worker_info": worker_info, "last_updated": None},
                    {"active": False, "worker_info": worker_info},
                    # An active task the row cannot be built from.
                    {"active": True},
                ],
            }
        )
        self.db["actions"].insert_many(
            [
                {
                    "action": "log_message",
                    "username": "someone",
                    "time": 1.0,
                    "message": "ok",
                },
                # A kind the server no longer writes.
                {"action": "update_stats", "username": "someone", "time": 2.0},
                # A field no branch declares.
                {
                    "action": "log_message",
                    "username": "someone",
                    "time": 3.0,
                    "message": "ok",
                    "remote_addr": "203.0.113.9",
                },
            ]
        )
        with redirect_stderr(io.StringIO()):
            tally = audit(self.db, 0, mcp=True)
        # Each active task is one worker row: one meets the contract, and the
        # one without worker info is counted, not raised.
        self.assertEqual(tally.checked["mcp list_active_workers"], 2)
        self.assertEqual(tally.failing["mcp list_active_workers"], 1)
        (raised,) = [code for group, _, code in tally.failures if "workers" in group]
        self.assertTrue(raised.startswith("raises KeyError"), raised)
        # Every action is published inside the event record: the projection
        # drops the field no branch declares, and the record admits any kind.
        self.assertEqual(tally.checked["mcp search_actions"], 3)
        self.assertEqual(tally.failing["mcp search_actions"], 0)
        # The stored check names both documents the event would hide.
        self.assertEqual(tally.checked["actions"], 3)
        self.assertEqual(tally.failing["actions"], 2)


if __name__ == "__main__":
    unittest.main()
