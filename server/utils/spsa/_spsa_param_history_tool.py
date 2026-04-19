#!/usr/bin/env python3
"""Local SPSA param_history migration helpers.

Run these scripts from server/ with uv so PyMongo and the fishtest package are
available, for example:

    cd /home/usr00/_git/fishtest/server
    uv run python ../scripts/dev/spsa_top_run_docs.py --limit 20
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, ReplaceOne, UpdateOne
from pymongo.collection import Collection

DEFAULT_URI = "mongodb://localhost:27017/"
DEFAULT_DB = "fishtest_new"
DEFAULT_COLLECTION = "runs"
DEFAULT_BACKUP_COLLECTION = "runs_spsa_backup"
DEFAULT_LIMIT = 20
DEFAULT_BATCH_SIZE = 250
DEFAULT_ITER_TOLERANCE = 1.0e-6
DEFAULT_PREVIEW_COUNT = 10

Document = dict[str, Any]
HistoryTransform = Callable[[Document], list[list[dict[str, Any]]] | None]


@dataclass(slots=True)
class MutationStats:
    scanned: int = 0
    changed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    previews: list[tuple[str, int, int]] = field(default_factory=list)


def _parse_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _format_run_date(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    return value.strftime("%Y-%m-%d")


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("expected an integer > 0")
    return number


def _nonnegative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("expected a number >= 0")
    return number


def _connect(args: argparse.Namespace) -> MongoClient[Document]:
    return MongoClient(args.uri)


def _runs_collection(
    client: MongoClient[Document], args: argparse.Namespace
) -> Collection[Document]:
    return client[args.db][args.collection]


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--uri", default=DEFAULT_URI, help="MongoDB connection URI")
    parser.add_argument("--db", default=DEFAULT_DB, help="MongoDB database name")
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="MongoDB runs collection name",
    )


def _add_limit_arg(
    parser: argparse.ArgumentParser, *, default: int | None = None
) -> None:
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=default,
        help="Limit the number of matching runs to scan",
    )


def _add_run_filter_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-id",
        type=_parse_object_id,
        help="Restrict the operation to a single runs._id",
    )


def _add_mutation_args(parser: argparse.ArgumentParser) -> None:
    _add_connection_args(parser)
    _add_run_filter_arg(parser)
    _add_limit_arg(parser)
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        help="Bulk-write batch size for mutating operations",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply the mutation. Without this flag the command is dry-run only.",
    )


def _build_spsa_query(args: argparse.Namespace) -> Document:
    query: Document = {"args.spsa": {"$exists": True}}
    if getattr(args, "run_id", None) is not None:
        query["_id"] = args.run_id
    return query


def _find_runs(
    collection: Collection[Document],
    query: Document,
    *,
    projection: Document | None = None,
    limit: int | None = None,
):
    cursor = collection.find(query, projection=projection)
    if limit is not None:
        cursor = cursor.limit(limit)
    return cursor


def _print_table(headers: list[str], rows: list[list[object]]) -> None:
    if not rows:
        print("No matching documents.")
        return

    rendered_rows = [
        ["" if value is None else str(value) for value in row] for row in rows
    ]
    widths = [len(header) for header in headers]
    for row in rendered_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    print(
        " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    )
    print("-+-".join("-" * width for width in widths))
    for row in rendered_rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _read_spsa(doc: Document) -> Mapping[str, Any]:
    args = doc.get("args")
    if not isinstance(args, Mapping):
        raise ValueError("missing args mapping")
    spsa = args.get("spsa")
    if not isinstance(spsa, Mapping):
        raise ValueError("missing args.spsa mapping")
    return spsa


def _read_param_history(doc: Document) -> list[Any]:
    history = _read_spsa(doc).get("param_history", [])
    if not isinstance(history, list):
        raise ValueError("args.spsa.param_history is not a list")
    return history


def _read_params(spsa: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    params = spsa.get("params", [])
    if not isinstance(params, list):
        raise ValueError("args.spsa.params is not a list")
    normalized: list[Mapping[str, Any]] = []
    for index, param in enumerate(params):
        if not isinstance(param, Mapping):
            raise ValueError(f"args.spsa.params[{index}] is not a mapping")
        normalized.append(param)
    return normalized


def _as_finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if isfinite(number) else None


def _as_positive_float(value: object, *, field_name: str) -> float:
    number = _as_finite_float(value)
    if number is None or number <= 0:
        raise ValueError(f"invalid {field_name}: expected a finite number > 0")
    return number


def _as_nonnegative_int(
    value: object, *, field_name: str, allow_none: bool = False
) -> int | None:
    if allow_none and value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid {field_name}: booleans are not integers")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"invalid {field_name}: expected an integer >= 0")
        return value
    number = _as_finite_float(value)
    if number is None:
        if allow_none:
            return None
        raise ValueError(f"invalid {field_name}: expected an integer >= 0")
    rounded = round(number)
    if abs(number - rounded) > DEFAULT_ITER_TOLERANCE or rounded < 0:
        raise ValueError(f"invalid {field_name}: expected an integer >= 0")
    return int(rounded)


def _target_history_samples(param_count: int) -> int:
    if param_count <= 0:
        return 0
    if param_count < 100:
        return 100
    if param_count < 1000:
        return int(10000 / param_count)
    return 1


def _target_history_period(*, num_iter: int, param_count: int) -> float:
    samples = _target_history_samples(param_count)
    if num_iter <= 0 or samples <= 0:
        return 0.0
    return num_iter / samples


def _iter_sample_is_c_based(sample: list[Any]) -> bool:
    return any(
        isinstance(sample_param, Mapping) and "c" in sample_param
        for sample_param in sample
    )


def _iter_sample_is_iter_only(sample: list[Any]) -> bool:
    return bool(sample) and all(
        isinstance(sample_param, Mapping)
        and "iter" in sample_param
        and "c" not in sample_param
        and "R" not in sample_param
        for sample_param in sample
    )


def _recover_sample_iter_from_c(
    spsa: Mapping[str, Any],
    sample: list[Any],
    *,
    tolerance: float,
) -> int:
    params = _read_params(spsa)
    if len(sample) != len(params):
        raise ValueError(
            "history sample length does not match args.spsa.params length",
        )

    gamma = _as_positive_float(spsa.get("gamma"), field_name="args.spsa.gamma")
    recovered_iters: list[float] = []
    for index, (param, sample_param) in enumerate(zip(params, sample), start=1):
        if not isinstance(sample_param, Mapping):
            raise ValueError(f"history sample entry {index} is not a mapping")

        base_c = _as_positive_float(
            param.get("c"), field_name=f"args.spsa.params[{index - 1}].c"
        )
        sample_c = _as_positive_float(
            sample_param.get("c"), field_name=f"history sample entry {index}.c"
        )
        iter_local = (base_c / sample_c) ** (1.0 / gamma)
        if not isfinite(iter_local) or iter_local <= 0:
            raise ValueError(
                f"history sample entry {index} produced an invalid recovered iteration"
            )
        recovered_iters.append(max(iter_local - 1.0, 0.0))

    if not recovered_iters:
        raise ValueError("history sample does not contain any recoverable c values")

    recovered_iters.sort()
    median_iter = recovered_iters[len(recovered_iters) // 2]
    rounded_iter = max(int(round(median_iter)), 0)
    max_error = max(abs(value - rounded_iter) for value in recovered_iters)
    if max_error > tolerance:
        raise ValueError(
            "history sample recovered inconsistent iterations "
            f"(max error {max_error:.3e} > {tolerance:.3e})",
        )

    live_iter = _as_nonnegative_int(
        spsa.get("iter"), field_name="args.spsa.iter", allow_none=True
    )
    num_iter = _as_nonnegative_int(
        spsa.get("num_iter"), field_name="args.spsa.num_iter", allow_none=True
    )
    upper_bound_candidates = [
        value for value in (live_iter, num_iter) if value is not None
    ]
    if upper_bound_candidates:
        rounded_iter = min(rounded_iter, min(upper_bound_candidates))

    return rounded_iter


def _extract_sample_iter(sample: list[Any]) -> int:
    sample_iters: list[int] = []
    for index, sample_param in enumerate(sample, start=1):
        if not isinstance(sample_param, Mapping):
            raise ValueError(f"history sample entry {index} is not a mapping")
        sample_iter = _as_nonnegative_int(
            sample_param.get("iter"),
            field_name=f"history sample entry {index}.iter",
        )
        if sample_iter is not None:
            sample_iters.append(sample_iter)

    if not sample_iters:
        raise ValueError("history sample does not contain any iter values")

    sample_iters.sort()
    median_iter = sample_iters[len(sample_iters) // 2]
    if any(value != median_iter for value in sample_iters):
        raise ValueError("history sample contains inconsistent iter values")
    return median_iter


def _drop_history_r(doc: Document) -> list[list[dict[str, Any]]] | None:
    history = _read_param_history(doc)
    new_history: list[list[dict[str, Any]]] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        new_sample: list[dict[str, Any]] = []
        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping",
                )
            new_sample.append(
                {key: value for key, value in sample_param.items() if key != "R"}
            )
        new_history.append(new_sample)

    return new_history if new_history != history else None


def _convert_history_c_to_iter(
    doc: Document,
    *,
    tolerance: float,
) -> list[list[dict[str, Any]]] | None:
    spsa = _read_spsa(doc)
    history = _read_param_history(doc)
    new_history: list[list[dict[str, Any]]] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if not sample:
            new_history.append([])
            continue

        sample_iter = (
            _extract_sample_iter(sample)
            if _iter_sample_is_iter_only(sample)
            else _recover_sample_iter_from_c(spsa, sample, tolerance=tolerance)
        )

        new_sample: list[dict[str, Any]] = []
        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping",
                )
            new_sample.append(
                {
                    "theta": sample_param.get("theta"),
                    "iter": sample_iter,
                }
            )
        new_history.append(new_sample)

    return new_history if new_history != history else None


def _resample_dense_history(doc: Document) -> list[list[dict[str, Any]]] | None:
    spsa = _read_spsa(doc)
    history = _read_param_history(doc)
    params = _read_params(spsa)
    target_count = _target_history_samples(len(params))
    if len(history) <= target_count:
        return None

    num_iter = _as_nonnegative_int(
        spsa.get("num_iter"), field_name="args.spsa.num_iter"
    )
    period = _target_history_period(num_iter=num_iter, param_count=len(params))
    if period <= 0 or target_count <= 0:
        return []

    iter_samples: list[tuple[int, list[dict[str, Any]]]] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        sample_iter = _extract_sample_iter(sample)
        normalized_sample: list[dict[str, Any]] = []
        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping",
                )
            normalized_sample.append(dict(sample_param))
        iter_samples.append((sample_iter, normalized_sample))

    iter_samples.sort(key=lambda item: item[0])
    resampled: list[list[dict[str, Any]]] = []
    for sample_iter, sample in iter_samples:
        threshold = period * (len(resampled) + 1)
        if sample_iter + DEFAULT_ITER_TOLERANCE >= threshold:
            resampled.append(sample)
        if len(resampled) >= target_count:
            break

    return resampled if resampled != history else None


def _collect_mutation_stats(
    collection: Collection[Document],
    query: Document,
    *,
    limit: int | None,
    transform: HistoryTransform,
) -> MutationStats:
    stats = MutationStats()
    projection = {
        "args.spsa": 1,
    }
    for doc in _find_runs(collection, query, projection=projection, limit=limit):
        stats.scanned += 1
        history = _read_param_history(doc)
        try:
            new_history = transform(doc)
        except ValueError as error:
            stats.errors.append(f"{doc['_id']}: {error}")
            continue

        if new_history is None:
            stats.unchanged += 1
            continue

        stats.changed += 1
        if len(stats.previews) < DEFAULT_PREVIEW_COUNT:
            stats.previews.append((str(doc["_id"]), len(history), len(new_history)))

    return stats


def _apply_history_mutation(
    collection: Collection[Document],
    query: Document,
    *,
    limit: int | None,
    batch_size: int,
    transform: HistoryTransform,
) -> int:
    projection = {
        "args.spsa": 1,
    }
    operations: list[Any] = []
    modified_count = 0
    for doc in _find_runs(collection, query, projection=projection, limit=limit):
        new_history = transform(doc)
        if new_history is None:
            continue
        operations.append(
            UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {"args.spsa.param_history": new_history}},
            )
        )
        if len(operations) >= batch_size:
            modified_count += collection.bulk_write(
                operations, ordered=False
            ).modified_count
            operations.clear()

    if operations:
        modified_count += collection.bulk_write(
            operations, ordered=False
        ).modified_count
    return modified_count


def _print_mutation_stats(action: str, stats: MutationStats) -> None:
    print(f"Action: {action}")
    print(f"Scanned: {stats.scanned}")
    print(f"Changed: {stats.changed}")
    print(f"Unchanged: {stats.unchanged}")
    print(f"Errors: {len(stats.errors)}")
    if stats.previews:
        print()
        _print_table(
            ["run_id", "history_before", "history_after"],
            [list(preview) for preview in stats.previews],
        )
    if stats.errors:
        print()
        print("Errors:")
        for error in stats.errors[:DEFAULT_PREVIEW_COUNT]:
            print(f"- {error}")
        if len(stats.errors) > DEFAULT_PREVIEW_COUNT:
            print(f"- ... and {len(stats.errors) - DEFAULT_PREVIEW_COUNT} more")


def _run_history_mutation(
    args: argparse.Namespace,
    *,
    action: str,
    transform: HistoryTransform,
) -> int:
    query = _build_spsa_query(args)
    with _connect(args) as client:
        collection = _runs_collection(client, args)
        stats = _collect_mutation_stats(
            collection,
            query,
            limit=args.limit,
            transform=transform,
        )
        _print_mutation_stats(action, stats)
        if stats.errors:
            return 1
        if not args.write:
            print()
            print("Dry run only. Re-run with --write to apply this mutation.")
            return 0

        modified_count = _apply_history_mutation(
            collection,
            query,
            limit=args.limit,
            batch_size=args.batch_size,
            transform=transform,
        )
        print()
        print(f"Applied mutation to {modified_count} runs.")
    return 0


def _aggregate_top_run_docs(
    collection: Collection[Document], limit: int
) -> list[list[object]]:
    pipeline = [
        {
            "$project": {
                "start_time": "$start_time",
                "username": "$args.username",
                "tc": "$args.tc",
                "num_games": "$args.num_games",
                "param_count": {"$size": {"$ifNull": ["$args.spsa.params", []]}},
                "history_len": {"$size": {"$ifNull": ["$args.spsa.param_history", []]}},
                "doc_size": {"$bsonSize": "$$ROOT"},
            }
        },
        {"$sort": {"doc_size": -1, "_id": 1}},
        {"$limit": limit},
    ]
    rows: list[list[object]] = []
    for doc in collection.aggregate(pipeline):
        rows.append(
            [
                doc.get("_id"),
                _format_run_date(doc.get("start_time")),
                doc.get("doc_size"),
                doc.get("username"),
                doc.get("tc"),
                doc.get("num_games"),
                doc.get("param_count"),
                doc.get("history_len"),
            ]
        )
    return rows


def _aggregate_top_spsa_docs(
    collection: Collection[Document], limit: int
) -> list[list[object]]:
    pipeline = [
        {"$match": {"args.spsa": {"$exists": True}}},
        {
            "$project": {
                "start_time": "$start_time",
                "username": "$args.username",
                "tc": "$args.tc",
                "num_games": "$args.num_games",
                "param_count": {"$size": {"$ifNull": ["$args.spsa.params", []]}},
                "history_len": {"$size": {"$ifNull": ["$args.spsa.param_history", []]}},
                "spsa_size": {"$bsonSize": "$args.spsa"},
                "doc_size": {"$bsonSize": "$$ROOT"},
            }
        },
        {"$sort": {"spsa_size": -1, "_id": 1}},
        {"$limit": limit},
    ]
    rows: list[list[object]] = []
    for doc in collection.aggregate(pipeline):
        rows.append(
            [
                doc.get("_id"),
                _format_run_date(doc.get("start_time")),
                doc.get("spsa_size"),
                doc.get("doc_size"),
                doc.get("username"),
                doc.get("tc"),
                doc.get("num_games"),
                doc.get("param_count"),
                doc.get("history_len"),
            ]
        )
    return rows


def main_show_top_run_docs(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show the largest run documents in the runs collection"
    )
    _add_connection_args(parser)
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        help="Number of runs to show",
    )
    args = parser.parse_args(argv)
    with _connect(args) as client:
        rows = _aggregate_top_run_docs(_runs_collection(client, args), args.limit)
    _print_table(
        [
            "run_id",
            "created",
            "doc_size",
            "username",
            "tc",
            "num_games",
            "params",
            "history",
        ],
        rows,
    )
    return 0


def main_show_top_spsa_docs(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show the largest SPSA subdocuments in the runs collection"
    )
    _add_connection_args(parser)
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        help="Number of SPSA runs to show",
    )
    args = parser.parse_args(argv)
    with _connect(args) as client:
        rows = _aggregate_top_spsa_docs(_runs_collection(client, args), args.limit)
    _print_table(
        [
            "run_id",
            "created",
            "spsa_size",
            "doc_size",
            "username",
            "tc",
            "num_games",
            "params",
            "history",
        ],
        rows,
    )
    return 0


def main_backup_spsa_runs(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy SPSA runs into a dedicated backup collection"
    )
    _add_mutation_args(parser)
    parser.add_argument(
        "--target-collection",
        default=DEFAULT_BACKUP_COLLECTION,
        help="Destination collection for the SPSA backup",
    )
    parser.add_argument(
        "--drop-target",
        action="store_true",
        help="Drop the destination collection before copying",
    )
    args = parser.parse_args(argv)
    query = _build_spsa_query(args)
    projection = None
    with _connect(args) as client:
        db = client[args.db]
        source = _runs_collection(client, args)
        target = db[args.target_collection]
        matched = 0
        operations: list[Any] = []
        for doc in _find_runs(source, query, projection=projection, limit=args.limit):
            matched += 1
            operations.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        print(f"SPSA runs selected for backup: {matched}")
        print(f"Target collection: {args.target_collection}")
        if args.drop_target:
            print("Target collection will be dropped before copy.")
        if not args.write:
            print("Dry run only. Re-run with --write to create or refresh the backup.")
            return 0

        if args.drop_target:
            target.drop()

        copied = 0
        for batch_start in range(0, len(operations), args.batch_size):
            batch = operations[batch_start : batch_start + args.batch_size]
            if not batch:
                continue
            result = target.bulk_write(batch, ordered=False)
            copied += result.upserted_count + result.modified_count
        print(f"Copied or refreshed {copied} SPSA runs in {args.target_collection}.")
    return 0


def main_drop_history_r(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drop legacy R fields from SPSA param_history rows"
    )
    _add_mutation_args(parser)
    args = parser.parse_args(argv)
    return _run_history_mutation(
        args, action="drop R from param_history", transform=_drop_history_r
    )


def main_convert_history_c_to_iter(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replace legacy param_history c values with recovered iter values"
    )
    _add_mutation_args(parser)
    parser.add_argument(
        "--iter-tolerance",
        type=_nonnegative_float,
        default=DEFAULT_ITER_TOLERANCE,
        help="Maximum allowed recovery error before a sample is rejected",
    )
    args = parser.parse_args(argv)
    return _run_history_mutation(
        args,
        action="replace c with iter in param_history",
        transform=lambda doc: _convert_history_c_to_iter(
            doc, tolerance=args.iter_tolerance
        ),
    )


def main_find_dense_histories(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find SPSA runs whose param_history is denser than the current live target"
    )
    _add_connection_args(parser)
    _add_run_filter_arg(parser)
    _add_limit_arg(parser)
    args = parser.parse_args(argv)
    query = _build_spsa_query(args)
    projection = {
        "args.username": 1,
        "args.tc": 1,
        "args.spsa.params": 1,
        "args.spsa.param_history": 1,
    }
    rows: list[list[object]] = []
    with _connect(args) as client:
        for doc in _find_runs(
            _runs_collection(client, args),
            query,
            projection=projection,
            limit=args.limit,
        ):
            spsa = _read_spsa(doc)
            history = _read_param_history(doc)
            target_count = _target_history_samples(len(_read_params(spsa)))
            if len(history) > target_count:
                rows.append(
                    [
                        doc.get("_id"),
                        doc.get("args", {}).get("username"),
                        doc.get("args", {}).get("tc"),
                        len(_read_params(spsa)),
                        len(history),
                        target_count,
                    ]
                )
    _print_table(
        ["run_id", "username", "tc", "params", "history", "target"],
        rows,
    )
    return 0


def main_resample_dense_histories(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resample dense SPSA param_history rows to the current live target count"
    )
    _add_mutation_args(parser)
    args = parser.parse_args(argv)
    return _run_history_mutation(
        args,
        action="resample dense param_history rows",
        transform=_resample_dense_history,
    )
