# This file describes some of the data structures used by Fishtest so that they
# can be statically validated before they are processed further or written
# to the database.
#
# The schemas are valgebra validators. A schema denotes a *set* of Python values
# and validation is membership: the checked object is never copied or coerced.
# Standard typing annotations are the notation; `union`, `intersection` and
# `complement` compose them, and `Annotated[T, ...]` narrows a base with the
# annotated-types markers. See https://ppigazzini.github.io/valgebra/ for the
# schema language, the Boolean algebra and the error model.
#
# These schemas were previously written in vtjson. The set of accepted documents
# is unchanged except where noted below, and each of these differences is either
# unreachable for a document Fishtest writes or is stated at its definition:
#
#   - vtjson's `float` schema also admitted ints. `number` below keeps that set,
#     and every field that was a bare `float` uses it.
#   - a vtjson constant matched by `==`, so `1` also accepted `True` and `1.0`.
#     valgebra follows the typing spec: a constant is a typed singleton. A float
#     constant is therefore exact here; `close_to` restores the old tolerance for
#     the two SPRT bounds, which are stored from a different computation.
#   - `magic("application/gzip")` asked libmagic. `is_gzip_data` reads the gzip
#     signature directly, which is the test libmagic performs, and drops the
#     libmagic dependency.
#   - `glob("*.epd")` matched with `pathlib.PurePath.match`. The anchored regex
#     below accepts the same names and rejects a trailing-slash spelling that no
#     book name uses.
#   - `ip_address` also admitted ints and bytes. `remote_addr` and the
#     connections counter only ever hold strings.
#   - a failure is a `valgebra.ValidationError` carrying structured
#     `code`/`path`/`expected`/`value` items rather than one message string.

import copy
import ipaddress
import math
import re
from datetime import UTC, datetime
from itertools import combinations
from typing import Annotated, Literal

import annotated_types as at
from bson.objectid import ObjectId
from email_validator import validate_email
from valgebra import (
    Regex,
    Validator,
    anything,
    complement,
    intersection,
    union,
)

import fishtest.stats.stat_util
from fishtest.constants import (
    PASSWORD_MAX_LENGTH,
    VALID_USERNAME_PATTERN,
    supported_arches,
    supported_compilers,
)

# vtjson's `float` admitted ints as well, and documents already in the database
# rely on it: an integral value read back from Mongo is an int. valgebra's own
# `float` is floats-only, so spell the wider set.
number = int | float


# Algebra recipes. valgebra ships the irreducible Boolean algebra; the patterns
# below reduce to it and are composed here rather than imported.


def satisfies(predicate, base=dict):
    """The values in `base` for which `predicate` is truthy.

    A predicate leaves Rust for Python, so it is used only for the checks the
    markers cannot express. A predicate that raises is reported as a distinct
    `predicate_error`, which is how the cross-field checks below surface their
    own diagnostic message.
    """
    return Annotated[base, at.Predicate(predicate)]


def implies(condition, then, otherwise=anything):
    """A value matching `condition` must also match `then`, else `otherwise`.

    "Condition implies consequent" is a union of two intersections: a value
    either matches the condition and must then satisfy the consequent, or fails
    the condition and must satisfy the alternative.
    """
    return union(
        intersection(condition, then),
        intersection(complement(condition), otherwise),
    )


def has(*names):
    """A mapping that carries every one of `names`, whatever the values are."""
    return intersection(*(Validator({name: anything}).open() for name in names))


def at_least_one_of(*names):
    """A mapping that carries at least one of `names`."""
    return union(*(has(name) for name in names))


def at_most_one_of(*names):
    """A mapping that carries at most one of `names`."""
    return intersection(
        dict,
        complement(union(*(has(a, b) for a, b in combinations(names, 2)))),
    )


def one_of(*names):
    """A mapping that carries exactly one of `names`."""
    return intersection(at_least_one_of(*names), at_most_one_of(*names))


