"""Test the stored-document audit."""

import io
import unittest
from contextlib import redirect_stdout
from datetime import UTC

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


if __name__ == "__main__":
    unittest.main()
