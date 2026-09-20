# This file describes some of the data structures used by Fishtest so that they
# can be statically validated before they are processed further or written
# to the database.
#
# A schema denotes a *set* of Python values and validation is membership: the
# document is checked as it stands, never copied or coerced. Standard typing
# annotations are the notation — `Literal`, `list[T]`, `dict[K, V]`, `X | Y` —
# and `Annotated[T, ...]` narrows a base with the annotated-types markers. The
# native list and record literals carry the two shapes typing has no syntax for:
# a fixed-length list, and a record whose optional keys end in `?`. `union`,
# `intersection` and `complement` compose all of it into a Boolean lattice, and
# the recipes below are the standard compositions over that lattice.
#
# See https://ppigazzini.github.io/valgebra/ for the schema language, the
# algebra, and the error model.
#
# Every schema here is compiled once, at import, into a Validator; a boundary
# calls `.validate(document)` on it. A `Predicate` is the one constraint that
# leaves Rust for Python, so it appears only where no marker denotes the set —
# a mapping's cross-field arithmetic, a mapping's key shape, an address, a
# mailbox, a signature.

import copy
import ipaddress
import math
import re
from datetime import UTC, datetime
from itertools import combinations
from typing import Annotated, Any, Literal

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

# Compositions over the lattice.


def implies(condition, then, otherwise=anything):
    """A value in `condition` must also be in `then`, else in `otherwise`.

    The union of two intersections: a value either matches the condition and
    must then satisfy the consequent, or fails the condition and must satisfy
    the alternative.
    """
    return union(
        intersection(condition, then),
        intersection(complement(condition), otherwise),
    )


def has(*names):
    """A mapping carrying every one of `names`, whatever the values are.

    An open record requiring a key asserts only that the key is there, so a
    conjunction of them is the presence test.
    """
    return intersection(*(Validator({name: anything}).open() for name in names))


def at_least_one_of(*names):
    """A mapping carrying at least one of `names`."""
    return union(*(has(name) for name in names))


def at_most_one_of(*names):
    """A mapping carrying at most one of `names`.

    The complement of "some two of them are both present". Meeting it with
    `dict` keeps the schema a statement about mappings: a complement on its own
    admits every value the inner one rejects, a non-mapping included.
    """
    return intersection(
        dict,
        complement(union(*(has(a, b) for a, b in combinations(names, 2)))),
    )


def one_of(*names):
    """A mapping carrying exactly one of `names`."""
    return intersection(at_least_one_of(*names), at_most_one_of(*names))


def keys_in(key_schema, *fields):
    """A mapping whose every undeclared key belongs to `key_schema`.

    A clause's key names a whole *type*, so a key narrowed by a constraint is
    refused where it is written. The keys are checked beside the mapping
    instead: meet this with the mapping's own shape, which stays in the algebra
    while the key constraint rides a predicate that no relation reads. `fields`
    names the record's declared keys, which its fields govern and its clauses
    do not.
    """
    declared = frozenset(fields)
    return Validator(
        Annotated[
            dict,
            at.Predicate(
                lambda mapping: all(
                    key in declared or key_schema.is_valid(key) for key in mapping
                )
            ),
        ]
    )


def open_record(fields):
    """A record admitting undeclared keys, its nested records left closed.

    `Validator.open()` opens every record in a schema, nested ones included.
    Where only this record may take extra keys, a catch-all clause over every
    key frees exactly its own undeclared ones, and the declared fields keep
    precedence over it.
    """
    return Validator({**fields, anything: anything})


# Scalars.

uint = Validator(Annotated[int, at.Ge(0)])
suint = Validator(Annotated[int, at.Gt(0)])
ufloat = Validator(Annotated[float, at.Ge(0.0)])
sfloat = Validator(Annotated[float, at.Gt(0.0)])
# A match is played in pairs, so a game count is even.
game_count = Validator(Annotated[int, at.Ge(0), at.MultipleOf(2)])

task_id = uint
timestamp = ufloat


def is_utc(value):
    return value.tzinfo == UTC


