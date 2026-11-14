"""The MCP tools this server lists, stated as the JSON Schema clients read.

Each entry of `TOOLS` is one tool as `tools/list` publishes it. MCP reads a
schema with no `$schema` as draft 2020-12, and so does valgebra-jsonschema,
which compiles each `inputSchema` and `outputSchema` into a valgebra set;
valgebra-planning reads the pair into the tool's contract. A schema either one
cannot read fails the import, so the server never lists a contract it does not
enforce.

The schemas describe JSON values, not the documents MongoDB holds: an
`integer` is any number with no fractional part, `1.0` included, and a
`pattern` is a search, so every pattern here is anchored. Every result record
is closed, so a field nobody published, such as a worker's address, cannot
leave through it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from valgebra_planning.contracts import Contract, read_contract

from fishtest.schemas import (
    ACTION_QUERY_CLAUSE_MAX,
    ACTION_QUERY_FIELDS,
    ACTION_QUERY_LIST_MAX,
)

FINISHED_RUNS_PAGE_SIZE = 50
ACTIONS_LIMIT_MAX = 200

# The hints a client may act on: every tool here reads, and reads only this
# server's data.
READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

RUN_ID = {"type": "string", "pattern": "^[a-f0-9]{24}$"}
COUNT = {"type": "integer", "minimum": 0}

RESULTS = {
    "type": "object",
    "properties": {
        "wins": COUNT,
        "losses": COUNT,
        "draws": COUNT,
        "crashes": COUNT,
        "time_losses": COUNT,
        "pentanomial": {"type": "array", "items": COUNT, "minItems": 5, "maxItems": 5},
    },
    "required": ["wins", "losses", "draws"],
    "additionalProperties": False,
}

SPRT = {
    "type": "object",
    "properties": {
        "elo0": {"type": "number"},
        "elo1": {"type": "number"},
        "elo_model": {"type": "string"},
        "state": {"enum": ["", "accepted", "rejected"]},
        "llr": {"type": "number"},
        "lower_bound": {"type": "number"},
        "upper_bound": {"type": "number"},
    },
    "required": ["elo0", "elo1", "state", "llr", "lower_bound", "upper_bound"],
    "additionalProperties": False,
}

SPSA = {
    "type": "object",
    "properties": {"iter": COUNT, "num_iter": COUNT, "params": COUNT},
    "required": ["iter", "num_iter", "params"],
    "additionalProperties": False,
}

# What the public run page shows of a run: the test it asks for, how far it
# got, and its verdict. Dates are the strings `/api/get_run` gives.
RUN_SUMMARY = {
    "type": "object",
    "properties": {
        "run_id": RUN_ID,
        "username": {"type": "string"},
        "new_tag": {"type": "string"},
        "base_tag": {"type": "string"},
        "tc": {"type": "string"},
        "new_tc": {"type": "string"},
        "threads": COUNT,
        "book": {"type": "string"},
        "num_games": COUNT,
        "info": {"type": "string"},
        "tests_repo": {"type": "string"},
        "resolved_new": {"type": "string"},
        "resolved_base": {"type": "string"},
        "start_time": {"type": "string"},
        "last_updated": {"type": "string"},
        "approved": {"type": "boolean"},
        "finished": {"type": "boolean"},
        "deleted": {"type": "boolean"},
        "failed": {"type": "boolean"},
        "is_green": {"type": "boolean"},
        "is_yellow": {"type": "boolean"},
        "workers": COUNT,
        "cores": COUNT,
        "results": RESULTS,
        "sprt": SPRT,
        "spsa": SPSA,
    },
    "required": [
        "run_id",
        "username",
        "new_tag",
        "base_tag",
        "tc",
        "num_games",
        "start_time",
        "last_updated",
        "approved",
        "finished",
        "results",
    ],
    "additionalProperties": False,
}

RUN_LIST = {"type": "array", "items": RUN_SUMMARY}

# The /api/actions filter language: what `action_query_schema` admits, written
# as the JSON a client sends.
FILTER_CONSTANT = {"type": ["string", "number", "boolean", "null"]}
FILTER_LIST = {
    "type": "array",
    "items": FILTER_CONSTANT,
    "maxItems": ACTION_QUERY_LIST_MAX,
}
FILTER_COMPARISON = {
    "type": "object",
    "properties": {
        "$eq": FILTER_CONSTANT,
        "$ne": FILTER_CONSTANT,
        "$gt": FILTER_CONSTANT,
        "$gte": FILTER_CONSTANT,
        "$lt": FILTER_CONSTANT,
        "$lte": FILTER_CONSTANT,
        "$in": FILTER_LIST,
        "$nin": FILTER_LIST,
    },
    "additionalProperties": False,
}
FILTER_TERM = {"anyOf": [FILTER_CONSTANT, FILTER_COMPARISON]}
FILTER_CLAUSES = {
    "type": "array",
    "items": {"$ref": "#/$defs/filter"},
    "minItems": 1,
    "maxItems": ACTION_QUERY_CLAUSE_MAX,
}
FILTER = {
    "type": "object",
    "properties": {
        **{field: FILTER_TERM for field in ACTION_QUERY_FIELDS},
        "$and": FILTER_CLAUSES,
        "$or": FILTER_CLAUSES,
        "$nor": FILTER_CLAUSES,
    },
    "additionalProperties": False,
}

# An action names its kind, its author and its time; the rest depends on the
# kind, and is what /api/actions returns too.
ACTION = {
    "type": "object",
    "properties": {
        "_id": {"type": "string"},
        "action": {"type": "string"},
        "username": {"type": "string"},
        "time": {"type": "number"},
    },
    "required": ["_id", "action", "username", "time"],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_run",
        "title": "Get a test run",
        "description": (
            "Return one fishtest run: the test it asks for, its progress, its"
            " results and its verdict."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": RUN_ID},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": RUN_SUMMARY,
        "annotations": READ_ONLY,
    },
    {
        "name": "list_active_runs",
        "title": "List the active runs",
        "description": "Return every run that has not finished, approved or not.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"runs": RUN_LIST},
            "required": ["runs"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "list_finished_runs",
        "title": "List finished runs",
        "description": (
            f"Return one page of finished runs, newest first,"
            f" {FINISHED_RUNS_PAGE_SIZE} to a page, optionally one user's, only"
            f" the passed ones, only the yellow ones, or only long time controls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1},
                "username": {"type": "string"},
                "success_only": {"type": "boolean"},
                "yellow_only": {"type": "boolean"},
                "ltc_only": {"type": "boolean"},
            },
            "required": ["page"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"page": COUNT, "total": COUNT, "runs": RUN_LIST},
            "required": ["page", "total", "runs"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "get_elo",
        "title": "Get the Elo estimate of an SPRT run",
        "description": (
            "Return the SPRT bounds and log-likelihood ratio of an SPRT run, its"
            " Elo estimate with a 95% confidence interval, and the likelihood of"
            " superiority."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": RUN_ID},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "run_id": RUN_ID,
                "elo_model": {"type": "string"},
                "elo0": {"type": "number"},
                "elo1": {"type": "number"},
                "LLR": {"type": "number"},
                "lower_bound": {"type": "number"},
                "upper_bound": {"type": "number"},
                "elo": {"type": "number"},
                "ci": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "LOS": {"type": "number"},
            },
            "required": [
                "run_id",
                "elo_model",
                "elo0",
                "elo1",
                "LLR",
                "lower_bound",
                "upper_bound",
                "elo",
                "ci",
                "LOS",
            ],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "search_actions",
        "title": "Search the event log",
        "description": (
            "Return the newest events that match a filter in the /api/actions"
            " language: a field matched against a constant or a comparison"
            " ($eq, $ne, $gt, $gte, $lt, $lte, $in, $nin), combined with $and,"
            " $or and $nor."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"$ref": "#/$defs/filter"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": ACTIONS_LIMIT_MAX,
                },
            },
            "required": ["filter"],
            "additionalProperties": False,
            "$defs": {"filter": FILTER},
        },
        "outputSchema": {
            "type": "object",
            "properties": {"actions": {"type": "array", "items": ACTION}},
            "required": ["actions"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
]

# Each tool's contract, read once at import.
CONTRACTS: dict[str, Contract] = {tool["name"]: read_contract(tool) for tool in TOOLS}

# The listing's version, the server names it: a digest of every tool's complete
# definition, annotations included, so any change to what a client reads is a
# new version.
TOOLS_DIGEST = hashlib.sha256(
    json.dumps(TOOLS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()[:16]
