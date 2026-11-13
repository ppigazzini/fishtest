"""The MCP tools' contracts."""

import unittest

from valgebra_jsonschema import compile_schema

from fishtest import mcp_contracts
from fishtest.schemas import action_query_schema

# The search_actions filter on its own, as the contract publishes it.
FILTER = compile_schema(
    {"$ref": "#/$defs/filter", "$defs": {"filter": mcp_contracts.FILTER}}
)

CONTRACTS = mcp_contracts.CONTRACTS


class TestMcpContracts(unittest.TestCase):
    def test_each_listed_tool_has_a_contract(self):
        names = [tool["name"] for tool in mcp_contracts.TOOLS]
        self.assertEqual(names, list(CONTRACTS))

    def test_the_filter_contract_admits_every_filter_the_api_accepts(self):
        # A proof: no filter /api/actions serves is refused by the contract.
        self.assertTrue(action_query_schema.is_subtype_of(FILTER))

    @unittest.expectedFailure
    def test_the_filter_contract_admits_only_filters_the_api_accepts(self):
        # valgebra 0.0.25 answers `undecided`: a closed object with a property
        # that is an array under `maxItems` is not related to the matching
        # record, though the array alone is. Until this is proven,
        # search_actions hands the database only what action_query_schema
        # accepts.
        self.assertEqual(FILTER.relation_to(action_query_schema), "subset")

    def test_the_filter_contract_and_the_api_agree_on_filters(self):
        filters = [
            {},
            {"username": "user00"},
            {"action": {"$in": ["new_run", "stop_run"]}, "time": {"$gt": 1.5}},
            {"$or": [{"action": "new_run"}, {"run_id": {"$ne": None}}]},
            {"$and": [{"$nor": [{"task_id": 3}]}]},
            {"$where": "sleep(1000)"},
            {"message": {"$regex": "a+"}},
            {"$and": []},
            {"action": {"$in": list(range(1001))}},
            {"user": {"$eq": [1]}},
            {"nn": {"$gte": {"x": 1}}},
            {"time": True},
        ]
        for query in filters:
            self.assertEqual(query in FILTER, query in action_query_schema, repr(query))

    def test_an_integer_argument_is_a_json_integer(self):
        # JSON Schema counts 1.0 as an integer and true as none.
        accepts = CONTRACTS["list_finished_runs"].accepts
        self.assertIn({"page": 1}, accepts)
        self.assertIn({"page": 1.0}, accepts)
        self.assertNotIn({"page": True}, accepts)
        self.assertNotIn({"page": 0}, accepts)


if __name__ == "__main__":
    unittest.main()
