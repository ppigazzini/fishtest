"""The MCP tools: their contracts, and the server driven through an MCP client."""

import unittest
from datetime import UTC, datetime

import test_support
from bson.objectid import ObjectId
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS
from test_schemas import RUN
from valgebra_jsonschema import compile_schema

from fishtest import mcp_contracts, mcp_tools
from fishtest.mcp_server import build_server
from fishtest.run_cache import Prio
from fishtest.schemas import action_query_schema

# The search_actions filter on its own, as the contract publishes it.
FILTER = compile_schema(
    {"$ref": "#/$defs/filter", "$defs": {"filter": mcp_contracts.FILTER}}
)

CONTRACTS = mcp_contracts.CONTRACTS


class TestMcpContracts(unittest.TestCase):
    def test_each_listed_tool_has_a_contract_and_a_function(self):
        names = [tool["name"] for tool in mcp_contracts.TOOLS]
        self.assertEqual(names, list(CONTRACTS))
        self.assertEqual(set(names), set(mcp_tools.TOOL_FUNCTIONS))

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

    def test_a_run_summary_is_what_get_run_publishes(self):
        summary = mcp_tools.run_summary(RUN)
        returns = CONTRACTS["get_run"].returns
        self.assertIn(summary, returns)
        # The record is closed, so a field nobody published is refused.
        self.assertNotIn({**summary, "tasks": []}, returns)
        self.assertNotIn(
            {**summary, "results": {**summary["results"], "spsa": 1}}, returns
        )

    def test_an_integer_argument_is_a_json_integer(self):
        # JSON Schema counts 1.0 as an integer and true as none.
        accepts = CONTRACTS["list_finished_runs"].accepts
        self.assertIn({"page": 1}, accepts)
        self.assertIn({"page": 1.0}, accepts)
        self.assertNotIn({"page": True}, accepts)
        self.assertNotIn({"page": 0}, accepts)