def is_unique(values):
    return len(set(values)) == len(values)


def is_gzip_data(value):
    # The gzip signature, which is what a mime-type probe reads for it too.
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


datetime_utc = Validator(Annotated[datetime, at.Predicate(is_utc)])
gzip_data = Validator(Annotated[bytes, at.Predicate(is_gzip_data)])
ip_address = Validator(Annotated[str, at.Predicate(is_ip_address)])
email = Validator(Annotated[str, at.Predicate(is_email)])
regex_pattern = Validator(Annotated[str, at.Predicate(is_regex_pattern)])

# `Regex` matches the whole string, as `re.fullmatch` does, and runs on the
# Rust engine: `\d`, `\w` and `\s` are Unicode-aware there exactly as in `re`,
# so a digit class is written `[0-9]` wherever the value is handed to `int()` or
# `float()` afterwards. Arabic-Indic digits are decimal digits to both engines
# and to Python's own parsers, which is not what any pattern here means.
#
# A Mongo ObjectId renders as 24 lowercase hex digits.
run_id = Validator(Annotated[str, Regex(r"[a-f0-9]{24}")])
run_id_pgns = Validator(Annotated[str, Regex(r"[a-f0-9]{24}-(0|[1-9][0-9]*)")])
run_name = Validator(Annotated[str, Regex(r".*-[a-f0-9]{7}"), at.MaxLen(23 + 1 + 7)])

ACTION_MESSAGE_SIZE = 5120
action_message = Validator(Annotated[str, at.MaxLen(ACTION_MESSAGE_SIZE)])
worker_message = Validator(Annotated[str, at.MaxLen(500)])

short_worker_name = Validator(Annotated[str, Regex(r".*-[0-9]+cores-[a-zA-Z0-9]{2,8}")])
long_worker_name = Validator(
    Annotated[str, Regex(r".*-[0-9]+cores-[a-zA-Z0-9]{2,8}-[a-f0-9]{4}\*?")]
)
worker_arch = Validator(Literal[*supported_arches])
compiler = Validator(Literal[*supported_compilers])

valid_username = Validator(Annotated[str, Regex(VALID_USERNAME_PATTERN)])
legacy_usernames = set()  # will be updated when the application starts up
# What the application loads into the set above, read from the kvstore.
legacy_usernames_schema = Validator(list[str])
# The predicate reads the module global, so the startup update is picked up.
legacy_username = Validator(
    Annotated[str, at.Predicate(lambda value: value in legacy_usernames)]
)
username = valid_username | legacy_username
action_username = username | Literal["fishtest.system"]

net_name = Validator(Annotated[str, Regex(r"nn-[a-f0-9]{12}\.nnue")])
tc = Validator(
    Annotated[str, Regex(r"([1-9][0-9]*/)?[0-9]+(\.[0-9]+)?(\+[0-9]+(\.[0-9]+)?)?")]
)
str_int = Validator(Annotated[str, Regex(r"[1-9][0-9]*")])
sha = Validator(Annotated[str, Regex(r"[a-f0-9]{40}")])
sri384 = Validator(Annotated[str, Regex(r"(sha384-)?[0-9A-Za-z+/]{64}")])
uuid = Validator(
    Annotated[str, Regex(r"[0-9a-zA-Z]{2,8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}")]
)
country_code = Validator(Annotated[str, Regex(r"[A-Z][A-Z]")])

epd_file = Validator(Annotated[str, Regex(r".*\.epd")])
pgn_file = Validator(Annotated[str, Regex(r".*\.pgn")])
book = epd_file | pgn_file

residual_color = Validator(Literal["green", "yellow", "red"])

