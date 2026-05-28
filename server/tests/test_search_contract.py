"""Test the Phase 0 search contract scaffolding."""

import unittest

from fishtest.http.settings import (
    TYPESENSE_ACTIONS_ALIAS,
    TYPESENSE_FINISHED_RUNS_ALIAS,
)
from fishtest.search_contract import (
    ACTIONS_PARITY_CASES,
    ACTIONS_SEARCH_CONTRACT,
    FINISHED_RUNS_PARITY_CASES,
    FINISHED_RUNS_SEARCH_CONTRACT,
)


class SearchContractTests(unittest.TestCase):
    def test_actions_contract_freezes_live_route_fields_and_sorts(self):
        self.assertEqual(ACTIONS_SEARCH_CONTRACT.route, "/actions")
        self.assertEqual(ACTIONS_SEARCH_CONTRACT.alias, TYPESENSE_ACTIONS_ALIAS)
        self.assertEqual(ACTIONS_SEARCH_CONTRACT.default_sort_field, "time")
        self.assertEqual(ACTIONS_SEARCH_CONTRACT.default_sort_order, "desc")

        field_names = {field.document_field for field in ACTIONS_SEARCH_CONTRACT.fields}
        self.assertTrue(
            {
                "time",
                "action",
                "username",
                "worker",
                "message",
                "run",
                "run_id",
                "user",
                "nn",
            }.issubset(field_names)
        )

        sort_names = {
            mapping.public_name for mapping in ACTIONS_SEARCH_CONTRACT.sort_mappings
        }
        self.assertEqual(
            sort_names,
            {"time", "event", "source", "target", "comment"},
        )

    def test_finished_contract_freezes_live_route_filters_and_defaults(self):
        self.assertEqual(FINISHED_RUNS_SEARCH_CONTRACT.route, "/tests/finished")
        self.assertEqual(
            FINISHED_RUNS_SEARCH_CONTRACT.alias,
            TYPESENSE_FINISHED_RUNS_ALIAS,
        )
        self.assertEqual(
            FINISHED_RUNS_SEARCH_CONTRACT.default_sort_field,
            "last_updated",
        )
        self.assertEqual(
            FINISHED_RUNS_SEARCH_CONTRACT.default_sort_order,
            "desc",
        )
        self.assertEqual(
            FINISHED_RUNS_SEARCH_CONTRACT.query_fields,
            ("args.info",),
        )

        field_names = {
            field.document_field for field in FINISHED_RUNS_SEARCH_CONTRACT.fields
        }
        self.assertTrue(
            {
                "last_updated",
                "args.username",
                "args.info",
                "finished",
                "deleted",
                "is_green",
                "is_yellow",
                "tc_base",
            }.issubset(field_names)
        )

    def test_parity_cases_cover_route_contract_traps(self):
        action_case_names = {case.name for case in ACTIONS_PARITY_CASES}
        self.assertTrue(
            {
                "actions_text_phrase_search",
                "actions_ranked_username_substring",
                "actions_alt_sort_scope",
                "actions_run_scoped_filter",
            }.issubset(action_case_names)
        )

        finished_case_names = {case.name for case in FINISHED_RUNS_PARITY_CASES}
        self.assertTrue(
            {
                "finished_search_text_only",
                "finished_ranked_username_substring",
                "finished_navigation_redirect_to_search",
                "finished_search_drops_status_tabs",
            }.issubset(finished_case_names)
        )


if __name__ == "__main__":
    unittest.main()
