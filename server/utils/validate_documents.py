#!/usr/bin/env python3
"""Validate the stored documents against the schemas and count the failures.

The audit only reads. It checks every unfinished run, user, worker and net,
and the kvstore entries that carry a schema, and counts each failure by
collection, path and code. A list index prints as `[*]`, so one field failing
across many tasks counts under one path. Runs are grouped by version: the
server logs a failing run older than RUN_VERSION to stdout only.

The server raises on a failing user, worker or net where it validates one:
a user update, a worker update, a net download. It logs a failing run.

With `--mcp` it also reads every document the MCP endpoint publishes, each
through its tool's result contract: every run, finished or not, as the
`get_run` summary and, for an SPRT run, the `get_elo` estimate; every stored
action as a `search_actions` event; and every active task's worker as a
`list_active_workers` row. The endpoint refuses a result outside its
contract, so each failure is a document a client cannot read. Each action is
also checked against its own schema in the same read: the event drops a field
the contract does not name, so only the stored check names a document that
holds one, or a kind the server no longer writes.

    cd server
    uv run python utils/validate_documents.py [--db NAME] [--limit N] [--mcp]
"""

import argparse
import sys
import time
from collections import Counter
from datetime import UTC

from bson.codec_options import CodecOptions
from pymongo import MongoClient
from valgebra import ValidationError

from fishtest.mcp.contracts import CONTRACTS
from fishtest.mcp.tools import (
    RUN_PROJECTION,
    _action,
    elo_estimate,
    plain,
    run_summary,
    worker_row,
)
from fishtest.schemas import (
    action_schema,
    books_schema,
    github_api_cache_schema,
    legacy_usernames_schema,
    nn_schema,
    runs_schema,
    user_schema,
    worker_schema,
)
from fishtest.util import FISHTEST

COLLECTIONS = (
    ("users", user_schema),
    ("workers", worker_schema),
    ("nns", nn_schema),
)
KVSTORE_ENTRIES = (
    ("books", books_schema),
    ("legacy_usernames", legacy_usernames_schema),
    ("github_api_cache", github_api_cache_schema),
)


def path_pattern(path):
    """Print an error path, with every list index as `[*]`."""
    pattern = ""
    for part in path:
        if isinstance(part, int):
            pattern += "[*]"
        elif part.isidentifier():
            pattern += f".{part}"
        else:
            pattern += f"[{part!r}]"
    return pattern.removeprefix(".") or "(document)"


class Tally:
    """Documents checked and failures counted, by group, path and code."""

    def __init__(self):
        self.checked = Counter()
        self.failing = Counter()
        self.failures = Counter()
        self.first = {}

    def check(self, group, document_id, document, schema):
        self.checked[group] += 1
        try:
            schema.validate(document)
        except ValidationError as e:
            self.failing[group] += 1
            for error in e.errors:
                key = (group, path_pattern(error["path"]), error["code"])
                self.failures[key] += 1
                self.first.setdefault(key, document_id)

    def check_call(self, group, document_id, call, schema):
        """Count what `call` returns against `schema`, or the exception it raises."""
        # An old document can lack what the reader expects, and the reader then
        # raises: that is a document a client cannot read, counted as a failure.
        try:
            value = call()
        except Exception as e:
            self.checked[group] += 1
            self.failing[group] += 1
            key = (group, "(document)", f"raises {type(e).__name__} {e}")
            self.failures[key] += 1
            self.first.setdefault(key, document_id)
            return
        self.check(group, document_id, value, schema)

    def report(self):
        width = max(map(len, self.checked), default=0)
        print(f"{'group':<{width}}  {'checked':>8}  {'failing':>8}")
        for group, checked in sorted(self.checked.items()):
            print(f"{group:<{width}}  {checked:>8}  {self.failing[group]:>8}")
        print()
        if not self.failures:
            print("No document fails its schema.")
            return
        print("count  group  path  code  first _id")
        for (group, path, code), count in self.failures.most_common():
            first = self.first[group, path, code]
            print(f"{count:>5}  {group}  {path}  {code}  {first}")


def audit(db, limit, *, mcp=False):
    tally = Tally()
    for run in db["runs"].find({"finished": False}).limit(limit):
        group = f"runs v{run.get('version', '?')}"
        tally.check(group, run["_id"], run, runs_schema)
    for collection, schema in COLLECTIONS:
        for document in db[collection].find().limit(limit):
            tally.check(collection, document["_id"], document, schema)
    for key, schema in KVSTORE_ENTRIES:
        for document in db["kvstore"].find({"_id": key}):
            tally.check(f"kvstore {key}", key, document["value"], schema)
    if mcp:
        audit_mcp(db, limit, tally)
    return tally


