"""Pin the sets the valgebra schemas denote.

The schemas are the only place Fishtest states the shape of a document before it
reaches the database, so each case below fixes one decision: a leaf's members, a
record's required and optional keys, or a cross-field rule that no single field
can express.
"""

import copy
import math
import unittest
from datetime import UTC, datetime

from bson.objectid import ObjectId
from valgebra import ValidationError

from fishtest import schemas as s

OID = ObjectId("64e74776a170cb1f26fa3930")
RUN_ID = "64e74776a170cb1f26fa3930"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

ZERO_RESULTS = {
    "wins": 0,
    "draws": 0,
    "losses": 0,
    "crashes": 0,
    "time_losses": 0,
    "pentanomial": [0, 0, 0, 0, 0],
}

WORKER_INFO_API = {
    "uname": "Linux 6.1",
    "architecture": ["64bit", "ELF"],
    "concurrency": 4,
    "max_memory": 8192,
    "min_threads": 1,
    "username": "user00",
    "version": 325,
    "python_version": [3, 14, 0],
    "gcc_version": [13, 2, 0],
    "compiler": "g++",
    "unique_key": "abcd1234-abcd-abcd-abcd-abcdef012345",
    "modified": False,
    "worker_arch": "x86-64-avx2",
    "ARCH": "x86-64",
    "nps": 1000000.0,
    "near_github_api_limit": False,
}

WORKER_INFO_RUNS = {
    **WORKER_INFO_API,
    "remote_addr": "1.2.3.4",
    "country_code": "IT",
}

SPRT = {
    "alpha": 0.05,
    "beta": 0.05,
    "elo0": 0.0,
    "elo1": 2.0,
    "elo_model": "normalized",
    "state": "",
    "llr": 0.0,
    "batch_size": 32,
    "lower_bound": -math.log(19),
    "upper_bound": math.log(19),
    "lost_samples": 0,
}

ARGS = {
    "base_tag": "master",
    "new_tag": "patch",
    "base_nets": ["nn-0000000000a0.nnue"],
    "new_nets": ["nn-0000000000a1.nnue"],
    "num_games": 100,
    "tc": "10+0.1",
    "new_tc": "10+0.1",
    "book": "UHO_Lichess.epd",
    "book_depth": "8",
    "threads": 1,
    "resolved_base": "a" * 40,
    "resolved_new": "b" * 40,
    "msg_base": "base msg",
    "msg_new": "new msg",
    "base_options": "Hash=16",
    "new_options": "Hash=16",
    "info": "",
    "base_signature": "123456",
    "new_signature": "123457",
    "username": "user00",
    "tests_repo": "https://github.com/official-stockfish/Stockfish",
    "auto_purge": False,
    "throughput": 100.0,
    "itp": 100.0,
    "priority": 0.0,
    "adjudication": True,
    "sprt": SPRT,
}

TASK = {
    "num_games": 100,
    "active": False,
    "last_updated": NOW,
    "start": 0,
    "stats": ZERO_RESULTS,
    "worker_info": WORKER_INFO_RUNS,
}

# A finished run whose aggregates match its single inactive, zero-scoring task.
RUN = {
    "_id": OID,
    "version": s.RUN_VERSION,
    "start_time": NOW,
    "last_updated": NOW,
    "tc_base": 10.0,
    "approved": True,
    "approver": "user00",
    "finished": True,
    "deleted": False,
    "failed": False,
    "failures": 0,
    "is_green": False,
    "is_yellow": False,
    "workers": 0,
    "cores": 0,
    "committed_games": 0,
    "total_games": 100,
    "results": ZERO_RESULTS,
    "nps": 0.0,
    "games_per_minute": 0.0,
    "args": ARGS,
    "tasks": [TASK],
    "bad_tasks": [],
}


def run_with(**changes):
    run = copy.deepcopy(RUN)
    run.update(copy.deepcopy(changes))
    return run


