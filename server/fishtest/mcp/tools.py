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

import unicodedata
from typing import Any

from pymongo import DESCENDING
from pymongo.errors import ExecutionTimeout

from fishtest.api import ACTIONS_QUERY_TIME_MAX_MS
from fishtest.mcp.contracts import (
    ACTIONS_LIMIT_MAX,
    FINISHED_RUNS_PAGE_SIZE,
    TEXT_MAX,
)
from fishtest.stats.stat_util import SPRT_elo
from fishtest.views_helpers import sanitize_quotation_marks


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


def list_finished_runs(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    page = int(arguments["page"])
    runs, total = rundb.get_finished_runs(
        skip=(page - 1) * FINISHED_RUNS_PAGE_SIZE,
        limit=FINISHED_RUNS_PAGE_SIZE,
        username=arguments.get("username", ""),
        # Read as the finished-runs page reads its search box.
        text=sanitize_quotation_marks(arguments.get("text", "")),
        success_only=arguments.get("success_only", False),
        yellow_only=arguments.get("yellow_only", False),
        ltc_only=arguments.get("ltc_only", False),
    )
    return {"page": page, "total": total, "runs": [run_summary(run) for run in runs]}


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
        found = [{**action, "_id": str(action["_id"])} for action in actions]
    except ExecutionTimeout as e:
        msg = (
            f"The filter took longer than {ACTIONS_QUERY_TIME_MAX_MS} ms; a filter"
            " on action, username, run_id or time, and a text search, are read"
            " from an index"
        )
        raise ToolRefusal(msg) from e
    return {"actions": found}


# A character no reader sees but a model reads: the C0 and C1 controls and the
# invisible format characters, such as bidirectional overrides and the tag
# characters that spell text out of sight. A tab, a line feed and the two
# joiners that shape scripts and emoji stay.
_KEPT = frozenset("\t\n\u200c\u200d")
_UNSEEN = frozenset(("Cc", "Cf"))


def plain(value: Any) -> Any:
    """Return a result with every string cleared of what no reader sees.

    The server publishes each result through this: a string longer than
    `TEXT_MAX` is cut, with a visible mark.
    """
    if isinstance(value, str):
        text = "".join(
            c for c in value if c in _KEPT or unicodedata.category(c) not in _UNSEEN
        )
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
    "get_elo": get_elo,
    "search_actions": search_actions,
}
