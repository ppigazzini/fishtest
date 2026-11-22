"""What each MCP tool reads: one function per tool over `RunDb`.

Each function takes the run database and the tool's arguments, already checked
against the tool's contract, and returns the structured result the contract
describes. The server runs them in the threadpool, as every blocking `rundb`
read runs. A run the arguments name and the database does not hold is a
`ToolRefusal`, which the server answers as a tool error, as `/api/get_run`
answers 404.

The arguments are JSON values: an integer argument may arrive as `1.0`, which
JSON Schema counts as an integer, so a page number is read with `int()`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import regex
from pymongo import DESCENDING
from pymongo.errors import ExecutionTimeout

from fishtest.api import ACTIONS_QUERY_TIME_MAX_MS
from fishtest.constants import supported_arches
from fishtest.mcp.contracts import (
    ACTION_FIELDS,
    ACTIONS_LIMIT_MAX,
    FINISHED_RUNS_PAGE_SIZE,
    TEXT_MAX,
    WORKERS_PAGE_SIZE,
)
from fishtest.rundb import ARCH_SEARCH_TIMEOUT
from fishtest.stats.stat_util import SPRT_elo
from fishtest.util import worker_name
from fishtest.views_helpers import sanitize_quotation_marks

# The largest skip MongoDB reads, a signed 64-bit integer: a page past it is
# past the last page, as any page past the last is.
SKIP_MAX = 2**63 - 1


class ToolRefusal(Exception):
    """A call its contract admits that names something the server does not hold."""


_RUN_ARGS = (
    "username",
    "new_tag",
    "base_tag",
    "tc",
    "new_tc",
    "threads",
    "book",
    "new_options",
    "base_options",
    "num_games",
    "info",
    "tests_repo",
    "resolved_new",
    "resolved_base",
    "arch_filter",
    "compiler",
    "priority",
    "throughput",
)
_RUN_FIELDS = (
    "approved",
    "finished",
    "deleted",
    "failed",
    "is_green",
    "is_yellow",
    "workers",
    "cores",
)
_RESULT_FIELDS = ("wins", "losses", "draws", "crashes", "time_losses")
_SPRT_FIELDS = (
    "elo0",
    "elo1",
    "elo_model",
    "state",
    "llr",
    "lower_bound",
    "upper_bound",
)
RUN_PROJECTION = {"tasks": 0, "bad_tasks": 0, "args.spsa.param_history": 0}


def _count(value: Any) -> int | None:
    # A count stored as a string of digits, as runs of 2013 hold it, is that
    # count; a negative one, as the worker counters an accounting drift left on
    # runs of 2024, is no count at all.
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _commit(value: Any) -> str | None:
    # A resolved commit stored as a one-element list, as runs of 2013 hold it,
    # is that commit.
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value if isinstance(value, str) else None


def _set(value: Any) -> str | None:
    # A worker restriction stored empty restricts nothing.
    return value if isinstance(value, str) and value else None


# How a stored field is read where runs written over the years differ.
_READERS = {
    "threads": _count,
    "num_games": _count,
    "workers": _count,
    "cores": _count,
    "resolved_new": _commit,
    "resolved_base": _commit,
    "arch_filter": _set,
    "compiler": _set,
}


def _published(document: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    # A field a reader cannot read is left out: an optional one, so the run is
    # published without it, or a required one, so the contract refuses the run.
    fields = {}
    for key in keys:
        if key in document:
            value = _READERS.get(key, lambda stored: stored)(document[key])
            if value is not None:
                fields[key] = value
    return fields


@lru_cache(maxsize=1000)
def admitted_arches(arch_filter: str) -> tuple[str, ...] | None:
    """Return the supported arches `arch_filter` admits, as the scheduler reads it.

    The scheduler searches a run's filter in a worker's arch and skips the
    run only where the search finds nothing, so an arch whose search runs past
    `ARCH_SEARCH_TIMEOUT` is admitted. None for a filter `regex` does not
    compile. Runs share a few filters, so each one is searched once.
    """
    try:
        pattern = regex.compile(arch_filter)
    except regex.error:
        return None
    admitted = []
    for arch in supported_arches:
        try:
            found = pattern.search(arch, timeout=ARCH_SEARCH_TIMEOUT) is not None
        except TimeoutError:
            found = True
        if found:
            admitted.append(arch)
    return tuple(admitted)


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Return what the `get_run` contract publishes of a run document."""
    args = run["args"]
    results = run["results"]
    summary = {
        "run_id": str(run["_id"]),
        **_published(args, _RUN_ARGS),
        "start_time": str(run["start_time"]),
        "last_updated": str(run["last_updated"]),
        **_published(run, _RUN_FIELDS),
        "results": {key: results[key] for key in _RESULT_FIELDS if key in results},
    }
    if "arch_filter" in summary:
        arches = admitted_arches(summary["arch_filter"])
        if arches is not None:
            summary["arches"] = list(arches)
    if "pentanomial" in results:
        summary["results"]["pentanomial"] = list(results["pentanomial"])
    if "sprt" in args:
        sprt = args["sprt"]
        summary["sprt"] = {key: sprt[key] for key in _SPRT_FIELDS if key in sprt}
    if "spsa" in args:
        spsa = args["spsa"]
        summary["spsa"] = {
            "iter": spsa["iter"],
            "num_iter": spsa["num_iter"],
            "params": len(spsa["params"]),
        }
    return summary