class TestLeaves(unittest.TestCase):
    def assert_members(self, schema, members, non_members):
        for value in members:
            self.assertTrue(schema.is_valid(value), f"{value!r} should be a member")
        for value in non_members:
            self.assertFalse(schema.is_valid(value), f"{value!r} should be rejected")

    def test_run_id(self):
        self.assert_members(s.run_id, [RUN_ID], ["", "zz", RUN_ID + "0", OID, None])

    def test_run_id_pgns(self):
        self.assert_members(
            s.run_id_pgns, [f"{RUN_ID}-0", f"{RUN_ID}-12"], [RUN_ID, f"{RUN_ID}-01"]
        )

    def test_worker_names(self):
        self.assert_members(
            s.short_worker_name,
            ["host-4cores-abcd1234"],
            ["host-4cores-abcd1234-abcd", "host", 4],
        )
        self.assert_members(
            s.long_worker_name,
            ["host-4cores-abcd1234-abcd", "host-4cores-abcd1234-abcd*"],
            ["host-4cores-abcd1234"],
        )

    def test_username(self):
        self.assert_members(s.username, ["ab", "user00"], ["a", "user-00", ""])
        self.assert_members(
            s.action_username, ["fishtest.system", "user00"], ["fishtest system"]
        )

    def test_legacy_username_reads_the_startup_set(self):
        self.assertFalse(s.username.is_valid("a"))
        s.legacy_usernames.add("a")
        try:
            self.assertTrue(s.username.is_valid("a"))
        finally:
            s.legacy_usernames.discard("a")

    def test_numbers(self):
        # vtjson's `float` admitted ints; `number` keeps that set.
        self.assert_members(s.unumber, [0, 0.0, 1, 1.5], [-1, -0.5, "0", None])
        self.assert_members(s.sunumber, [1, 0.5], [0, 0.0, -1])
        self.assert_members(s.uint, [0, 1], [-1, 0.0, 1.5, "1"])
        self.assert_members(s.suint, [1], [0, -1, 1.0])
        self.assert_members(s.even_uint, [0, 2, 100], [1, 3, -2, 2.0])

    def test_datetime_utc(self):
        self.assert_members(s.datetime_utc, [NOW], [datetime(2026, 1, 1), NOW.date()])

    def test_book(self):
        self.assert_members(
            s.book, ["UHO.epd", "UHO.pgn"], ["UHO.txt", "UHO", "", ".epd/"]
        )

    def test_repos(self):
        self.assert_members(
            s.github_repo,
            ["https://github.com/a/b", "https://www.github.com/a/b"],
            ["https://github.com/a/b/", "http://github.com/a/b", ""],
        )
        self.assert_members(
            s.github_repo_input,
            ["https://github.com/a/b", "https://github.com/a/b/"],
            ["https://gitlab.com/a/b", ""],
        )
        self.assert_members(s.tests_repo_input, ["", "https://github.com/a/b/"], ["x"])

    def test_gzip_data(self):
        self.assert_members(
            s.gzip_data, [b"\x1f\x8b\x08\x00"], [b"", b"PK\x03\x04", "\x1f\x8b"]
        )

    def test_option_list(self):
        self.assert_members(
            s.option_list,
            ["", "Hash=16", "Hash=16 Threads=2"],
            ["Hash=16  Threads=2", "Hash", "Hàsh=16"],
        )

    def test_ip_address(self):
        self.assert_members(
            s.ip_address, ["1.2.3.4", "::1"], ["1.2.3.4.5", "", 16909060]
        )

    def test_email(self):
        self.assert_members(s.email, ["user@example.com"], ["user@", "user", ""])

    def test_regex_pattern(self):
        self.assert_members(s.regex_pattern, ["[a-z]+", ""], ["[a-z", 1])

    def test_residual_color(self):
        self.assert_members(s.residual_color, ["green", "red"], ["blue", None])

    def test_literals_are_typed_singletons(self):
        # valgebra follows the typing spec: 1 is not True and 0 is not 0.0.
        self.assertTrue(s.action_name.is_valid("new_run"))
        self.assertFalse(s.action_name.is_valid("no_such_action"))
        self.assertFalse(s.uint.is_valid(True) and not s.uint.is_valid(1))