class TestMcpServer(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.rundb = test_support.get_rundb()
        cls.run_id = cls._create_run(sprt=True)
        cls.plain_run_id = cls._create_run(sprt=False)
        cls.server = build_server(lambda: cls.rundb)

    @classmethod
    def tearDownClass(cls):
        cls.rundb.runs.delete_many(
            {"_id": {"$in": [ObjectId(cls.run_id), ObjectId(cls.plain_run_id)]}}
        )
        cls.rundb.db["actions"].delete_many({"username": "TestMcpUser"})
        test_support.cleanup_test_rundb(cls.rundb)

    @classmethod
    def _create_run(cls, *, sprt):
        sprt_args = {
            "alpha": 0.05,
            "beta": 0.05,
            "elo0": -2.0,
            "elo1": 2.0,
            "elo_model": "normalized",
            "state": "",
            "llr": 0.0,
            "batch_size": 1,
            "lower_bound": -2.9444389791664403,
            "upper_bound": 2.9444389791664403,
            "lost_samples": 0,
        }
        run_id = cls.rundb.new_run(
            "master",
            "mcp-branch",
            800,
            "10+0.1",
            "10+0.1",
            "book.pgn",
            "10",
            1,
            "",
            "",
            info="MCP test run",
            resolved_base="347d613b0e2c47f90cbf1c5a5affe97303f1ac3d",
            resolved_new="347d613b0e2c47f90cbf1c5a5affe97303f1ac3d",
            msg_base="base",
            msg_new="new",
            base_signature="123456",
            new_signature="654321",
            base_nets=["nn-0000000000a0.nnue"],
            new_nets=["nn-0000000000a1.nnue"],
            tests_repo="https://github.com/official-stockfish/Stockfish",
            auto_purge=False,
            username="TestMcpUser",
            start_time=datetime.now(UTC),
            **({"sprt": sprt_args} if sprt else {}),
        )
        run = cls.rundb.get_run(run_id)
        run["results"] = {
            "wins": 12,
            "losses": 10,
            "draws": 18,
            "crashes": 0,
            "time_losses": 0,
            "pentanomial": [1, 4, 8, 5, 2],
        }
        cls.rundb.buffer(run, priority=Prio.SAVE_NOW)
        return str(run_id)

    async def _call(self, name, arguments):
        async with Client(self.server, raise_exceptions=True) as client:
            return await client.call_tool(name, arguments)

    async def test_the_listing_publishes_each_contract(self):
        async with Client(self.server, raise_exceptions=True) as client:
            listing = await client.list_tools()
        published = {tool.name: tool for tool in listing.tools}
        self.assertEqual(list(published), list(CONTRACTS))
        for tool in mcp_contracts.TOOLS:
            self.assertEqual(published[tool["name"]].input_schema, tool["inputSchema"])
            self.assertEqual(
                published[tool["name"]].output_schema, tool["outputSchema"]
            )
            self.assertTrue(published[tool["name"]].annotations.read_only_hint)

    async def test_get_run_returns_the_run_summary(self):
        result = await self._call("get_run", {"run_id": self.run_id})
        self.assertFalse(result.is_error)
        summary = result.structured_content
        self.assertEqual(summary["run_id"], self.run_id)
        self.assertEqual(summary["new_tag"], "mcp-branch")
        self.assertEqual(summary["results"]["pentanomial"], [1, 4, 8, 5, 2])
        self.assertEqual(summary["sprt"]["elo1"], 2.0)
        self.assertNotIn("tasks", summary)

    async def test_get_elo_estimates_an_sprt_run(self):
        result = await self._call("get_elo", {"run_id": self.run_id})
        self.assertFalse(result.is_error)
        estimate = result.structured_content
        self.assertEqual(estimate["elo_model"], "normalized")
        self.assertEqual(len(estimate["ci"]), 2)
        self.assertLess(estimate["ci"][0], estimate["ci"][1])

    async def test_get_elo_refuses_a_run_that_is_no_sprt_test(self):
        result = await self._call("get_elo", {"run_id": self.plain_run_id})
        self.assertTrue(result.is_error)
        self.assertIn("is not an SPRT test", result.content[0].text)

    async def test_an_argument_outside_the_contract_is_a_tool_error(self):
        for arguments in ({"run_id": "nope"}, {"run_id": self.run_id, "x": 1}, {}):
            result = await self._call("get_run", arguments)
            self.assertTrue(result.is_error, repr(arguments))
            self.assertIn("get_run refuses its arguments", result.content[0].text)

    async def test_an_unknown_tool_is_a_protocol_error(self):
        async with Client(self.server, raise_exceptions=True) as client:
            with self.assertRaises(MCPError) as caught:
                await client.call_tool("nope", {})
        self.assertEqual(caught.exception.code, INVALID_PARAMS)
        self.assertEqual(caught.exception.message, "Unknown tool: nope")

    async def test_a_run_the_database_does_not_hold_is_a_tool_error(self):
        result = await self._call("get_run", {"run_id": "f" * 24})
        self.assertTrue(result.is_error)
        self.assertIn("does not exist", result.content[0].text)

    async def test_list_active_runs_holds_an_unfinished_run(self):
        result = await self._call("list_active_runs", {})
        self.assertFalse(result.is_error)
        run_ids = [run["run_id"] for run in result.structured_content["runs"]]
        self.assertIn(self.run_id, run_ids)

    async def test_list_finished_runs_reads_a_json_integer_page(self):
        result = await self._call("list_finished_runs", {"page": 1.0})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["page"], 1)
        self.assertLessEqual(
            len(result.structured_content["runs"]),
            mcp_contracts.FINISHED_RUNS_PAGE_SIZE,
        )

    async def test_search_actions_finds_what_the_filter_names(self):
        marker = f"mcp probe {datetime.now(UTC).isoformat()}"
        self.rundb.actiondb.log_message(username="TestMcpUser", message=marker)
        result = await self._call(
            "search_actions",
            {"filter": {"action": "log_message", "message": marker}, "limit": 5},
        )
        self.assertFalse(result.is_error)
        messages = [a["message"] for a in result.structured_content["actions"]]
        self.assertEqual(messages, [marker])

    async def test_search_actions_refuses_code_on_the_database(self):
        result = await self._call("search_actions", {"filter": {"$where": "1"}})
        self.assertTrue(result.is_error)
        self.assertIn("search_actions refuses its arguments", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
