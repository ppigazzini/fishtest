#!/usr/bin/env python3
"""Backfill missing worker API keys.

Idempotent: only sets api_key when it is missing/None/empty string.

Optionally, rotate all API keys (dangerous).

Intended to be run while the web server is offline.

Example:
  ./utils/backfill_api_keys.py --db fishtest_new
  ./utils/backfill_api_keys.py --db fishtest_new --dry-run
  ./utils/backfill_api_keys.py --db fishtest_new --rotate-all --yes
  ./utils/backfill_api_keys.py --db fishtest_new --drop-all --yes
"""

from __future__ import annotations

import argparse

from fishtest.rundb import RunDb  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill missing/empty user api_key")
    parser.add_argument(
        "--db",
        default="fishtest_new",
        help="MongoDB database name (default: fishtest_new)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write changes; only report what would change",
    )
    parser.add_argument(
        "--rotate-all",
        action="store_true",
        help="Rotate api_key for all users (DANGEROUS; requires --yes)",
    )
    parser.add_argument(
        "--drop-all",
        action="store_true",
        help="Unset api_key for all users (DANGEROUS; requires --yes)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a dangerous operation (required for --rotate-all)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.rotate_all and args.drop_all:
        print("ERROR: --rotate-all and --drop-all are mutually exclusive")
        return 2

    if (args.rotate_all or args.drop_all) and not args.yes:
        print("ERROR: --rotate-all/--drop-all requires --yes")
        return 2

    rundb = RunDb(db_name=args.db, is_primary_instance=False)
    users = rundb.userdb.users

    if args.rotate_all:
        query = {}
    elif args.drop_all:
        query = {"api_key": {"$exists": True}}
    else:
        # Only backfill missing/empty keys; do NOT rotate existing keys.
        query = {
            "$or": [
                {"api_key": {"$exists": False}},
                {"api_key": None},
                {"api_key": ""},
            ]
        }

    total_missing = users.count_documents(query)
    if args.rotate_all:
        print(f"Users to rotate api_key: {total_missing}")
    elif args.drop_all:
        print(f"Users to drop api_key: {total_missing}")
    else:
        print(f"Users missing api_key: {total_missing}")

    if total_missing == 0:
        return 0

    updated = 0
    cursor = users.find(query, {"username": 1}).sort("_id", 1)
    for user in cursor:
        username = user.get("username")
        if not username:
            continue

        if args.dry_run:
            updated += 1
            continue

        if args.rotate_all:
            new_api_key = rundb.userdb.generate_api_key()
            result = users.update_one(
                {"_id": user["_id"]},
                {"$set": {"api_key": new_api_key}},
            )
            if result.modified_count == 1:
                updated += 1
        elif args.drop_all:
            result = users.update_one(
                {"_id": user["_id"]},
                {"$unset": {"api_key": ""}},
            )
            if result.modified_count == 1:
                updated += 1
        else:
            api_key = rundb.userdb.ensure_worker_api_key(username)
            if api_key:
                updated += 1

    if args.dry_run:
        print(f"Would update: {updated}")
    else:
        print(f"Updated: {updated}")

    if args.rotate_all:
        remaining_missing = users.count_documents(
            {
                "$or": [
                    {"api_key": {"$exists": False}},
                    {"api_key": None},
                    {"api_key": ""},
                ]
            }
        )
        print(f"Remaining missing api_key: {remaining_missing}")
    elif args.drop_all:
        remaining_with_key = users.count_documents({"api_key": {"$exists": True}})
        print(f"Remaining users with api_key: {remaining_with_key}")
    else:
        remaining = users.count_documents(query)
        print(f"Remaining missing api_key: {remaining}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