class TestAlgebraRecipes(unittest.TestCase):
    def test_has(self):
        self.assertTrue(s.has("a", "b").is_valid({"a": 1, "b": 2, "c": 3}))
        self.assertFalse(s.has("a", "b").is_valid({"a": 1}))
        self.assertFalse(s.has("a").is_valid("not a mapping"))

    def test_key_cardinality(self):
        self.assertTrue(s.at_least_one_of("a", "b").is_valid({"b": 1}))
        self.assertFalse(s.at_least_one_of("a", "b").is_valid({"c": 1}))
        self.assertTrue(s.at_most_one_of("a", "b").is_valid({}))
        self.assertFalse(s.at_most_one_of("a", "b").is_valid({"a": 1, "b": 2}))
        self.assertTrue(s.one_of("a", "b").is_valid({"a": 1}))
        self.assertFalse(s.one_of("a", "b").is_valid({}))
        self.assertFalse(s.one_of("a", "b").is_valid({"a": 1, "b": 2}))

    def test_implies(self):
        schema = s.implies({"a": True}, {"b": 1}).open()
        self.assertTrue(schema.is_valid({"a": True, "b": 1}))
        self.assertFalse(schema.is_valid({"a": True, "b": 2}))
        self.assertTrue(schema.is_valid({"a": False, "b": 2}))

    def test_open_record_leaves_nested_records_closed(self):
        schema = s.open_record({"inner": {"x": 1}})
        self.assertTrue(schema.is_valid({"inner": {"x": 1}, "extra": "ok"}))
        self.assertFalse(schema.is_valid({"inner": {"x": 1, "extra": "no"}}))


class TestDocuments(unittest.TestCase):
    def test_user(self):
        user = {
            "_id": OID,
            "username": "user00",
            "password": "secret",
            "registration_time": NOW,
            "pending": False,
            "blocked": False,
            "email": "user00@example.com",
            "groups": ["approvers"],
            "tests_repo": "",
            "machine_limit": 16,
        }
        s.user_schema.validate(user)
        self.assertFalse(s.user_schema.is_valid({**user, "groups": ["a", "a"]}))
        self.assertFalse(s.user_schema.is_valid({**user, "unknown": 1}))
        self.assertFalse(
            s.user_schema.is_valid({k: v for k, v in user.items() if k != "email"})
        )

    def test_worker(self):
        worker = {
            "worker_name": "host-4cores-abcd1234",
            "blocked": False,
            "message": "hello",
            "last_updated": NOW,
        }
        s.worker_schema.validate(worker)
        self.assertFalse(s.worker_schema.is_valid({**worker, "message": "x" * 501}))

    def test_pgns(self):
        record = {"run_id": f"{RUN_ID}-0", "pgn_zip": b"\x1f\x8b\x08\x00", "size": 4}
        s.pgns_schema.validate(record)
        self.assertFalse(s.pgns_schema.is_valid({**record, "size": 5}))

    def test_nn_requires_both_tests_when_one_is_present(self):
        net = {"downloads": 0, "name": "nn-0000000000a0.nnue", "user": "user00"}
        s.nn_schema.validate(net)
        self.assertFalse(s.nn_schema.is_valid({**net, "is_master": True}))
        tested = {
            **net,
            "is_master": True,
            "first_test": {"date": NOW, "id": RUN_ID},
            "last_test": {"date": NOW, "id": RUN_ID},
        }
        s.nn_schema.validate(tested)
        backwards = copy.deepcopy(tested)
        backwards["first_test"]["date"] = datetime(2027, 1, 1, tzinfo=UTC)
        self.assertFalse(s.nn_schema.is_valid(backwards))

    def test_action_is_a_tagged_union(self):
        action = {
            "_id": OID,
            "time": 1.0,
            "action": "system_event",
            "username": "fishtest.system",
            "message": "started",
        }
        s.action_schema.validate(action)
        # the discriminant selects the branch: another action's fields do not fit
        self.assertFalse(s.action_schema.is_valid({**action, "action": "new_run"}))
        self.assertFalse(s.action_schema.is_valid({**action, "action": "unknown"}))
        # system_event is the one action reserved to the system user
        self.assertFalse(s.action_schema.is_valid({**action, "username": "user00"}))

    def test_action_stop_run_needs_worker_and_task_together(self):
        action = {
            "_id": OID,
            "time": 1.0,
            "action": "stop_run",
            "username": "user00",
            "run_id": RUN_ID,
            "run": "patch-abcdef0",
            "message": "stopped",
        }
        s.action_schema.validate(action)
        self.assertFalse(s.action_schema.is_valid({**action, "task_id": 0}))
        s.action_schema.validate(
            {**action, "task_id": 0, "worker": "host-4cores-abcd1234-abcd"}
        )

    def test_api_request(self):
        request = {
            "password": "secret",
            "worker_info": WORKER_INFO_API,
            "run_id": RUN_ID,
            "task_id": 0,
            "stats": ZERO_RESULTS,
        }
        s.api_schema.validate(request)
        # a task_id is only meaningful beside its run_id
        self.assertFalse(
            s.api_schema.is_valid({k: v for k, v in request.items() if k != "run_id"})
        )
        # the access check reads the same body, and admits the extra keys
        s.api_access_schema.validate(request)

    def test_results_must_agree_with_the_pentanomial(self):
        s.results_schema.validate(ZERO_RESULTS)
        self.assertFalse(s.results_schema.is_valid({**ZERO_RESULTS, "wins": 1}))


