"""Test the conversion of int values unfinished runs store for floats."""

import copy
import io
import unittest
from contextlib import redirect_stdout
from datetime import UTC
from typing import Any

from bson.codec_options import CodecOptions
from bson.objectid import ObjectId
from pymongo import MongoClient
from test_schemas import RUN

from fishtest.schemas import runs_schema
from utils.convert_run_floats import convert, int_float_fields


def run_with_int_zeros(*, finished=False):
    run: dict[str, Any] = copy.deepcopy(RUN)
    run["_id"] = ObjectId()
    run["finished"] = finished
    run["args"]["itp"] = 100
    run["args"]["sprt"]["llr"] = 0
    return run


class IntFloatFieldsTest(unittest.TestCase):
    def test_an_int_where_the_schema_states_a_float_is_listed(self):
        self.assertEqual(
            int_float_fields(run_with_int_zeros()),
            {"args.itp": 100.0, "args.sprt.llr": 0.0},
        )

    def test_a_bool_where_the_schema_states_a_float_is_not(self):
        run = run_with_int_zeros()
        run["args"]["itp"] = True
        self.assertEqual(int_float_fields(run), {"args.sprt.llr": 0.0})

    def test_a_valid_run_has_nothing_to_convert(self):
        run: dict[str, Any] = copy.deepcopy(RUN)
        run["finished"] = False
        self.assertEqual(int_float_fields(run), {})


class ConvertTest(unittest.TestCase):
    def setUp(self):
        client = MongoClient("localhost")
        self.addCleanup(client.close)
        self.addCleanup(client.drop_database, "fishtest_convert_run_floats_tests")
        self.db = client["fishtest_convert_run_floats_tests"].with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=UTC)
        )
        self.unfinished = run_with_int_zeros()
        self.finished = run_with_int_zeros(finished=True)
        self.db["runs"].insert_many([self.unfinished, self.finished])

    def stored_as(self, run, bson_type):
        query = {"_id": run["_id"], "args.itp": {"$type": bson_type}}
        return self.db["runs"].count_documents(query) == 1

    def convert(self, *, apply):
        with redirect_stdout(io.StringIO()):
            return convert(self.db, apply)

    def test_a_dry_run_writes_nothing(self):
        self.assertEqual(self.convert(apply=False), (1, 2))
        self.assertTrue(self.stored_as(self.unfinished, "int"))

    def test_apply_writes_doubles_into_unfinished_runs_only(self):
        self.assertEqual(self.convert(apply=True), (1, 2))
        self.assertTrue(self.stored_as(self.unfinished, "double"))
        self.assertTrue(self.stored_as(self.finished, "int"))
        runs_schema.validate(self.db["runs"].find_one({"_id": self.unfinished["_id"]}))
        self.assertEqual(self.convert(apply=True), (0, 0))


if __name__ == "__main__":
    unittest.main()