def keys_in(key_schema, *fields):
    """A mapping whose every undeclared key belongs to `key_schema`.

    A clause's key names a whole *type*, so a key narrowed by a constraint is
    refused where it is written: a narrowed key names part of a type, and two
    such clauses can overlap without either containing the other. The keys are
    checked beside the mapping instead — meet this with the mapping's own shape,
    which stays in the algebra while only the key constraint rides a predicate.
    `fields` names a record's declared keys, which its fields govern and its
    clauses do not.
    """
    declared = frozenset(fields)
    # `key in key_schema` is the operator form of `is_valid`, so the check reads
    # as the set test it is.
    return satisfies(
        lambda mapping: all(key in declared or key in key_schema for key in mapping)
    )


def open_record(fields):
    """A record admitting undeclared keys, its nested records left closed.

    `Validator.open()` opens every record in a schema, nested ones included.
    Where only this record may take extra keys, the catch-all clause below frees
    exactly its own undeclared keys, and the declared fields keep precedence
    over it.
    """
    return Validator({**fields, anything: anything})


def close_to(x):
    """The numbers `math.isclose` calls close to `x`.

    A float literal is a typed singleton, so it demands bit equality. Use this
    instead only where the stored value is computed by an expression other than
    the one written here, which need not produce the same bits.
    """
    return satisfies(lambda value: math.isclose(value, x), base=number)


# Leaf schemas.

uint = Validator(Annotated[int, at.Ge(0)])
suint = Validator(Annotated[int, at.Gt(0)])
unumber = Validator(Annotated[number, at.Ge(0)])
sunumber = Validator(Annotated[number, at.Gt(0)])
even_uint = Validator(Annotated[int, at.Ge(0), at.MultipleOf(2)])

task_id = uint
timestamp = unumber


def is_unique(values):
    return len(set(values)) == len(values)


def is_utc(value):
    return value.tzinfo == UTC


def is_gzip_data(value):
    return value[:2] == b"\x1f\x8b"