# Accept trailing slash only at raw-input boundaries. Persisted documents use
# the slash-free canonical form.
github_repo_input = Validator(
    Annotated[
        str,
        Regex(r"https://(www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?"),
    ]
)
github_repo = Validator(
    Annotated[
        str, Regex(r"https://(www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
    ]
)
tests_repo = github_repo | Literal[""]
tests_repo_input = github_repo_input | Literal[""]
# A UNIX timestamp as a query parameter carries seconds, optionally fractional.
unix_timestamp_param = Validator(Annotated[str, Regex(r"[0-9]{10}(\.[0-9]+)?")])
# The machines view addresses one worker or the whole list.
worker_name_or_show = short_worker_name | Literal["show"]

# An engine option is `Name=Value`, space separated: printable ASCII with no
# space and no second `=`.
OPTION_CHAR = r"[\x21-\x3c\x3e-\x7e]"
SET_OPTION = rf"{OPTION_CHAR}+={OPTION_CHAR}+"
option_list = Validator(Annotated[str, Regex(rf"(|({SET_OPTION} )*{SET_OPTION})")])


def size_is_length(pgn_doc):
    return pgn_doc["size"] == len(pgn_doc["pgn_zip"])


# The stored size is the length of the blob it describes.
size_matches_blob = Validator(Annotated[dict, at.Predicate(size_is_length)])

pgns_schema = intersection(
    {
        "_id?": ObjectId,
        "run_id": run_id_pgns,
        "pgn_zip": gzip_data,
        "size": uint,
    },
    size_matches_blob,
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
        "tests_repo": tests_repo,
        "machine_limit": uint,
    }
)

