"""Pin the sets the valgebra schemas denote.

The schemas are the only place Fishtest states the shape of a document before it
reaches the database, so each case below fixes one decision: a leaf's members, a
record's required and optional keys, or a cross-field rule that no single field
can express.
"""

import copy
import json
import math
import unittest
from datetime import UTC, datetime

from bson.objectid import ObjectId
from valgebra import ValidationError, Validator, anything, intersection

from fishtest import schemas as s
from fishtest import spsa_workflow
from fishtest.stats import stat_util

OID = ObjectId("64e74776a170cb1f26fa3930")
RUN_ID = "64e74776a170cb1f26fa3930"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
LONG_WORKER = "host-4cores-abcd1234-abcd"
SHORT_WORKER = "host-4cores-abcd1234"
RUN_REF = {"run_id": "64e74776a170cb1f26fa3930", "run": "patch-abcdef0"}
# One document per action, so reachability is shown rather than not-disproven.
ACTION_WITNESSES = {
    "failed_task": {**RUN_REF, "worker": LONG_WORKER, "task_id": 0, "message": "m"},
    "crash_or_time": {**RUN_REF, "worker": LONG_WORKER, "task_id": 0, "message": "m"},
    "dead_task": {**RUN_REF, "worker": LONG_WORKER, "task_id": 0},
    "system_event": {"username": "fishtest.system", "message": "m"},
    "new_run": {**RUN_REF, "message": "m"},
    "upload_nn": {"nn": "nn-0000000000a0.nnue"},
    "modify_run": {**RUN_REF, "message": "m"},
    "delete_run": {**RUN_REF},
    "stop_run": {**RUN_REF, "message": "m"},
    "finished_run": {**RUN_REF, "message": "m"},
    "approve_run": {**RUN_REF, "message": "approved"},
    "purge_run": {**RUN_REF, "message": "m"},
    "block_user": {"user": "someone", "message": "blocked"},
    "accept_user": {"user": "someone", "message": "accepted"},
    "block_worker": {"worker": SHORT_WORKER, "message": "blocked"},
    "log_message": {"message": "m"},
    "worker_log": {"worker": LONG_WORKER, "message": "m"},
}

ACTION_NAMES = (
    "failed_task",
    "crash_or_time",
    "dead_task",
    "system_event",
    "new_run",
    "upload_nn",
    "modify_run",
    "delete_run",
    "stop_run",
    "finished_run",
    "approve_run",
    "purge_run",
    "block_user",
    "accept_user",
    "block_worker",
    "log_message",
    "worker_log",
)

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
    "throughput": 100,
    "itp": 100.0,
    "priority": 0,
    "adjudication": True,
    "sprt": SPRT,
}