def is_ip_address(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def is_email(value):
    # Deliverability needs DNS; the registration form checks that separately.
    validate_email(value, check_deliverability=False)
    return True


def is_regex_pattern(value):
    re.compile(value)
    return True


datetime_utc = Validator(satisfies(is_utc, base=datetime))
gzip_data = Validator(satisfies(is_gzip_data, base=bytes))
ip_address = Validator(satisfies(is_ip_address, base=str))
email = Validator(satisfies(is_email, base=str))
regex_pattern = Validator(satisfies(is_regex_pattern, base=str))

run_id = Validator(satisfies(ObjectId.is_valid, base=str))
run_id_pgns = Validator(Annotated[str, Regex(r"[a-f0-9]{24}-(0|[1-9]\d*)")])
run_name = Validator(Annotated[str, Regex(r".*-[a-f0-9]{7}"), at.MaxLen(23 + 1 + 7)])

ACTION_MESSAGE_SIZE = 5120
action_message = Validator(Annotated[str, at.MaxLen(ACTION_MESSAGE_SIZE)])
worker_message = Validator(Annotated[str, at.MaxLen(500)])

short_worker_name = Validator(Annotated[str, Regex(r".*-[\d]+cores-[a-zA-Z0-9]{2,8}")])
long_worker_name = Validator(
    Annotated[str, Regex(r".*-[\d]+cores-[a-zA-Z0-9]{2,8}-[a-f0-9]{4}\*?")]
)
worker_arch = union(*supported_arches)
compiler = union(*supported_compilers)

valid_username = Validator(Annotated[str, Regex(VALID_USERNAME_PATTERN)])
legacy_usernames = set()  # will be updated when the application starts up
# The predicate reads the module global, so the startup update is picked up.
legacy_username = Validator(
    satisfies(lambda value: value in legacy_usernames, base=str)
)
username = union(valid_username, legacy_username)
action_username = union(username, "fishtest.system")

net_name = Validator(Annotated[str, Regex(r"nn-[a-f0-9]{12}.nnue")])
tc = Validator(Annotated[str, Regex(r"([1-9]\d*/)?\d+(\.\d+)?(\+\d+(\.\d+)?)?")])
str_int = Validator(Annotated[str, Regex(r"[1-9]\d*")])
sha = Validator(Annotated[str, Regex(r"[a-f0-9]{40}")])
sri384 = Validator(Annotated[str, Regex(r"(sha384-)?[0-9A-Za-z+/]{64}")])
uuid = Validator(
    Annotated[str, Regex(r"[0-9a-zA-Z]{2,8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}")]
)
country_code = Validator(Annotated[str, Regex(r"[A-Z][A-Z]")])

# `(?s)` because a glob matched a newline in a name where an unflagged `.` does not.
epd_file = Validator(Annotated[str, Regex(r"(?s).*\.epd")])
pgn_file = Validator(Annotated[str, Regex(r"(?s).*\.pgn")])
book = union(epd_file, pgn_file)

residual_color = Validator(Literal["green", "yellow", "red"])

# Accept trailing slash only at raw-input boundaries. Persisted documents use
# the slash-free canonical form.
github_repo_input = Validator(
    Annotated[
        str,
        Regex(r"https:\/\/(www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/?"),
    ]
)
github_repo = Validator(
    Annotated[
        str, Regex(r"https:\/\/(www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+")
    ]
)
tests_repo_input = union(github_repo_input, "")
# The machines view addresses one worker or the whole list.
worker_name_or_show = union(short_worker_name, "show")

set_option = r"[^\s=]+=[^\s=]+"
option_list = Validator(
    Annotated[
        str,
        at.Predicate(str.isascii),
        Regex(rf"(|({set_option} )*{set_option})"),
    ]
)


def size_is_length(pgn_doc):
    return pgn_doc["size"] == len(pgn_doc["pgn_zip"])


pgns_schema = Validator(
    satisfies(
        size_is_length,
        base={
            "_id?": ObjectId,
            "run_id": run_id_pgns,
            "pgn_zip": gzip_data,
            "size": uint,
        },
    )
)

user_schema = Validator(
    {
        "_id?": ObjectId,
        "username": username,
        "password": Annotated[str, at.MaxLen(PASSWORD_MAX_LENGTH)],
        "registration_time": datetime_utc,
        "pending": bool,
        "blocked": bool,
        "email": email,
        "groups": Annotated[list[str], at.Predicate(is_unique)],
        "tests_repo": union(github_repo, ""),
        "machine_limit": uint,
    }
)

kvstore_schema = Validator(
    {
        "_id": str,
        "value": anything,
    }
)

worker_schema = Validator(
    {
        "_id?": ObjectId,
        "worker_name": short_worker_name,
        "blocked": bool,
        "message": worker_message,
        "last_updated": datetime_utc,
    }
)


def first_test_before_last(net_doc):
    first = net_doc["first_test"]["date"]
    last = net_doc["last_test"]["date"]
    if first <= last:
        return True
    else:
        raise Exception(
            f"The first test at {str(first)} is later than the last test at {str(last)}"
        )


nn_schema = intersection(
    {
        "_id?": ObjectId,
        "downloads": uint,
        "first_test?": {"date": datetime_utc, "id": run_id},
        "is_master?": True,
        "last_test?": {"date": datetime_utc, "id": run_id},
        "name": net_name,
        "user": username,
    },
    implies(
        at_least_one_of("is_master", "first_test", "last_test"),
        intersection(
            has("first_test", "last_test"),
            satisfies(first_test_before_last),
        ),
    ),
)

# not yet used, not tested
contributors_schema = Validator(
    {
        "_id": ObjectId,
        "cpu_hours": unumber,
        "diff": unumber,
        "games": uint,
        "games_per_hour": unumber,
        "last_updated": datetime_utc,
        "str_last_updated": str,
        "tests": uint,
        "tests_repo": union(github_repo, ""),
        "username": username,
    }
)


action_name = Validator(
    Literal[
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
    ]
)

# Every branch below pins its own "action" literal, so the branches are pairwise
# disjoint and their union is the tagged union of the action documents: a value
# can satisfy at most the branch its own action names. The recognized action
# names are asserted beside it, so an unknown action fails there rather than
# against every branch.
action_schema = intersection(
    open_record({"action": action_name}),
    union(
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "failed_task",
            "username": action_username,
            "worker": long_worker_name,
            "run_id": run_id,
            "run": run_name,
            "task_id": task_id,
            "message": action_message,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "crash_or_time",
            "username": action_username,
            "worker": long_worker_name,
            "run_id": run_id,
            "run": run_name,
            "task_id": task_id,
            "message": action_message,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "dead_task",
            "username": action_username,
            "worker": long_worker_name,
            "run_id": run_id,
            "run": run_name,
            "task_id": task_id,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "system_event",
            "username": "fishtest.system",
            "message": action_message,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "new_run",
            "username": action_username,
            "run_id": run_id,
            "run": run_name,
            "message": action_message,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "upload_nn",
            "username": action_username,
            "nn": net_name,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "modify_run",
            "username": action_username,
            "run_id": run_id,
            "run": run_name,
            "message": action_message,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "delete_run",
            "username": action_username,
            "run_id": run_id,
            "run": run_name,
        },
        intersection(
            {
                "_id": ObjectId,
                "time": timestamp,
                "action": "stop_run",
                "username": action_username,
                "run_id": run_id,
                "run": run_name,
                "message": action_message,
                "worker?": long_worker_name,
                "task_id?": task_id,
            },
            implies(at_least_one_of("worker", "task_id"), has("worker", "task_id")),
        ),
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "finished_run",
            "username": action_username,
            "run_id": run_id,
            "run": run_name,
            "message": action_message,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "approve_run",
            "username": action_username,
            "run_id": run_id,
            "run": run_name,
            "message": Literal["approved", "unapproved"],
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "purge_run",
            "username": action_username,
            "run_id": run_id,
            "run": run_name,
            "message": action_message,
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "block_user",
            "username": action_username,
            "user": str,
            "message": Literal["blocked", "unblocked"],
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "accept_user",
            "username": action_username,
            "user": str,
            "message": "accepted",
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "block_worker",
            "username": action_username,
            "worker": short_worker_name,
            "message": Literal["blocked", "unblocked"],
        },
        {
            "_id": ObjectId,
            "time": timestamp,
            "action": "log_message",
            "username": action_username,
            "worker?": long_worker_name,
            "message": action_message,
        },
        intersection(
            {
                "_id": ObjectId,
                "time": timestamp,
                "action": "worker_log",
                "username": action_username,
                "worker": long_worker_name,
                "message": action_message,
                "run_id?": run_id,
                "run?": run_name,
                "task_id?": task_id,
            },
            implies(
                at_least_one_of("run_id", "run", "task_id"),
                has("run_id", "run"),
            ),
        ),
    ),
)