# Runs read between two progress lines: the Elo estimate of an SPRT run takes
# milliseconds, so a full database takes minutes.
PROGRESS_EVERY = 10_000


def _played(run):
    # Whether a run's results count a game: a malformed count is read as one,
    # so the estimate is asked of it, and fails where a client would.
    results = run.get("results", {})
    counts = results.get("pentanomial") or [
        results.get(key, 0) for key in ("wins", "losses", "draws")
    ]
    return any(counts)


def audit_mcp(db, limit, tally):
    summary, estimate = CONTRACTS["get_run"].returns, CONTRACTS["get_elo"].returns
    total = limit or db["runs"].estimated_document_count()
    print(f"mcp: reading {total} runs", file=sys.stderr, flush=True)
    start = time.monotonic()
    for read, run in enumerate(db["runs"].find({}, RUN_PROJECTION).limit(limit), 1):
        if read % PROGRESS_EVERY == 0:
            elapsed = time.monotonic() - start
            print(
                f"mcp: {read} of {total} runs, {elapsed:.0f} s",
                file=sys.stderr,
                flush=True,
            )
        version = run.get("version", "?")
        tally.check_call(
            f"mcp get_run v{version}",
            run["_id"],
            lambda run=run: plain(run_summary(run)),
            summary,
        )
        # get_elo estimates an SPRT test, and a fixed-games test that has
        # played a game; it refuses an SPSA tune and a test with no game.
        args = run.get("args", {})
        group = None
        if "sprt" in args:
            group = f"mcp get_elo v{version}"
        elif "spsa" not in args and _played(run):
            group = f"mcp get_elo fixed v{version}"
        if group is not None:
            tally.check_call(
                group,
                run["_id"],
                lambda run=run: plain(elo_estimate(run)),
                estimate,
            )
    audit_mcp_actions(db, limit, tally)
    audit_mcp_workers(db, limit, tally)


def audit_mcp_actions(db, limit, tally):
    # Every stored action twice in one read: as the server writes it, which
    # names a field or a kind its schema does not declare, and as
    # `search_actions` publishes it, one event in the tool's result, cleared
    # as the server clears it, which names an event a client cannot read.
    events = CONTRACTS["search_actions"].returns
    total = limit or db["actions"].estimated_document_count()
    print(f"mcp: reading {total} actions", file=sys.stderr, flush=True)
    start = time.monotonic()
    for read, action in enumerate(db["actions"].find().limit(limit), 1):
        if read % PROGRESS_EVERY == 0:
            elapsed = time.monotonic() - start
            print(
                f"mcp: {read} of {total} actions, {elapsed:.0f} s",
                file=sys.stderr,
                flush=True,
            )
        tally.check("actions", action["_id"], action, action_schema)
        tally.check_call(
            "mcp search_actions",
            action["_id"],
            lambda action=action: plain({"actions": [_action(action)]}),
            events,
        )


def audit_mcp_workers(db, limit, tally):
    # Every active task's worker as `list_active_workers` publishes it, built
    # as `RunDb.get_machines` builds a secondary instance's rows: the task's
    # worker info, the run it plays, its index and its last update.
    listing = CONTRACTS["list_active_workers"].returns
    projection = {"tasks": 1, "args": 1}
    for run in db["runs"].find({"finished": False}, projection).limit(limit):
        tasks = run.get("tasks", [])
        if not any(task.get("active") for task in tasks):
            continue
        for task_id, task in enumerate(tasks):
            if not task.get("active"):
                continue
            # Built inside the call, so a task the row cannot be built from is
            # counted rather than raised.
            tally.check_call(
                "mcp list_active_workers",
                run["_id"],
                lambda run=run, task_id=task_id, task=task: plain(
                    _worker_result(worker_row(_machine(run, task_id, task)))
                ),
                listing,
            )


def _machine(run, task_id, task):
    """Return the row `RunDb.get_machines` builds of an active task."""
    return task["worker_info"] | {
        "last_updated": task.get("last_updated"),
        "run": run,
        "task_id": task_id,
    }


def _worker_result(row):
    """Return the one-worker result its contract checks: the row in the page."""
    return {"page": 1, "total": 1, "cores": row["cores"], "workers": [row]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=FISHTEST, help="database name")
    parser.add_argument(
        "--limit", type=int, default=0, help="documents per collection, 0 for all"
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="also read every run as the MCP endpoint publishes it",
    )
    args = parser.parse_args()
    # The server's own codec: without it a stored datetime decodes naive, and
    # every `datetime_utc` field fails.
    codec_options = CodecOptions(tz_aware=True, tzinfo=UTC)
    with MongoClient("localhost") as client:
        db = client[args.db].with_options(codec_options=codec_options)
        audit(db, args.limit, mcp=args.mcp).report()


if __name__ == "__main__":
    main()