def _held_run(rundb: Any, run_id: str) -> dict[str, Any]:
    run = rundb.get_run(run_id)
    if run is None:
        msg = f"The run {run_id} does not exist"
        raise ToolRefusal(msg)
    return run


def get_run(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    return run_summary(_held_run(rundb, arguments["run_id"]))


def list_active_runs(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    runs = rundb.runs.find({"finished": False}, RUN_PROJECTION)
    return {"runs": [run_summary(run) for run in runs]}


def _moment(arguments: dict[str, Any], key: str) -> datetime | None:
    # A time the contract admits, read in UTC where it names no offset. The
    # pattern admits a day no month holds, such as 2026-02-30.
    if key not in arguments:
        return None
    try:
        moment = datetime.fromisoformat(arguments[key])
    except ValueError as e:
        msg = f"{key} names a day the calendar does not hold"
        raise ToolRefusal(msg) from e
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def list_finished_runs(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    page = int(arguments.get("page", 1))
    filters = {
        "username": arguments.get("username", ""),
        # Read as the finished-runs page reads its search box.
        "text": sanitize_quotation_marks(arguments.get("text", "")),
        "success_only": arguments.get("success_only", False),
        "yellow_only": arguments.get("yellow_only", False),
        "ltc_only": arguments.get("ltc_only", False),
        # A window on the time each index sorts the runs by.
        "last_updated": _moment(arguments, "since"),
        "until": _moment(arguments, "until"),
        "option": arguments.get("option", ""),
    }
    bound = {"max_time_ms": ACTIONS_QUERY_TIME_MAX_MS}
    try:
        arch_filters = None
        if "arch" in arguments:
            # Each filter the window's runs set is searched once, as the
            # scheduler searches it, and MongoDB pages the runs that set one
            # admitting the arch.
            stored = rundb.get_finished_runs_arch_filters(**filters, **bound)
            arch_filters = [
                arch_filter
                for arch_filter in stored
                if arguments["arch"] in (admitted_arches(arch_filter) or ())
            ]
            if not arch_filters:
                return {"page": page, "total": 0, "runs": []}
        runs, total = rundb.get_finished_runs(
            skip=min((page - 1) * FINISHED_RUNS_PAGE_SIZE, SKIP_MAX),
            limit=FINISHED_RUNS_PAGE_SIZE,
            arch_filters=arch_filters,
            **filters,
            **bound,
        )
    except ExecutionTimeout as e:
        raise ToolRefusal(_past_time_bound(arguments)) from e
    return {"page": page, "total": total, "runs": [run_summary(run) for run in runs]}


# The window a listing past its time bound names. An arch, option or text
# filter reads every run the window holds, and on DEV's 170,000 runs a month
# of them reads within the bound with any of the three.
RETRY_WINDOW = timedelta(days=30)


def _past_time_bound(arguments: dict[str, Any]) -> str:
    # A window to retry with, and how to read the runs before it: a client
    # retries a refusal that names its next call.
    bound = f"The listing took longer than {ACTIONS_QUERY_TIME_MAX_MS} ms"
    if "since" in arguments:
        return f"{bound}; a later since or an earlier until narrows the runs it reads"
    since = (datetime.now(UTC) - RETRY_WINDOW).date().isoformat()
    return (
        f"{bound}; since and until bound the runs it reads: retry with"
        f" since={since}, a month back, and read the month before it with"
        f" until={since}"
    )


def _version(value: Any) -> str | None:
    # A version stored as a list of numbers, written with dots.
    if isinstance(value, list) and value and all(isinstance(n, int) for n in value):
        return ".".join(map(str, value))
    return None


def worker_row(machine: dict[str, Any]) -> dict[str, Any]:
    """Return what `list_active_workers` publishes of a worker playing a task.

    `machine` is a row of `RunDb.get_machines`: the worker's info, the task's
    run, index and last update. A field left unset is left out.
    """
    last_updated = machine["last_updated"]
    row = {
        "name": worker_name(machine, short=True),
        "username": machine["username"],
        "country_code": machine.get("country_code"),
        "cores": machine["concurrency"],
        "nps": machine.get("nps"),
        "max_memory": machine.get("max_memory"),
        "system": machine.get("uname"),
        "arch": machine.get("worker_arch"),
        "compiler": machine.get("compiler"),
        "compiler_version": _version(machine.get("gcc_version")),
        "python_version": _version(machine.get("python_version")),
        "version": machine.get("version"),
        "modified": machine.get("modified"),
        "run_id": str(machine["run"]["_id"]),
        "task_id": machine["task_id"],
        "last_updated": None if last_updated is None else str(last_updated),
    }
    return {key: value for key, value in row.items() if value is not None}


def list_active_workers(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    # The machines page's own read: on a secondary instance, every unfinished
    # run's tasks, cached for a few seconds, so the page and the calls made
    # within those seconds share one query.
    machines = sorted(
        rundb.get_machines(), key=lambda m: (str(m["run"]["_id"]), m["task_id"])
    )
    # The most recently updated first, a task never updated last.
    machines.sort(
        key=lambda m: (m["last_updated"] is not None, m["last_updated"] or 0),
        reverse=True,
    )
    rows = [worker_row(machine) for machine in machines]
    for key in ("username", "arch", "run_id"):
        if key in arguments:
            rows = [row for row in rows if row.get(key) == arguments[key]]
    page = int(arguments.get("page", 1))
    start = (page - 1) * WORKERS_PAGE_SIZE
    return {
        "page": page,
        "total": len(rows),
        "cores": sum(row["cores"] for row in rows),
        "workers": rows[start : start + WORKERS_PAGE_SIZE],
    }


def get_elo(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments["run_id"]
    run = _held_run(rundb, run_id)
    if "sprt" not in run["args"]:
        msg = f"The run {run_id} is not an SPRT test"
        raise ToolRefusal(msg)
    return elo_estimate(run)


def elo_estimate(run: dict[str, Any]) -> dict[str, Any]:
    """Return what the `get_elo` contract publishes of an SPRT run."""
    sprt = run["args"]["sprt"]
    elo_model = sprt.get("elo_model", "BayesElo")
    try:
        estimate = SPRT_elo(
            run["results"],
            alpha=sprt["alpha"],
            beta=sprt["beta"],
            elo0=sprt["elo0"],
            elo1=sprt["elo1"],
            elo_model=elo_model,
        )
    except ArithmeticError as e:
        # Bounds the estimator cannot read, such as an elo0 above elo1 on a run
        # of 2014, drive it past every float.
        msg = (
            f"The Elo estimate of the run {run['_id']} cannot be computed from its"
            f" bounds, elo0 {sprt['elo0']} and elo1 {sprt['elo1']}"
        )
        raise ToolRefusal(msg) from e
    return {
        "run_id": str(run["_id"]),
        "elo_model": elo_model,
        "elo0": sprt["elo0"],
        "elo1": sprt["elo1"],
        "LLR": float(estimate["LLR"]),
        "lower_bound": float(estimate["a"]),
        "upper_bound": float(estimate["b"]),
        "elo": float(estimate["elo"]),
        "ci": [float(bound) for bound in estimate["ci"]],
        "LOS": float(estimate["LOS"]),
    }


def _action(action: dict[str, Any]) -> dict[str, Any]:
    # An event as the `search_actions` contract publishes it: the fields it
    # names and the document holds, the id rendered. A field the document does
    # not hold is left out, and one it holds and the contract does not name is
    # dropped here rather than passed through.
    event = {key: action[key] for key in ACTION_FIELDS if key in action}
    event["_id"] = str(action["_id"])
    return event


def search_actions(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    # The filter the contract admits is one `action_query_schema` accepts: the
    # two are proven equal, so the database is handed only what `/api/actions`
    # would hand it, and a text search as the event log page hands it.
    query = arguments["filter"]
    if "text" in arguments:
        text = sanitize_quotation_marks(arguments["text"])
        query = {**query, "$text": {"$search": text}}
    limit = int(arguments.get("limit", ACTIONS_LIMIT_MAX))
    actions = (
        rundb.db["actions"]
        .find(query, max_time_ms=ACTIONS_QUERY_TIME_MAX_MS)
        .sort([("time", DESCENDING), ("_id", DESCENDING)])
        .limit(limit)
    )
    try:
        # Build each event from the fields the contract names, not from the
        # stored document: a field nobody published cannot leave through it.
        found = [_action(action) for action in actions]
    except ExecutionTimeout as e:
        msg = (
            f"The filter took longer than {ACTIONS_QUERY_TIME_MAX_MS} ms; a filter"
            " on action, username, run_id or time, and a text search, are read"
            " from an index"
        )
        raise ToolRefusal(msg) from e
    return {"actions": found}


# A character no reader sees but a model reads: the C0 and C1 controls and the
# invisible format characters, Unicode's Cc and Cf, such as bidirectional
# overrides and the tag characters that spell text out of sight. A tab, a line
# feed and the two joiners that shape scripts and emoji stay.
UNSEEN = regex.compile(r"[[\p{Cc}\p{Cf}]--[\t\n\u200c\u200d]]", regex.V1)


def plain(value: Any) -> Any:
    """Return a result with every string cleared of what no reader sees.

    The server publishes each result through this: a string longer than
    `TEXT_MAX` is cut, with a visible mark.
    """
    if isinstance(value, str):
        text = UNSEEN.sub("", value)
        return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 3] + "..."
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


TOOL_FUNCTIONS = {
    "get_run": get_run,
    "list_active_runs": list_active_runs,
    "list_finished_runs": list_finished_runs,
    "list_active_workers": list_active_workers,
    "get_elo": get_elo,
    "search_actions": search_actions,
}