worker_info_fields_api = {
    "uname": str,
    "architecture": [str, str],
    "concurrency": suint,
    "max_memory": uint,
    "min_threads": suint,
    "username": username,
    "version": uint,
    "python_version": [uint, uint, uint],
    "gcc_version": [uint, uint, uint],
    "compiler": compiler,
    "unique_key": uuid,
    "modified": bool,
    "worker_arch": union(worker_arch, "unknown"),
    "ARCH": str,
    "nps": unumber,
    "near_github_api_limit": bool,
}

worker_info_schema_api = Validator(worker_info_fields_api)

worker_info_schema_runs = Validator(
    {
        **worker_info_fields_api,
        "remote_addr": ip_address,
        "country_code": union(country_code, "?"),
    }
)


def valid_results(stats):
    losses, draws, wins = stats["losses"], stats["draws"], stats["wins"]
    pentas = stats["pentanomial"]
    return (
        losses + draws + wins == 2 * sum(pentas)
        and wins - losses == 2 * pentas[4] + pentas[3] - pentas[1] - 2 * pentas[0]
        and pentas[3] + 2 * pentas[2] + pentas[1] >= draws >= pentas[3] + pentas[1]
    )


results_schema = Validator(
    satisfies(
        valid_results,
        base={
            "wins": uint,
            "losses": uint,
            "draws": uint,
            "crashes": uint,
            "time_losses": uint,
            "pentanomial": [uint, uint, uint, uint, uint],
        },
    )
)