class TestRunsSchema(unittest.TestCase):
    def test_valid_run(self):
        s.runs_schema.validate(RUN, fail_fast=True)

    def test_aggregates_must_match_the_tasks(self):
        for field, value in [
            ("cores", 1),
            ("workers", 1),
            ("committed_games", 1),
            ("total_games", 2),
        ]:
            self.assertFalse(s.runs_schema.is_valid(run_with(**{field: value})), field)

    def test_finished_run_is_idle(self):
        self.assertFalse(s.runs_schema.is_valid(run_with(nps=1.0)))
        running = run_with(finished=False, workers=1, cores=4, committed_games=100)
        running["tasks"][0]["active"] = True
        s.runs_schema.validate(running, fail_fast=True)

    def test_flags_are_exclusive(self):
        self.assertFalse(
            s.runs_schema.is_valid(run_with(is_green=True, is_yellow=True))
        )

    def test_failed_run_has_failures(self):
        self.assertFalse(s.runs_schema.is_valid(run_with(failed=True, failures=0)))

    def test_approval_carries_an_approver(self):
        self.assertFalse(s.runs_schema.is_valid(run_with(approved=True, approver="")))
        self.assertFalse(s.runs_schema.is_valid(run_with(approved=False)))

    def test_sprt_and_spsa_are_exclusive(self):
        run = run_with()
        run["args"]["spsa"] = {
            "A": 1.0,
            "alpha": 0.602,
            "gamma": 0.101,
            "raw_params": "p,1,0,2,0.1,0.02",
            "iter": 0,
            "num_iter": 100,
            "params": [
                {
                    "name": "p",
                    "start": 1.0,
                    "min": 0.0,
                    "max": 2.0,
                    "c_end": 0.1,
                    "r_end": 0.02,
                    "c": 0.1,
                    "a_end": 1.0,
                    "a": 1.0,
                    "theta": 1.0,
                }
            ],
        }
        self.assertFalse(s.runs_schema.is_valid(run))
        del run["args"]["sprt"]
        s.runs_schema.validate(run, fail_fast=True)

    def test_sprt_carries_exactly_one_of_overshoot_and_lost_samples(self):
        run = run_with()
        del run["args"]["sprt"]["lost_samples"]
        self.assertFalse(s.runs_schema.is_valid(run))
        run["args"]["sprt"]["overshoot"] = {
            "last_update": 0,
            "skipped_updates": 0,
            "ref0": 0.0,
            "m0": 0.0,
            "sq0": 0.0,
            "ref1": 0.0,
            "m1": 0.0,
            "sq1": 0.0,
        }
        s.runs_schema.validate(run, fail_fast=True)

    def test_sprt_bounds_tolerate_their_own_computation(self):
        run = run_with()
        alpha = beta = 0.05
        run["args"]["sprt"]["lower_bound"] = math.log(beta / (1 - alpha))
        run["args"]["sprt"]["upper_bound"] = math.log((1 - beta) / alpha)
        s.runs_schema.validate(run, fail_fast=True)
        run["args"]["sprt"]["lower_bound"] = -1.0
        self.assertFalse(s.runs_schema.is_valid(run))

    def test_bad_task_is_inactive_and_scoreless(self):
        run = run_with()
        run["tasks"][0]["bad"] = True
        s.runs_schema.validate(run, fail_fast=True)
        run["tasks"][0]["active"] = True
        self.assertFalse(s.runs_schema.is_valid(run))

    def test_unknown_key_is_rejected(self):
        self.assertFalse(s.runs_schema.is_valid(run_with(unknown_field=1)))