kvstore_schema = Validator(
    {
        "_id": str,
        # `Any`, not `anything`: the value is whatever the caller stores, and
        # the entries this module does describe carry their own schema.
        "value": Any,
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


net_test = Validator({"date": datetime_utc, "id": run_id})

# A net's first test cannot postdate its last.
tests_in_order = Validator(Annotated[dict, at.Predicate(first_test_before_last)])

nn_schema = intersection(
    {
        "_id?": ObjectId,
        "downloads": uint,
        "first_test?": net_test,
        "is_master?": True,
        "last_test?": net_test,
        "name": net_name,
        "user": username,
    },
    # A net that has been tested at all carries both ends of the interval.
    implies(
        at_least_one_of("is_master", "first_test", "last_test"),
        intersection(
            has("first_test", "last_test"),
            tests_in_order,
        ),
    ),
)

# not yet used, not tested
contributors_schema = Validator(
    {
        "_id": ObjectId,
        "cpu_hours": ufloat,
        "diff": ufloat,
        "games": uint,
        "games_per_hour": ufloat,
        "last_updated": datetime_utc,
        "str_last_updated": str,
        "tests": uint,
        "tests_repo": tests_repo,
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

# The action documents are a tagged union: every branch pins its own "action"
# literal, so the branches are pairwise disjoint and a document can satisfy at
# most the one its own action names. The recognized names are asserted beside
# the union, so an unknown action fails there rather than against every branch.
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
            # A stop attributed to a task names the worker that ran it.
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
            # A log attributed to a run names it both ways.
            implies(at_least_one_of("run_id", "run", "task_id"), has("run_id", "run")),
        ),
    ),
)


# A fixed-length list is the native literal: typing has no syntax for it.
worker_info_fields = {
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
    "worker_arch": worker_arch | Literal["unknown"],
    "ARCH": str,
    "nps": ufloat,
    "near_github_api_limit": bool,
}

# The worker states its own identity; the server adds what it observed.
worker_info_schema_api = Validator(worker_info_fields)

worker_info_schema_runs = Validator(
    {
        **worker_info_fields,
        "remote_addr": ip_address,
        "country_code": country_code | Literal["?"],
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


# The game counts and the pentanomial frequencies describe the same games.
results_add_up = Validator(Annotated[dict, at.Predicate(valid_results)])

results_schema = intersection(
    {
        "wins": uint,
        "losses": uint,
        "draws": uint,
        "crashes": uint,
        "time_losses": uint,
        "pentanomial": [uint, uint, uint, uint, uint],
    },
    results_add_up,
)


def valid_spsa_results(stats):
    return stats["wins"] + stats["losses"] + stats["draws"] == stats["num_games"]


# An SPSA batch reports every game it was given.
spsa_results_add_up = Validator(Annotated[dict, at.Predicate(valid_spsa_results)])

spsa_results_schema = intersection(
    {
        "wins": uint,
        "losses": uint,
        "draws": uint,
        "num_games": game_count,
        "sig": uint,
    },
    spsa_results_add_up,
)


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
        "spsa?": spsa_results_schema,
        "stats?": results_schema,
    },
    # A task is only addressable through the run that owns it.
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

# The same document as a schema: every field is the literal it holds.
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


def packed_flips_length_must_match(run):
    """An SPSA task packs one bit per tuned parameter, rounded up to bytes.

    The length is fixed by the run's own parameter list, so no schema on the
    task alone can state it. A blob of the wrong length unpacks to the wrong
    flips rather than to an error, which is why this is checked here.
    """
    params = run["args"].get("spsa", {}).get("params", [])
    expected = -(-len(params) // 8)
    for task_index, task in enumerate(run["tasks"]):
        if "spsa_params" not in task:
            continue
        packed = task["spsa_params"]["packed_flips"]
        if len(packed) != expected:
            raise Exception(
                f"Task {task_index} packs {len(packed)} bytes of SPSA flips, "
                f"but {len(params)} parameters need {expected}"
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


# The run document repeats what its tasks say; each of these decides one of
# those repetitions against the tasks themselves.
run_is_undecided = Validator(Annotated[dict, at.Predicate(is_undecided)])

valid_aggregated_data = intersection(
    Annotated[dict, at.Predicate(final_results_must_match)],
    Annotated[dict, at.Predicate(packed_flips_length_must_match)],
    Annotated[dict, at.Predicate(cores_must_match)],
    Annotated[dict, at.Predicate(workers_must_match)],
    Annotated[dict, at.Predicate(committed_games_must_match)],
    Annotated[dict, at.Predicate(total_games_must_match)],
    Annotated[dict, at.Predicate(flags_must_match)],
)

# The following schema only matches new runs. The old runs
# are not compatible with it. For documentation purposes
# it would also be useful to have a "universal schema"
# that matches all the runs in the db.

# Please increment this if the format of the run schema
# changes. This will suppress spurious event log messages
# about non-validation of runs created with the prior
# schema.

RUN_VERSION = 25

sprt_overshoot = Validator(
    {
        "last_update": uint,
        "skipped_updates": uint,
        "ref0": float,
        "m0": float,
        "sq0": ufloat,
        "ref1": float,
        "m1": float,
        "sq1": ufloat,
    }
)

sprt_schema = intersection(
    {
        "alpha": 0.05,
        "beta": 0.05,
        "elo0": float,
        "elo1": float,
        "elo_model": "normalized",
        "state": Literal["", "accepted", "rejected"],
        "llr": float,
        "batch_size": suint,
        # The bounds follow from alpha and beta, which this record pins.
        "lower_bound": -math.log(19),
        "upper_bound": math.log(19),
        "lost_samples?": uint,
        "illegal_update?": uint,
        "overshoot?": sprt_overshoot,
    },
    # Overshoot accounting and sample loss are the two ways a batch can be
    # reconciled, and a test uses one of them.
    one_of("overshoot", "lost_samples"),
)

spsa_param = Validator(
    {
        "name": str,
        "start": float,
        "min": float,
        "max": float,
        "c_end": sfloat,
        "r_end": ufloat,
        "c": sfloat,
        "a_end": ufloat,
        "a": ufloat,
        "theta": float,
    }
)

spsa_param_sample = Validator({"theta": float, "R": ufloat, "c": ufloat})

spsa_schema = Validator(
    {
        "algorithm?": "classic",
        "A": uint,  # an iteration offset, in game pairs
        "alpha": ufloat,
        "gamma": ufloat,
        "raw_params": str,
        "iter": uint,
        "num_iter": uint,
        "params": list[spsa_param],
        "param_history?": list[list[spsa_param_sample]],
    }
)

run_args_schema = intersection(
    {
        "base_tag": str,
        "new_tag": str,
        "base_nets": Annotated[list[net_name], at.Predicate(is_unique)],
        "new_nets": Annotated[list[net_name], at.Predicate(is_unique)],
        "num_games": game_count,
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
        "throughput": uint,  # a percentage
        "itp": ufloat,  # throughput after the scheduler's adjustments
        "priority": int,
        "adjudication": bool,
        "arch_filter?": regex_pattern,
        "compiler?": compiler,
        "sprt?": sprt_schema,
        "spsa?": spsa_schema,
    },
    # A test is either an SPRT or an SPSA tuning run, never both.
    at_most_one_of("sprt", "spsa"),
)

task_schema = intersection(
    {
        "num_games": game_count,
        "active": bool,
        "last_updated": datetime_utc,
        "start": uint,
        "bad?": True,
        "stats": results_schema,
        "spsa_params?": {
            "iter": uint,
            # One bit per tuned parameter; the run-level rule below decides
            # the length, which only the run's own parameter list fixes.
            "packed_flips": bytes,
        },
        "worker_info": worker_info_schema_runs,
    },
    # A task marked bad has been stopped and its games discounted.
    implies(
        has("bad"),
        open_record({"active": False, "stats": zero_results_schema}),
    ),
    # Only a running task holds the SPSA state it is playing with.
    implies(has("spsa_params"), open_record({"active": True})),
)

bad_task_schema = Validator(
    {
        "num_games": game_count,
        "active": False,
        "last_updated": datetime_utc,
        "start": uint,
        "residual": float,
        "residual_color": residual_color,
        "bad": True,
        "task_id": task_id,
        "stats": results_schema,
        "worker_info": worker_info_schema_runs,
    }
)

runs_schema = intersection(
    {
        "_id": ObjectId,
        "version": uint,
        "start_time": datetime_utc,
        "last_updated": datetime_utc,
        "tc_base": ufloat,
        "rescheduled_from?": run_id,
        "approved": bool,
        "approver": username | Literal[""],
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
        "nps": ufloat,
        "games_per_minute": ufloat,
        "args": run_args_schema,
        "tasks": list[task_schema],
        "bad_tasks": list[bad_task_schema],
    },
    # The conjuncts below constrain a few fields each, so each one is opened:
    # a field a conjunct does not name is the other conjuncts' business.
    implies({"failed": True}, {"failures": suint}).open(),
    implies({"approved": True}, {"approver": username}, {"approver": ""}).open(),
    implies({"is_green": True}, {"is_yellow": False}).open(),
    implies({"is_yellow": True}, {"is_green": False}).open(),
    implies(
        {"finished": True},
        # A finished run holds no worker and burns no cores.
        {
            "workers": 0,
            "cores": 0,
            "nps": 0.0,
            "games_per_minute": 0.0,
            "tasks": [{"active": False}, ...],
        },
        # An unfinished run has not reached a verdict.
        intersection(
            {
                "is_green": False,
                "is_yellow": False,
                "failed": False,
                "deleted": False,
            },
            run_is_undecided,
        ),
    ).open(),
    valid_aggregated_data,
)

cache_entry_schema = Validator(
    {
        # The run itself is validated where it is written and where it is read
        # back, so the cache is checked for its own shape only.
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

# A record with a typed catch-all: "last_run" names a run, and every other key
# is itself a run the worker has taken part in.
worker_runs_entry_schema = intersection(
    {"last_run": run_id, str: True}, keys_in(run_id, "last_run")
)

worker_runs_schema = intersection(
    dict[str, worker_runs_entry_schema], keys_in(short_worker_name)
)


def total_is_white_plus_black(book_doc):
    return book_doc["total"] == book_doc["white"] + book_doc["black"]


# Every position in the book opens with one side or the other.
book_totals_add_up = Validator(Annotated[dict, at.Predicate(total_is_white_plus_black)])

book_schema = intersection(
    {
        "total": uint,
        "white": uint,
        "black": uint,
        "min_depth": uint | None,
        "max_depth": uint | None,
        "sri": sri384,
    },
    book_totals_add_up,
)

books_schema = intersection(dict[str, book_schema], keys_in(book))