def valid_spsa_results(stats):
    return stats["wins"] + stats["losses"] + stats["draws"] == stats["num_games"]


api_access_schema = Validator(
    {"password": str, "worker_info": {"username": username}}
).open()

api_schema = intersection(
    {
        "password": str,
        "run_id?": run_id,
        "task_id?": task_id,
        "pgn?": str,
        "message?": str,
        "worker_info": worker_info_schema_api,
        "spsa?": satisfies(
            valid_spsa_results,
            base={
                "wins": uint,
                "losses": uint,
                "draws": uint,
                "num_games": even_uint,
                "sig": uint,
            },
        ),
        "stats?": results_schema,
    },
    implies(has("task_id"), has("run_id")),
)


zero_results = {
    "wins": 0,
    "draws": 0,
    "losses": 0,
    "crashes": 0,
    "time_losses": 0,
    "pentanomial": 5 * [0],
}

# The same document written as a schema: every field is the literal it holds.
zero_results_schema = {
    "wins": 0,
    "draws": 0,
    "losses": 0,
    "crashes": 0,
    "time_losses": 0,
    "pentanomial": [0, 0, 0, 0, 0],
}


def compute_results(run):
    results = copy.deepcopy(zero_results)
    for task in run["tasks"]:
        stats = task["stats"]
        for key in stats:
            if key != "pentanomial":
                results[key] += stats[key]
            else:
                for idx, penta in enumerate(stats["pentanomial"]):
                    results[key][idx] += penta
    return results


def compute_cores(run):
    cores = 0
    for task in run["tasks"]:
        if task["active"]:
            cores += task["worker_info"]["concurrency"]
    return cores


def compute_workers(run):
    workers = 0
    for task in run["tasks"]:
        if task["active"]:
            workers += 1
    return workers


def compute_committed_games(run):
    committed_games = 0
    for task in run["tasks"]:
        if not task["active"]:
            if "stats" in task:
                stats = task["stats"]
                committed_games += stats["wins"] + stats["losses"] + stats["draws"]
        else:
            committed_games += task["num_games"]
    return committed_games


def compute_total_games(run):
    total_games = 0
    for task in run["tasks"]:
        total_games += task["num_games"]
    return total_games


def compute_flags(run):
    no_flags = {"is_green": False, "is_yellow": False}
    green_flag = {"is_green": True, "is_yellow": False}
    yellow_flag = {"is_green": False, "is_yellow": True}
    results = run["results"]
    WLD = [results["wins"], results["losses"], results["draws"]]
    if not run["finished"]:
        return no_flags
    if "spsa" in run["args"]:
        return no_flags
    state = ""
    if "sprt" in run["args"]:
        state = run["args"]["sprt"].get("state", "")
    else:
        _, _, los = fishtest.stats.stat_util.get_elo(results["pentanomial"])

        if los < 0.05:
            state = "rejected"
        elif los > 0.95:
            state = "accepted"

    if state == "accepted":
        return green_flag
    elif state == "rejected" and WLD[0] > WLD[1]:
        return yellow_flag
    else:
        # Stopped SPRT test
        return no_flags


def final_results_must_match(run):
    results = compute_results(run)
    if results != run["results"]:
        raise Exception(
            f"The final results {run['results']} do not match the computed results {results}"
        )

    return True


def cores_must_match(run):
    cores = compute_cores(run)
    if cores != run["cores"]:
        raise Exception(f"Cores from tasks: {cores}. Cores from run: {run['cores']}")

    return True


def workers_must_match(run):
    workers = compute_workers(run)
    if workers != run["workers"]:
        raise Exception(
            f"Workers mismatch. Workers from tasks: {workers}. Workers from "
            f"run: {run['workers']}"
        )

    return True


def committed_games_must_match(run):
    committed_games = compute_committed_games(run)
    if committed_games != run["committed_games"]:
        raise Exception(
            f"Committed games mismatch. Committed games from tasks: {committed_games}. Committed games from "
            f"run: {run['committed_games']}"
        )

    return True


