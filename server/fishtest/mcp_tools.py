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

from typing import Any

from pymongo import DESCENDING

from fishtest.mcp_contracts import ACTIONS_LIMIT_MAX, FINISHED_RUNS_PAGE_SIZE
from fishtest.stats.stat_util import SPRT_elo


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
    "num_games",
    "info",
    "tests_repo",
    "resolved_new",
    "resolved_base",
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
_RUN_PROJECTION = {"tasks": 0, "bad_tasks": 0, "args.spsa.param_history": 0}


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Return what the `get_run` contract publishes of a run document."""
    args = run["args"]
    results = run["results"]
    summary = {
        "run_id": str(run["_id"]),
        **{key: args[key] for key in _RUN_ARGS if key in args},
        "start_time": str(run["start_time"]),
        "last_updated": str(run["last_updated"]),
        **{key: run[key] for key in _RUN_FIELDS if key in run},
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
    runs = rundb.runs.find({"finished": False}, _RUN_PROJECTION)
    return {"runs": [run_summary(run) for run in runs]}


def list_finished_runs(rundb: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    page = int(arguments["page"])
    runs, total = rundb.get_finished_runs(
        skip=(page - 1) * FINISHED_RUNS_PAGE_SIZE,
        limit=FINISHED_RUNS_PAGE_SIZE,
        username=arguments.get("username", ""),
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
    sprt = run["args"]["sprt"]
    elo_model = sprt.get("elo_model", "BayesElo")
    estimate = SPRT_elo(
        run["results"],
        alpha=sprt["alpha"],
        beta=sprt["beta"],
        elo0=sprt["elo0"],
        elo1=sprt["elo1"],
        elo_model=elo_model,
    )
    return {
        "run_id": run_id,
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
    # would hand it.
    query = arguments["filter"]
    limit = int(arguments.get("limit", ACTIONS_LIMIT_MAX))
    actions = (
        rundb.db["actions"]
        .find(query)
        .sort([("time", DESCENDING), ("_id", DESCENDING)])
        .limit(limit)
    )
    return {"actions": [{**action, "_id": str(action["_id"])} for action in actions]}


TOOL_FUNCTIONS = {
    "get_run": get_run,
    "list_active_runs": list_active_runs,
    "list_finished_runs": list_finished_runs,
    "get_elo": get_elo,
    "search_actions": search_actions,
}
