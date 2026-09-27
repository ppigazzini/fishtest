#!/usr/bin/env python3
"""Validate the stored documents against the schemas and count the failures.

The audit only reads. It checks every unfinished run, user, worker and net,
and the kvstore entries that carry a schema, and counts each failure by
collection, path and code. A list index prints as `[*]`, so one field failing
across many tasks counts under one path. Runs are grouped by version: the
server logs a failing run older than RUN_VERSION to stdout only.

The server raises on a failing user, worker or net where it validates one:
a user update, a worker update, a net download. It logs a failing run.

    cd server
    uv run python utils/validate_documents.py [--db NAME] [--limit N]
"""

import argparse
from collections import Counter
from datetime import UTC

from bson.codec_options import CodecOptions
from pymongo import MongoClient
from valgebra import ValidationError

from fishtest.schemas import (
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


def audit(db, limit):
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
    return tally


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=FISHTEST, help="database name")
    parser.add_argument(
        "--limit", type=int, default=0, help="documents per collection, 0 for all"
    )
    args = parser.parse_args()
    # The server's own codec: without it a stored datetime decodes naive, and
    # every `datetime_utc` field fails.
    codec_options = CodecOptions(tz_aware=True, tzinfo=UTC)
    with MongoClient("localhost") as client:
        db = client[args.db].with_options(codec_options=codec_options)
        audit(db, args.limit).report()


if __name__ == "__main__":
    main()