def total_games_must_match(run):
    total_games = compute_total_games(run)
    if total_games != run["total_games"]:
        raise Exception(
            f"Total games mismatch. Total games from tasks: {total_games}. Total games from "
            f"run: {run['total_games']}"
        )

    return True


def flags_must_match(run):
    flags = compute_flags(run)
    run_flags = {"is_green": run["is_green"], "is_yellow": run["is_yellow"]}
    if flags != run_flags:
        raise Exception(
            f"Flags mismatch. Computed flags: {flags}. Flags from run: {run_flags}"
        )
    return True


def is_undecided(run):
    completed_games = (
        run["results"]["wins"] + run["results"]["losses"] + run["results"]["draws"]
    )
    if completed_games >= run["args"]["num_games"]:
        return False
    if (
        "sprt" in run["args"]
        and "state" in run["args"]["sprt"]
        and run["args"]["sprt"]["state"] != ""
    ):
        return False
    return True


valid_aggregated_data = intersection(
    satisfies(final_results_must_match),
    satisfies(cores_must_match),
    satisfies(workers_must_match),
    satisfies(committed_games_must_match),
    satisfies(total_games_must_match),
    satisfies(flags_must_match),
)

# The following schema only matches new runs. The old runs
# are not compatible with it. For documentation purposes
# it would also be useful to have a "universal schema"
# that matches all the runs in the db.

# Please increment this if the format of the run schema
# changes. This will suppress spurious event log messages
# about non-validation of runs created with the prior
# schema.

RUN_VERSION = 24

runs_schema = intersection(
    {
        "_id": ObjectId,
        "version": uint,
        "start_time": datetime_utc,
        "last_updated": datetime_utc,
        "tc_base": unumber,
        "rescheduled_from?": run_id,
        "approved": bool,
        "approver": union(username, ""),
        "finished": bool,
        "deleted": bool,
        "failed": bool,
        "failures": uint,
        "is_green": bool,
        "is_yellow": bool,
        "workers": uint,
        "cores": uint,
        "committed_games": uint,
        "total_games": uint,
        "results": results_schema,
        "nps": unumber,
        "games_per_minute": unumber,
        "args": intersection(
            {
                "base_tag": str,
                "new_tag": str,
                "base_nets": Annotated[list[net_name], at.Predicate(is_unique)],
                "new_nets": Annotated[list[net_name], at.Predicate(is_unique)],
                "num_games": even_uint,
                "tc": tc,
                "new_tc": tc,
                "book": book,
                "book_depth": str_int,
                "threads": suint,
                "resolved_base": sha,
                "resolved_new": sha,
                "msg_base": str,
                "msg_new": str,
                "base_options": option_list,
                "new_options": option_list,
                "info": str,
                "base_signature": str_int,
                "new_signature": str_int,
                "username": username,
                "tests_repo": github_repo,
                "master_repo?": github_repo,  # present only when non-standard (rare)
                "auto_purge": bool,
                "throughput": unumber,
                "itp": unumber,
                "priority": number,
                "adjudication": bool,
                "arch_filter?": regex_pattern,
                "compiler?": compiler,
                "sprt?": intersection(
                    {
                        "alpha": 0.05,
                        "beta": 0.05,
                        "elo0": number,
                        "elo1": number,
                        "elo_model": "normalized",
                        "state": Literal["", "accepted", "rejected"],
                        "llr": number,
                        "batch_size": suint,
                        # stat_util computes these as log(beta / (1 - alpha)) and
                        # log((1 - beta) / alpha), so do not demand bit equality.
                        "lower_bound": close_to(-math.log(19)),
                        "upper_bound": close_to(math.log(19)),
                        "lost_samples?": uint,
                        "illegal_update?": uint,
                        "overshoot?": {
                            "last_update": uint,
                            "skipped_updates": uint,
                            "ref0": number,
                            "m0": number,
                            "sq0": unumber,
                            "ref1": number,
                            "m1": number,
                            "sq1": unumber,
                        },
                    },
                    one_of("overshoot", "lost_samples"),
                ),
                "spsa?": {
                    "algorithm?": "classic",
                    "A": unumber,
                    "alpha": unumber,
                    "gamma": unumber,
                    "raw_params": str,
                    "iter": uint,
                    "num_iter": uint,
                    "params": [
                        {
                            "name": str,
                            "start": number,
                            "min": number,
                            "max": number,
                            "c_end": sunumber,
                            "r_end": unumber,
                            "c": sunumber,
                            "a_end": unumber,
                            "a": unumber,
                            "theta": number,
                        },
                        ...,
                    ],
                    "param_history?": [
                        [
                            {"theta": number, "R": unumber, "c": unumber},
                            ...,
                        ],
                        ...,
                    ],
                },
            },
            at_most_one_of("sprt", "spsa"),
        ),
        "tasks": [
            intersection(
                {
                    "num_games": even_uint,
                    "active": bool,
                    "last_updated": datetime_utc,
                    "start": uint,
                    "bad?": True,
                    "stats": results_schema,
                    "spsa_params?": {
                        "iter": uint,
                        "packed_flips": bytes,  # TODO: check length
                    },
                    "worker_info": worker_info_schema_runs,
                },
                implies(
                    has("bad"),
                    open_record({"active": False, "stats": zero_results_schema}),
                ),
                implies(has("spsa_params"), open_record({"active": True})),
            ),
            ...,
        ],
        "bad_tasks": [
            {
                "num_games": even_uint,
                "active": False,
                "last_updated": datetime_utc,
                "start": uint,
                "residual": number,
                "residual_color": residual_color,
                "bad": True,
                "task_id": task_id,
                "stats": results_schema,
                "worker_info": worker_info_schema_runs,
            },
            ...,
        ],
    },
    implies({"failed": True}, {"failures": suint}).open(),
    implies({"approved": True}, {"approver": username}, {"approver": ""}).open(),
    implies({"is_green": True}, {"is_yellow": False}).open(),
    implies({"is_yellow": True}, {"is_green": False}).open(),
    implies(
        {"finished": True},
        {
            "workers": 0,
            "cores": 0,
            "nps": 0.0,
            "games_per_minute": 0.0,
            "tasks": [{"active": False}, ...],
        },
        intersection(
            {
                "is_green": False,
                "is_yellow": False,
                "failed": False,
                "deleted": False,
            },
            satisfies(is_undecided),
        ),
    ).open(),
    valid_aggregated_data,
)