SPSA = {
    "A": 50,
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
        # An int is not a float and a float is not an int: each field says
        # which one it stores.
        self.assert_members(s.ufloat, [0.0, 1.5], [0, 1, -0.5, "0", None])
        self.assert_members(s.sfloat, [0.5], [0.0, -1.0, 1])
        self.assert_members(s.uint, [0, 1], [-1, 0.0, 1.5, "1"])
        self.assert_members(s.suint, [1], [0, -1, 1.0])
        self.assert_members(s.game_count, [0, 2, 100], [1, 3, -2, 2.0])

    def test_datetime_utc(self):
        self.assert_members(s.datetime_utc, [NOW], [datetime(2026, 1, 1), NOW.date()])

    def test_book(self):
        self.assert_members(
            s.book, ["UHO.epd", "UHO.pgn"], ["UHO.txt", "UHO", "", "UHO.epd\n"]
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
        # A literal is a typed singleton: same type and equal, so 0 is not 0.0
        # and 1 is not True.
        self.assertTrue(s.action_name.is_valid("new_run"))
        self.assertFalse(s.action_name.is_valid("no_such_action"))
        finished = Validator({"nps": 0.0})
        self.assertTrue(finished.is_valid({"nps": 0.0}))
        self.assertFalse(finished.is_valid({"nps": 0}))


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

    def test_keys_in_constrains_the_keys_beside_the_mapping(self):
        # A clause's key names a whole type, so `dict[run_id, V]` is refused
        # where it is written; the key shape rides a predicate instead.
        keyed = intersection(dict[str, int], s.keys_in(s.run_id))
        self.assertTrue(keyed.is_valid({RUN_ID: 1}))
        self.assertFalse(keyed.is_valid({"not-a-run-id": 1}))
        self.assertFalse(keyed.is_valid({RUN_ID: "not an int"}))
        # A declared field is governed by the field, not by the clause, so it is
        # exempt from the key shape.
        record = intersection({"last": str, str: int}, s.keys_in(s.run_id, "last"))
        self.assertTrue(record.is_valid({"last": "x", RUN_ID: 1}))
        self.assertFalse(record.is_valid({"last": "x", "other": 1}))
        with self.assertRaises(NotImplementedError):
            Validator(dict[s.run_id, int])


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

    def test_a_worker_message_is_narrowed_to_what_an_action_holds(self):
        # The API accepts any string and an action caps it, so the server
        # narrows in between; without that a long report was accepted and then
        # dropped when the action failed to validate.
        long_message = "x" * (s.ACTION_MESSAGE_SIZE + 1)
        request = {
            "password": "secret",
            "worker_info": WORKER_INFO_API,
            "message": long_message,
        }
        s.api_schema.validate(request)
        self.assertFalse(s.action_message.is_valid(long_message))
        self.assertTrue(
            s.action_message.is_valid(long_message[: s.ACTION_MESSAGE_SIZE])
        )

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
        run["args"]["spsa"] = SPSA
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

    def test_sprt_bounds_are_the_ones_alpha_and_beta_imply(self):
        run = run_with()
        alpha = beta = 0.05
        # what stat_util.SPRT stores, from the alpha and beta this record pins
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

    def test_spsa_flips_are_one_bit_per_parameter(self):
        run = run_with()
        del run["args"]["sprt"]
        run["args"]["spsa"] = SPSA
        run["tasks"][0]["active"] = True
        run["tasks"][0]["spsa_params"] = {"iter": 0, "packed_flips": b"\x80"}
        run["finished"] = False
        run["workers"] = 1
        run["cores"] = 4
        run["committed_games"] = 100
        s.runs_schema.validate(run, fail_fast=True)
        # one parameter needs one byte, not two
        run["tasks"][0]["spsa_params"]["packed_flips"] = b"\x80\x00"
        self.assertFalse(s.runs_schema.is_valid(run))

    def test_unknown_key_is_rejected(self):
        self.assertFalse(s.runs_schema.is_valid(run_with(unknown_field=1)))


class TestWritersAgreeWithTheSchemas(unittest.TestCase):
    """A field says which type it stores, so the code that fills it must store
    that type. These pin the two places that build a document from scratch."""

    def test_sprt_constructor(self):
        sprt = stat_util.SPRT(
            alpha=0.05,
            beta=0.05,
            elo0=0.0,
            elo1=2.0,
            elo_model="normalized",
            batch_size=32,
        )
        s.sprt_schema.validate(sprt, fail_fast=True)

    def test_sprt_constructor_survives_an_update(self):
        sprt = stat_util.SPRT(
            alpha=0.05,
            beta=0.05,
            elo0=0.0,
            elo1=2.0,
            elo_model="normalized",
            batch_size=1,
        )
        results = {
            "wins": 30,
            "losses": 30,
            "draws": 40,
            "pentanomial": [2, 8, 30, 8, 2],
        }
        stat_util.update_SPRT(results, sprt)
        s.sprt_schema.validate(sprt, fail_fast=True)

    def test_spsa_form_parsing(self):
        spsa = spsa_workflow.build_spsa_state(
            {
                "spsa_A": "0.1",
                "spsa_alpha": "0.602",
                "spsa_gamma": "0.101",
                "spsa_raw_params": "Hash,16,8,32,2,0.002",
            },
            num_games=1000,
        )
        s.spsa_schema.validate(spsa, fail_fast=True)


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

    def test_books_are_parsed_and_checked_in_one_pass(self):
        # books.json arrives as bytes from a third party, so it is validated on
        # the way in rather than after it is stored.
        book = {
            "total": 10,
            "white": 4,
            "black": 6,
            "min_depth": None,
            "max_depth": 8,
            "sri": "sha384-" + "A" * 64,
        }
        raw = json.dumps({"UHO.epd": book}).encode()
        self.assertEqual(s.books_schema.load(raw), {"UHO.epd": book})
        with self.assertRaises(ValidationError):
            s.books_schema.load(json.dumps({"UHO.epd": {"total": 10}}).encode())
        with self.assertRaises(ValidationError) as caught:
            s.books_schema.load(b"not json at all")
        self.assertEqual(caught.exception.code, "json_invalid")

    def test_legacy_usernames(self):
        self.assertEqual(s.legacy_usernames_schema.ensure(["a", "b"]), ["a", "b"])
        with self.assertRaises(ValidationError):
            s.legacy_usernames_schema.validate(["a", 1])
        with self.assertRaises(ValidationError):
            s.legacy_usernames_schema.validate({"a"})

    def test_wtt_map(self):
        s.wtt_map_schema.validate({"host-4cores-abcd1234": (RUN_ID, 0)})
        self.assertFalse(
            s.wtt_map_schema.is_valid({"host-4cores-abcd1234": [RUN_ID, 0]})
        )
        self.assertFalse(s.wtt_map_schema.is_valid({"not-a-worker": (RUN_ID, 0)}))

    def test_connections_counter(self):
        s.connections_counter_schema.validate({"1.2.3.4": 1})
        self.assertFalse(s.connections_counter_schema.is_valid({"1.2.3.4": 0}))
        self.assertFalse(s.connections_counter_schema.is_valid({"not-an-ip": 1}))

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
        # A key narrowed by a constraint is refused where it is written, so the
        # key shape is checked beside the mapping; this is what makes it bite.
        self.assertFalse(s.books_schema.is_valid({"UHO.txt": book}))


class TestSchemaAlgebra(unittest.TestCase):
    """Ask the schemas about themselves.

    is_subtype_of, is_equivalent and is_empty are sound in the True direction
    only: a True is always correct, a False is either a genuine non-relation or
    one valgebra does not prove. So no assertion here reads a False as a proof.

    `relation_to` is what tells those two apart: "subset" is exactly what
    is_subtype_of answers True for, "not_subset" is a *refutation* -- some value
    of the subject lies outside the other schema -- and "undecided" is the
    conservative answer. A negative claim is asserted as "not_subset" where the
    procedure decides it, and through membership, which is exact in both
    directions, where it does not.
    """

    def module_validators(self):
        return {
            name: value
            for name, value in vars(s).items()
            if isinstance(value, Validator) and not name.startswith("_")
        }

    def test_no_schema_is_provably_uninhabited(self):
        """Catch the uninhabited schemas the decision procedure decides.

        `is_empty()` is sound in the True direction only, so this asserts a
        negative and cannot prove the rest inhabited: a contradictory
        refinement is caught, an `intersection(record, bare_callable)` is not
        -- `test_no_schema_swallowed_a_bare_callable` is what covers that one.
        The name says what the assertion is worth.
        """
        empty = [name for name, v in self.module_validators().items() if v.is_empty()]
        self.assertEqual(empty, [])

    def test_a_refinement_is_a_subtype_of_what_it_refines(self):
        for narrow, wide in [
            (s.suint, s.uint),
            (s.game_count, s.uint),
            (s.sfloat, s.ufloat),
            (s.uint, int),
            (s.ufloat, float),
            (s.valid_username, s.username),
            (s.username, s.action_username),
            (s.short_worker_name, s.worker_name_or_show),
            (s.epd_file, s.book),
            (s.pgn_file, s.book),
        ]:
            self.assertTrue(narrow.is_subtype_of(wide), f"{narrow!r} </= {wide!r}")

    def test_sibling_leaves_are_refuted_against_each_other(self):
        # A "not_subset" is a statement about a value, so these are refutations
        # rather than unproven relations: each pair names a distinction a field
        # relies on to say which of the two it stores.
        for left, right in [
            (s.uint, s.ufloat),
            (s.ufloat, s.uint),
            (s.epd_file, s.pgn_file),
            (s.run_id, s.run_name),
            (s.short_worker_name, s.long_worker_name),
        ]:
            self.assertEqual(left.relation_to(right), "not_subset", f"{left!r}")

    def test_an_int_field_and_a_float_field_share_no_value(self):
        # The two are disjoint sets, which is what lets a field state which one
        # it stores.
        self.assertTrue(intersection(s.uint, s.ufloat).is_empty())

    def test_the_action_name_guard_is_redundant(self):
        # The union alone already decides membership, since every branch pins
        # its own "action". The guard is kept for the error it produces, and
        # this proves keeping it does not change the set.
        guard = s.open_record({"action": s.action_name})
        self.assertTrue(s.action_schema.is_subtype_of(guard))

    def test_the_discriminant_selects_the_shape(self):
        # The branches are disjoint by construction: each pins its own "action",
        # and a document carries one. What is worth checking is that the name
        # decides which fields are required, so a document wearing the wrong
        # name is rejected on the fields the new name demands.
        #
        # Note this is not "no other name is accepted": two actions can require
        # the same fields, and then relabelling produces a document that really
        # is a valid instance of the other branch. That is the discriminant
        # working, not an overlap.
        system_event = {
            "_id": OID,
            "time": 1.0,
            "action": "system_event",
            "username": "fishtest.system",
            "message": "started",
        }
        s.action_schema.validate(system_event)
        for name in ("new_run", "upload_nn", "delete_run", "failed_task"):
            self.assertFalse(
                s.action_schema.is_valid({**system_event, "action": name}), name
            )

    def test_every_branch_of_the_union_is_reachable(self):
        # A branch no document can reach is dead weight in the union. Shown
        # with a witness per action: membership is exact, where a False from
        # is_empty() would only mean "not proven unreachable".
        for name, extra in ACTION_WITNESSES.items():
            document = {"_id": OID, "time": 1.0, "username": "user00", **extra}
            document["action"] = name
            s.action_schema.validate(document, fail_fast=True)
        self.assertEqual(sorted(ACTION_WITNESSES), sorted(ACTION_NAMES))

    def test_the_recipes_denote_what_they_claim(self):
        self.assertTrue(s.has("a").is_equivalent(Validator({"a": anything}).open()))
        self.assertTrue(
            s.one_of("a", "b").is_equivalent(
                intersection(s.at_least_one_of("a", "b"), s.at_most_one_of("a", "b"))
            )
        )

    def test_open_record_opens_one_level_where_open_opens_all(self):
        flat = {"active": False, "n": int}
        self.assertTrue(s.open_record(flat).is_equivalent(Validator(flat).open()))
        nested = {"active": False, "stats": {"wins": int}}
        extra_inside = {"active": False, "stats": {"wins": 1, "x": 2}}
        self.assertFalse(s.open_record(nested).is_valid(extra_inside))
        self.assertTrue(Validator(nested).open().is_valid(extra_inside))

    def test_the_stored_worker_info_extends_the_received_one(self):
        # Closed-record width is inside what valgebra decides, so this relation
        # is proven rather than sampled: everything a worker sends is admitted
        # by the stored shape once its own extra keys are allowed for, and the
        # closed received shape does not admit them.
        self.assertTrue(
            s.worker_info_schema_runs.is_subtype_of(s.worker_info_schema_api.open())
        )
        # The other half is a membership question, which is exact, rather than
        # a subtyping one, whose False would only mean "not proven".
        self.assertTrue(s.worker_info_schema_runs.is_valid(WORKER_INFO_RUNS))
        self.assertFalse(s.worker_info_schema_api.is_valid(WORKER_INFO_RUNS))

    def test_a_raw_input_schema_admits_the_persisted_form(self):
        # One regular language inside another is decided, so the containment is
        # proven rather than sampled: a canonical repo is an accepted input, and
        # an input with the trailing slash is one the persisted form refuses.
        self.assertEqual(s.github_repo.relation_to(s.github_repo_input), "subset")
        self.assertEqual(s.tests_repo.relation_to(s.tests_repo_input), "subset")
        self.assertEqual(s.github_repo_input.relation_to(s.github_repo), "not_subset")
        repos = [
            "https://github.com/official-stockfish/Stockfish",
            "https://www.github.com/a/b",
            "https://github.com/a.b-c_d/e.f-g_h",
        ]
        for repo in repos:
            self.assertTrue(s.github_repo.is_valid(repo))
            self.assertTrue(s.github_repo_input.is_valid(repo))
            self.assertTrue(s.github_repo_input.is_valid(repo + "/"))
            self.assertFalse(s.github_repo.is_valid(repo + "/"))

    def test_no_schema_swallowed_a_bare_callable(self):
        # A callable is a predicate as `Annotated` metadata, but a *schema spec*
        # on its own is a constant: `intersection(record, my_check)` compiles to
        # `intersection(record, Literal[<function my_check>])`, which admits
        # nothing. valgebra does not prove that intersection empty, so the
        # emptiness check above cannot catch it -- the stable repr can.
        for name, v in self.module_validators().items():
            self.assertNotIn("Literal[<function", repr(v), name)
            self.assertNotIn("Literal[<built-in", repr(v), name)

    def test_a_schema_prints_back_as_its_annotation(self):
        self.assertEqual(repr(s.uint), "Annotated[int, Ge(0)]")
        self.assertEqual(repr(s.run_id), "Annotated[str, Regex('[a-f0-9]{24}')]")


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