class TestInternalStructures(unittest.TestCase):
    def test_cache(self):
        cache = {
            RUN_ID: {
                "run": RUN,
                "is_changed": False,
                "last_sync_time": 1.0,
                "last_access_time": 1.0,
                "priority": 0,
            }
        }
        s.cache_schema.validate(cache, fail_fast=True)
        self.assertFalse(s.cache_schema.is_valid({"not-a-run-id": cache[RUN_ID]}))

    def test_wtt_map(self):
        s.wtt_map_schema.validate({"host-4cores-abcd1234": (RUN_ID, 0)})
        self.assertFalse(
            s.wtt_map_schema.is_valid({"host-4cores-abcd1234": [RUN_ID, 0]})
        )

    def test_connections_counter(self):
        s.connections_counter_schema.validate({"1.2.3.4": 1})
        self.assertFalse(s.connections_counter_schema.is_valid({"1.2.3.4": 0}))

    def test_unfinished_runs(self):
        s.unfinished_runs_schema.validate({RUN_ID})
        s.unfinished_runs_schema.validate(set())
        self.assertFalse(s.unfinished_runs_schema.is_valid([RUN_ID]))

    def test_worker_runs_mixes_a_record_with_a_catch_all(self):
        s.worker_runs_schema.validate(
            {"host-4cores-abcd1234": {RUN_ID: True, "last_run": RUN_ID}}
        )
        self.assertFalse(
            s.worker_runs_schema.is_valid(
                {"host-4cores-abcd1234": {"nonsense": True, "last_run": RUN_ID}}
            )
        )

    def test_books(self):
        book = {
            "total": 10,
            "white": 4,
            "black": 6,
            "min_depth": None,
            "max_depth": 8,
            "sri": "sha384-" + "A" * 64,
        }
        s.books_schema.validate({"UHO.epd": book})
        self.assertFalse(s.books_schema.is_valid({"UHO.epd": {**book, "total": 11}}))


class TestErrorModel(unittest.TestCase):
    def test_error_carries_a_code_and_a_path(self):
        with self.assertRaises(ValidationError) as caught:
            s.runs_schema.validate(run_with(version="24"), fail_fast=True)
        error = caught.exception.errors[0]
        self.assertEqual(error["path"], ("version",))
        self.assertEqual(error["code"], "int_type")

    def test_failures_aggregate_by_default(self):
        request = {"password": 1, "worker_info": 2}
        with self.assertRaises(ValidationError) as caught:
            s.api_access_schema.validate(request)
        self.assertEqual(
            sorted(error["path"] for error in caught.exception.errors),
            [("password",), ("worker_info",)],
        )

    def test_a_raising_cross_field_check_keeps_its_message(self):
        with self.assertRaises(ValidationError) as caught:
            s.runs_schema.validate(run_with(cores=7), fail_fast=False)
        self.assertIn(
            "Cores from tasks: 0. Cores from run: 7",
            str(caught.exception),
        )


if __name__ == "__main__":
    unittest.main()