# The cache holds whole run documents; each one is validated where it is written
# and read back, so the cache is checked for its own shape only.
cache_entry_schema = Validator(
    {
        "run": dict,
        "is_changed": bool,  # Indicates if the run has changed since last_sync_time.
        "last_sync_time": timestamp,  # Last sync time (reading from or writing to db). If never synced then creation time.
        "last_access_time": timestamp,  # Last time the cache entry was touched (via buffer() or get_run()).
        "priority": int,  # Entries with higher priority are synced first.
    }
)

cache_schema = intersection(dict[str, cache_entry_schema], keys_in(run_id))

wtt_map_schema = intersection(
    dict[str, tuple[run_id, task_id]], keys_in(short_worker_name)
)

connections_counter_schema = intersection(dict[str, suint], keys_in(ip_address))

unfinished_runs_schema = Validator(set[run_id])

# A record with a typed catch-all: "last_run" names a run, every other key is
# itself a run id. The catch-all's key type is `str`, and `keys_in` narrows it to
# the run ids beside the record, exempting the one declared field.
worker_runs_entry_schema = intersection(
    {
        "last_run": run_id,
        str: True,
    },
    keys_in(run_id, "last_run"),
)

worker_runs_schema = intersection(
    dict[str, worker_runs_entry_schema], keys_in(short_worker_name)
)


def total_is_white_plus_black(book_doc):
    return book_doc["total"] == book_doc["white"] + book_doc["black"]


books_schema = intersection(
    dict[
        str,
        satisfies(
            total_is_white_plus_black,
            base={
                "total": uint,
                "white": uint,
                "black": uint,
                "min_depth": union(uint, None),
                "max_depth": union(uint, None),
                "sri": sri384,
            },
        ),
    ],
    keys_in(book),
)
