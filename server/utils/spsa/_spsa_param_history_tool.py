#!/usr/bin/env python3
"""Local staged SPSA param_history migration helper.

Run the staged workflow from server/ with uv so PyMongo and the fishtest
package are available, for example:

    cd /home/usr00/_git/fishtest/server
    uv run python utils/spsa/spsa_param_history_tool.py stage-orig --help
    uv run python utils/spsa/spsa_param_history_tool.py stage-new --help
    uv run python utils/spsa/spsa_param_history_tool.py apply-stage orig --help
    uv run python utils/spsa/spsa_param_history_tool.py apply-stage new --help
    uv run python utils/spsa/spsa_param_history_tool.py inspect-iter-window --help
    uv run python utils/spsa/spsa_param_history_tool.py list-constant-history --help

The dense-history resampler remains available as a standalone maintenance
command:

    uv run python utils/spsa/spsa_param_history_tool.py resample-dense-histories --help
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil, floor, isclose, isfinite, log
from typing import Any, cast

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, ReplaceOne, UpdateOne
from pymongo.collection import Collection

from fishtest.spsa_workflow import (
    build_spsa_chart_payload,
    resolve_spsa_history_samples,
)

DEFAULT_URI = "mongodb://localhost:27017/"
DEFAULT_DB = "fishtest_new"
DEFAULT_COLLECTION = "runs"
DEFAULT_ORIG_COLLECTION = "spsa_orig"
DEFAULT_NEW_COLLECTION = "spsa_new"
DEFAULT_LIMIT = 20
DEFAULT_BATCH_SIZE = 250
DEFAULT_ITER_TOLERANCE = 1.0e-6
DEFAULT_C_TOLERANCE = 1.0e-12
DEFAULT_R_TOLERANCE = 1.0e-12
DEFAULT_CHART_TOLERANCE = 1.0e-12
DEFAULT_PREVIEW_COUNT = 10
DEFAULT_RESAMPLE_LIMIT = 101
DEFAULT_ITER_REFINEMENT_RADIUS = 8

Document = dict[str, Any]
HistoryTransform = Callable[[Document], list[list[dict[str, Any]]] | None]


@dataclass(slots=True)
class MutationStats:
    scanned: int = 0
    changed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    previews: list[tuple[str, int, int]] = field(default_factory=list)


@dataclass(slots=True)
class CRoundTripCheck:
    checked_values: int = 0
    mismatched_values: int = 0
    max_abs_error: float = 0.0
    max_rel_error: float = 0.0
    first_mismatch: str | None = None


@dataclass(slots=True)
class CRoundTripStats:
    checked_values: int = 0
    mismatched_values: int = 0
    mismatch_runs: int = 0
    max_abs_error: float = 0.0
    max_rel_error: float = 0.0
    previews: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RRoundTripCheck:
    checked_values: int = 0
    mismatched_values: int = 0
    max_abs_error: float = 0.0
    max_rel_error: float = 0.0
    first_mismatch: str | None = None


@dataclass(slots=True)
class RRoundTripStats:
    checked_values: int = 0
    mismatched_values: int = 0
    mismatch_runs: int = 0
    max_abs_error: float = 0.0
    max_rel_error: float = 0.0
    previews: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ChartEquivalenceCheck:
    checked_rows: int = 0
    mismatched_rows: int = 0
    max_iter_ratio_error: float = 0.0
    max_value_error: float = 0.0
    first_mismatch: str | None = None


@dataclass(slots=True)
class ChartEquivalenceStats:
    checked_rows: int = 0
    mismatched_rows: int = 0
    mismatch_runs: int = 0
    max_iter_ratio_error: float = 0.0
    max_value_error: float = 0.0
    previews: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HistoryConversionReport:
    converted_history: list[list[dict[str, Any]]]
    c_check: CRoundTripCheck
    r_check: RRoundTripCheck
    chart_check: ChartEquivalenceCheck
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _HistoryRoundtripRequirements:
    require_c_roundtrip: bool
    require_r_roundtrip: bool
    require_chart_equivalence: bool


@dataclass(slots=True)
class StageBuildStats:
    scanned: int = 0
    staged: int = 0
    ready: int = 0
    validation_failed: int = 0
    conversion_errors: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    previews: list[tuple[str, str | None, str, int, int]] = field(default_factory=list)


@dataclass(slots=True)
class StageBuildResult:
    stage_doc: Document
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ApplyStageStats:
    scanned: int = 0
    ready: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    previews: list[tuple[str, str, int]] = field(default_factory=list)


@dataclass(slots=True)
class _HistorySamplingPrior:
    regime_name: str
    period: float
    append_rule: str
    max_history_length: int | None = None


@dataclass(slots=True)
class _HistorySampleValidationTarget:
    stored_c: float | None
    base_c: float
    stored_r: float | None
    base_a: float | None


def _whole_interval_samples_fixed_100(*, num_iter: int, param_count: int) -> int:
    del param_count
    if num_iter <= 0:
        return 0
    return num_iter // 100


def _whole_interval_samples_2018_03_17(*, num_iter: int, param_count: int) -> int:
    if num_iter <= 0:
        return 0

    if param_count < 20:
        frequency = 100
        maxlen = 5001
    else:
        frequency = 1000
        maxlen = 201
    return min(maxlen, num_iter // frequency)


def _whole_interval_samples_2018_03_24(*, num_iter: int, param_count: int) -> int:
    if num_iter <= 0:
        return 0

    if param_count < 20:
        frequency = 100
        maxlen = 5001
    elif param_count < 50:
        frequency = 1000
        maxlen = 201
    else:
        frequency = 10000
        maxlen = 41
    return min(maxlen, num_iter // frequency)


def _whole_interval_samples_2018_04_25(*, num_iter: int, param_count: int) -> int:
    if num_iter <= 0 or param_count <= 0:
        return 0

    frequency = max(100, 25 * param_count)
    maxlen = int(250000 / frequency)
    return min(maxlen, num_iter // frequency)


def _whole_interval_samples_2021_09_07(*, num_iter: int, param_count: int) -> int:
    if num_iter <= 0 or param_count <= 0:
        return 0

    frequency_limit = max(100, min(25 * param_count, 250000))
    frequency = int(frequency_limit // 100) * 100
    while frequency >= 100:
        if 250000 % frequency == 0:
            break
        frequency -= 100
    if frequency < 100:
        frequency = 100

    maxlen = 250000 // frequency
    return min(maxlen, num_iter // frequency)


def _whole_interval_samples_2022_03_29(*, num_iter: int, param_count: int) -> int:
    del num_iter
    if param_count <= 0:
        return 0
    if param_count < 100:
        return 101
    if param_count < 1000:
        return int(10000 / param_count)
    return 1


def _whole_interval_samples_2025_02_16(*, num_iter: int, param_count: int) -> int:
    del num_iter
    if param_count <= 0:
        return 0
    if param_count < 100:
        return 100
    if param_count < 1000:
        return int(10000 / param_count)
    return 1


_HISTORY_SAMPLING_REGIMES = (
    (
        datetime(2025, 2, 16, 20, 23, 47, tzinfo=UTC),
        "2025-02-16-100",
        _whole_interval_samples_2025_02_16,
    ),
    (
        datetime(2022, 3, 29, 15, 57, 26, tzinfo=UTC),
        "2022-03-29-101",
        _whole_interval_samples_2022_03_29,
    ),
    (
        datetime(2021, 9, 7, 16, 28, 13, tzinfo=UTC),
        "2021-09-07-multiples",
        _whole_interval_samples_2021_09_07,
    ),
    (
        datetime(2018, 4, 25, 6, 40, 58, tzinfo=UTC),
        "2018-04-25-freq",
        _whole_interval_samples_2018_04_25,
    ),
    (
        datetime(2018, 3, 24, 19, 25, 48, tzinfo=UTC),
        "2018-03-24-adjust",
        _whole_interval_samples_2018_03_24,
    ),
    (
        datetime(2018, 3, 17, 13, 49, 52, tzinfo=UTC),
        "2018-03-17-optimize",
        _whole_interval_samples_2018_03_17,
    ),
)


def _parse_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _format_run_date(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    return value.strftime("%Y-%m-%d")


def _format_run_label(run_id: object, run_date: str | None) -> str:
    run_id_text = str(run_id)
    if not run_date:
        return run_id_text
    return f"{run_id_text} ({run_date})"


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


def _history_has_non_empty_samples(history: Sequence[object]) -> bool:
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if sample:
            return True
    return False


def _collect_invalid_base_c_errors(doc: Document) -> list[str]:
    errors: list[str] = []
    for param_index, param in enumerate(_read_params(_read_spsa(doc))):
        base_c = _as_finite_float(param.get("c"))
        if base_c is not None and base_c > 0:
            continue
        errors.append(
            f"invalid args.spsa.params[{param_index}].c: expected a finite number > 0"
        )
    return errors


def _collect_invalid_history_base_c_warnings(doc: Document) -> list[str]:
    history = _read_param_history(doc)
    params = _read_params(_read_spsa(doc))
    invalid_param_indexes: set[int] = set()
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")

        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping"
                )

            sample_param_dict = dict(sample_param)
            stored_c = _as_finite_float(sample_param_dict.get("c"))
            stored_r = _as_finite_float(sample_param_dict.get("R"))
            if stored_c is None and stored_r is None:
                continue

            param_index = entry_index - 1
            if param_index >= len(params):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} has no matching SPSA param"
                )

            base_c = _as_finite_float(params[param_index].get("c"))
            if base_c is not None and base_c > 0:
                continue
            invalid_param_indexes.add(param_index)

    return [
        (
            f"invalid args.spsa.params[{param_index}].c: expected a finite number > 0; "
            "non-empty args.spsa.param_history was converted using other recoverable entries"
        )
        for param_index in sorted(invalid_param_indexes)
    ]


def _read_run_start_time(doc: Document) -> datetime:
    start_time = doc.get("start_time")
    if not isinstance(start_time, datetime):
        raise ValueError("missing start_time datetime")
    if start_time.tzinfo is None:
        return start_time.replace(tzinfo=UTC)
    return start_time.astimezone(UTC)


def _read_params(spsa: Mapping[str, Any]) -> list[dict[str, Any]]:
    params = spsa.get("params", [])
    if not isinstance(params, list):
        raise ValueError("args.spsa.params is not a list")
    normalized: list[dict[str, Any]] = []
    for index, param in enumerate(params):
        if not isinstance(param, Mapping):
            raise ValueError(f"args.spsa.params[{index}] is not a mapping")
        normalized.append({str(key): value for key, value in param.items()})
    return normalized


def _as_finite_float(value: object) -> float | None:
    try:
        number = float(cast(Any, value))
    except TypeError, ValueError:
        return None
    return number if isfinite(number) else None


def _as_positive_float(value: object, *, field_name: str) -> float:
    number = _as_finite_float(value)
    if number is None or number <= 0:
        raise ValueError(f"invalid {field_name}: expected a finite number > 0")
    return number


def _as_nonnegative_float_value(value: object, *, field_name: str) -> float:
    number = _as_finite_float(value)
    if number is None or number < 0:
        raise ValueError(f"invalid {field_name}: expected a finite number >= 0")
    return number


def _as_optional_nonnegative_float_value(value: object) -> float | None:
    number = _as_finite_float(value)
    if number is None:
        return None
    if number < 0:
        raise ValueError("expected a finite number >= 0")
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


def _read_num_iter_for_history(doc: Document) -> int:
    args = doc.get("args")
    if not isinstance(args, Mapping):
        raise ValueError("missing args mapping")

    num_games = _as_nonnegative_int(
        args.get("num_games"),
        field_name="args.num_games",
        allow_none=True,
    )
    if num_games is not None:
        return num_games // 2

    spsa = _read_spsa(doc)
    num_iter = _as_nonnegative_int(
        spsa.get("num_iter"),
        field_name="args.spsa.num_iter",
    )
    if num_iter is None:
        raise ValueError("missing args.num_games and args.spsa.num_iter")
    return num_iter


def _canonicalize_sample_iter(sample_iter: float, *, tolerance: float) -> float | int:
    rounded = round(sample_iter)
    if abs(sample_iter - rounded) <= tolerance:
        return int(rounded)
    return sample_iter


def _recompute_sample_c_from_iter(
    *,
    base_c: float,
    gamma: float,
    sample_iter: float,
) -> float:
    iter_local = sample_iter + 1.0
    if iter_local <= 0:
        raise ValueError(f"invalid sample iter: expected >= 0, got {sample_iter!r}")

    try:
        sample_c = float(base_c / iter_local**gamma)
    except (ArithmeticError, OverflowError, ValueError, ZeroDivisionError) as error:
        raise ValueError(
            f"unable to recompute c from iter={sample_iter!r}, base_c={base_c!r}, gamma={gamma!r}"
        ) from error

    if not isfinite(sample_c) or sample_c <= 0:
        raise ValueError(
            f"invalid recomputed c from iter={sample_iter!r}: {sample_c!r}"
        )

    return sample_c


def _recompute_sample_r_from_iter(
    *,
    base_a: float,
    base_c: float,
    A: float,
    alpha: float,
    gamma: float,
    sample_iter: float,
) -> float:
    iter_local = sample_iter + 1.0
    if iter_local <= 0:
        raise ValueError(f"invalid sample iter: expected >= 0, got {sample_iter!r}")

    sample_c = _recompute_sample_c_from_iter(
        base_c=base_c,
        gamma=gamma,
        sample_iter=sample_iter,
    )

    try:
        sample_r = float(base_a / (A + iter_local) ** alpha / sample_c**2)
    except (ArithmeticError, OverflowError, ValueError, ZeroDivisionError) as error:
        raise ValueError(
            "unable to recompute R from "
            f"iter={sample_iter!r}, base_a={base_a!r}, A={A!r}, alpha={alpha!r}, "
            f"base_c={base_c!r}, gamma={gamma!r}"
        ) from error

    if not isfinite(sample_r) or sample_r < 0:
        raise ValueError(
            f"invalid recomputed R from iter={sample_iter!r}: {sample_r!r}"
        )

    return sample_r


def _inspect_c_to_iter_roundtrip(
    doc: Document,
    new_history: list[list[dict[str, Any]]],
    *,
    tolerance: float,
) -> CRoundTripCheck:
    history = _read_param_history(doc)
    if len(history) != len(new_history):
        raise ValueError("history length changed during c-to-iter conversion")

    spsa = _read_spsa(doc)
    params = _read_params(spsa)
    gamma = _as_nonnegative_float_value(spsa.get("gamma"), field_name="args.spsa.gamma")
    check = CRoundTripCheck()

    for sample_index, (sample, new_sample) in enumerate(
        zip(history, new_history, strict=False),
        start=1,
    ):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if len(sample) != len(new_sample):
            raise ValueError(
                f"history sample {sample_index} changed length during conversion"
            )

        for entry_index, (sample_param, new_sample_param) in enumerate(
            zip(sample, new_sample, strict=False),
            start=1,
        ):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping"
                )
            if not isinstance(new_sample_param, Mapping):
                raise ValueError(
                    f"converted history sample {sample_index} entry {entry_index} is not a mapping"
                )

            sample_param_dict = dict(sample_param)
            new_sample_param_dict = dict(new_sample_param)
            stored_c = _as_finite_float(sample_param_dict.get("c"))
            if stored_c is None:
                continue

            param_index = entry_index - 1
            if param_index >= len(params):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} has no matching SPSA param"
                )

            base_c = _as_finite_float(params[param_index].get("c"))
            if base_c is None or base_c <= 0:
                continue
            sample_iter = _as_nonnegative_float_value(
                new_sample_param_dict.get("iter"),
                field_name=(
                    f"converted history sample {sample_index} entry {entry_index}.iter"
                ),
            )
            recomputed_c = _recompute_sample_c_from_iter(
                base_c=base_c,
                gamma=gamma,
                sample_iter=sample_iter,
            )

            abs_error = abs(recomputed_c - stored_c)
            rel_denominator = max(abs(recomputed_c), abs(stored_c), 1.0e-300)
            rel_error = abs_error / rel_denominator
            check.checked_values += 1
            check.max_abs_error = max(check.max_abs_error, abs_error)
            check.max_rel_error = max(check.max_rel_error, rel_error)

            if isclose(recomputed_c, stored_c, rel_tol=tolerance, abs_tol=tolerance):
                continue

            check.mismatched_values += 1
            if check.first_mismatch is None:
                check.first_mismatch = (
                    f"sample {sample_index} entry {entry_index}: "
                    f"stored c={stored_c:.16g}, iter={sample_iter:.16g}, "
                    f"recomputed c={recomputed_c:.16g}, "
                    f"abs_error={abs_error:.6g}, rel_error={rel_error:.6g}"
                )

    return check


def _inspect_r_to_iter_roundtrip(
    doc: Document,
    new_history: list[list[dict[str, Any]]],
    *,
    tolerance: float,
) -> RRoundTripCheck:
    history = _read_param_history(doc)
    if len(history) != len(new_history):
        raise ValueError("history length changed during c-to-iter conversion")

    spsa = _read_spsa(doc)
    params = _read_params(spsa)
    A = _as_optional_nonnegative_float_value(spsa.get("A"))
    alpha = _as_optional_nonnegative_float_value(spsa.get("alpha"))
    gamma = _as_nonnegative_float_value(spsa.get("gamma"), field_name="args.spsa.gamma")
    check = RRoundTripCheck()

    for sample_index, (sample, new_sample) in enumerate(
        zip(history, new_history, strict=False),
        start=1,
    ):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if len(sample) != len(new_sample):
            raise ValueError(
                f"history sample {sample_index} changed length during conversion"
            )

        for entry_index, (sample_param, new_sample_param) in enumerate(
            zip(sample, new_sample, strict=False),
            start=1,
        ):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping"
                )
            if not isinstance(new_sample_param, Mapping):
                raise ValueError(
                    f"converted history sample {sample_index} entry {entry_index} is not a mapping"
                )

            sample_param_dict = dict(sample_param)
            new_sample_param_dict = dict(new_sample_param)
            stored_r = _as_finite_float(sample_param_dict.get("R"))
            if stored_r is None:
                continue

            if A is None or alpha is None:
                continue

            param_index = entry_index - 1
            if param_index >= len(params):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} has no matching SPSA param"
                )

            base_c = _as_finite_float(params[param_index].get("c"))
            if base_c is None or base_c <= 0:
                continue
            base_a = _as_optional_nonnegative_float_value(params[param_index].get("a"))
            if base_a is None:
                continue
            sample_iter = _as_nonnegative_float_value(
                new_sample_param_dict.get("iter"),
                field_name=(
                    f"converted history sample {sample_index} entry {entry_index}.iter"
                ),
            )
            recomputed_r = _recompute_sample_r_from_iter(
                base_a=base_a,
                base_c=base_c,
                A=A,
                alpha=alpha,
                gamma=gamma,
                sample_iter=sample_iter,
            )

            abs_error = abs(recomputed_r - stored_r)
            rel_denominator = max(abs(recomputed_r), abs(stored_r), 1.0e-300)
            rel_error = abs_error / rel_denominator
            check.checked_values += 1
            check.max_abs_error = max(check.max_abs_error, abs_error)
            check.max_rel_error = max(check.max_rel_error, rel_error)

            if isclose(recomputed_r, stored_r, rel_tol=tolerance, abs_tol=tolerance):
                continue

            check.mismatched_values += 1
            if check.first_mismatch is None:
                check.first_mismatch = (
                    f"sample {sample_index} entry {entry_index}: "
                    f"stored R={stored_r:.16g}, iter={sample_iter:.16g}, "
                    f"recomputed R={recomputed_r:.16g}, "
                    f"abs_error={abs_error:.6g}, rel_error={rel_error:.6g}"
                )

    return check


def _chart_value_matches(left: object, right: object, *, tolerance: float) -> bool:
    if left is None and right is None:
        return True

    left_number = _as_finite_float(left)
    right_number = _as_finite_float(right)
    if left_number is None or right_number is None:
        return False

    return isclose(left_number, right_number, rel_tol=tolerance, abs_tol=tolerance)


def _chart_row_payload_matches(
    left_row: Mapping[str, Any],
    right_row: Mapping[str, Any],
    *,
    tolerance: float,
) -> bool:
    for key in ("values", "c_values"):
        left_values = left_row.get(key)
        right_values = right_row.get(key)
        if left_values is None and right_values is None:
            continue
        if not isinstance(left_values, list) or not isinstance(right_values, list):
            return False
        if len(left_values) != len(right_values):
            return False
        if not all(
            _chart_value_matches(left_value, right_value, tolerance=tolerance)
            for left_value, right_value in zip(left_values, right_values, strict=False)
        ):
            return False
    return True


def _normalize_chart_rows_for_comparison(
    rows: list[Any],
    *,
    iter_ratio_tolerance: float,
    value_tolerance: float,
) -> list[Any]:
    normalized_rows = [dict(row) if isinstance(row, Mapping) else row for row in rows]
    while len(normalized_rows) >= 2:
        previous_row = normalized_rows[-2]
        current_row = normalized_rows[-1]
        if not isinstance(previous_row, Mapping) or not isinstance(
            current_row, Mapping
        ):
            break

        previous_iter_ratio = _as_finite_float(previous_row.get("iter_ratio"))
        current_iter_ratio = _as_finite_float(current_row.get("iter_ratio"))
        if previous_iter_ratio is None or current_iter_ratio is None:
            break
        if abs(previous_iter_ratio - current_iter_ratio) > iter_ratio_tolerance:
            break
        if not _chart_row_payload_matches(
            previous_row,
            current_row,
            tolerance=value_tolerance,
        ):
            break

        normalized_rows[-2] = (
            current_row if current_iter_ratio >= previous_iter_ratio else previous_row
        )
        normalized_rows.pop()

    return normalized_rows


def _build_chart_payload_for_history(
    doc: Document,
    history: list[list[dict[str, Any]]],
    *,
    tolerance: float,
) -> dict[str, Any]:
    chart_doc = deepcopy(doc)
    args = chart_doc.get("args")
    if not isinstance(args, Mapping):
        raise ValueError("missing args mapping")

    chart_args = dict(args)
    spsa = dict(_read_spsa(chart_doc))
    spsa["param_history"] = history
    chart_args["spsa"] = spsa
    chart_doc["args"] = chart_args

    iter_tolerance = max(tolerance, DEFAULT_ITER_TOLERANCE)
    non_empty_samples = [sample for sample in history if sample]
    if non_empty_samples and all(
        _iter_sample_is_iter_only(sample) for sample in non_empty_samples
    ):
        resolved_iters = _resolve_history_sample_iters(
            chart_doc,
            list(history),
            tolerance=iter_tolerance,
        )
    else:
        resolved_iters = _resolve_history_sample_iters(
            chart_doc,
            list(history),
            tolerance=iter_tolerance,
        )
        resolved_iters = _integerize_resolved_history_iters(
            chart_doc,
            resolved_iters,
            tolerance=iter_tolerance,
        )

    normalized_history: list[list[dict[str, Any]]] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if not sample:
            normalized_history.append([])
            continue

        sample_iter = resolved_iters[sample_index - 1]
        if sample_iter is None:
            raise ValueError(f"history sample {sample_index} did not resolve to iter")

        normalized_history.append(
            [
                {
                    "theta": dict(sample_param).get("theta"),
                    "iter": sample_iter,
                }
                for sample_param in sample
                if isinstance(sample_param, Mapping)
            ]
        )

    placeholder_iter = max(tolerance, DEFAULT_ITER_TOLERANCE)
    for sample in normalized_history:
        if not sample:
            continue
        sample_iter = _as_finite_float(sample[0].get("iter"))
        if sample_iter is None or sample_iter > 0:
            continue
        for sample_param in sample:
            sample_param["iter"] = placeholder_iter

    spsa["param_history"] = normalized_history
    payload = build_spsa_chart_payload(spsa)
    return payload if isinstance(payload, dict) else {}


def _inspect_chart_roundtrip(
    doc: Document,
    new_history: list[list[dict[str, Any]]],
    *,
    tolerance: float,
) -> ChartEquivalenceCheck:
    original_payload = _build_chart_payload_for_history(
        doc,
        _read_param_history(doc),
        tolerance=tolerance,
    )
    converted_payload = _build_chart_payload_for_history(
        doc,
        new_history,
        tolerance=tolerance,
    )
    check = ChartEquivalenceCheck()

    original_param_names = original_payload.get("param_names")
    converted_param_names = converted_payload.get("param_names")
    if original_param_names != converted_param_names:
        check.mismatched_rows = 1
        check.first_mismatch = (
            "param_names changed during conversion: "
            f"before={original_param_names!r}, after={converted_param_names!r}"
        )
        return check

    original_rows = original_payload.get("chart_rows")
    converted_rows = converted_payload.get("chart_rows")
    if not isinstance(original_rows, list) or not isinstance(converted_rows, list):
        check.mismatched_rows = 1
        check.first_mismatch = "chart_rows payload is not a list"
        return check

    num_iter = _read_num_iter_for_history(doc)
    iter_ratio_tolerance = max(
        tolerance,
        1.0 / num_iter if num_iter > 0 else tolerance,
    )
    original_rows = _normalize_chart_rows_for_comparison(
        original_rows,
        iter_ratio_tolerance=iter_ratio_tolerance,
        value_tolerance=tolerance,
    )
    converted_rows = _normalize_chart_rows_for_comparison(
        converted_rows,
        iter_ratio_tolerance=iter_ratio_tolerance,
        value_tolerance=tolerance,
    )

    check.checked_rows = len(original_rows)
    if len(original_rows) != len(converted_rows):
        check.mismatched_rows = 1
        check.first_mismatch = (
            "chart row count changed during conversion: "
            f"before={len(original_rows)}, after={len(converted_rows)}"
        )
        return check

    for row_index, (original_row, converted_row) in enumerate(
        zip(original_rows, converted_rows, strict=False),
        start=1,
    ):
        if not isinstance(original_row, Mapping) or not isinstance(
            converted_row, Mapping
        ):
            check.mismatched_rows += 1
            if check.first_mismatch is None:
                check.first_mismatch = f"chart row {row_index} is not a mapping"
            continue

        original_row_dict = dict(original_row)
        converted_row_dict = dict(converted_row)
        original_iter_ratio = _as_finite_float(original_row_dict.get("iter_ratio"))
        converted_iter_ratio = _as_finite_float(converted_row_dict.get("iter_ratio"))
        original_iter_ratio_value = (
            0.0 if original_iter_ratio is None else original_iter_ratio
        )
        converted_iter_ratio_value = (
            0.0 if converted_iter_ratio is None else converted_iter_ratio
        )
        if original_iter_ratio is None or converted_iter_ratio is None:
            iter_ratio_error = float("inf")
        else:
            iter_ratio_error = abs(original_iter_ratio - converted_iter_ratio)
        check.max_iter_ratio_error = max(check.max_iter_ratio_error, iter_ratio_error)

        row_matches = _chart_value_matches(
            original_row_dict.get("iter_ratio"),
            converted_row_dict.get("iter_ratio"),
            tolerance=iter_ratio_tolerance,
        )
        mismatch_detail: str | None = None
        if not row_matches:
            mismatch_detail = (
                f"row {row_index} iter_ratio differs: "
                f"before={original_iter_ratio_value:.16g}, after={converted_iter_ratio_value:.16g}, "
                f"abs_error={iter_ratio_error:.6g}"
            )

        for key in ("values", "c_values"):
            original_values = original_row_dict.get(key)
            converted_values = converted_row_dict.get(key)
            if original_values is None and converted_values is None:
                continue
            if not isinstance(original_values, list) or not isinstance(
                converted_values, list
            ):
                row_matches = False
                if mismatch_detail is None:
                    mismatch_detail = f"row {row_index} {key} is not a list"
                continue
            if len(original_values) != len(converted_values):
                row_matches = False
                if mismatch_detail is None:
                    mismatch_detail = (
                        f"row {row_index} {key} length differs: "
                        f"before={len(original_values)}, after={len(converted_values)}"
                    )
                continue

            for value_index, (original_value, converted_value) in enumerate(
                zip(original_values, converted_values, strict=False),
                start=1,
            ):
                original_number = _as_finite_float(original_value)
                converted_number = _as_finite_float(converted_value)
                if original_number is None or converted_number is None:
                    value_error = (
                        0.0
                        if original_value is None and converted_value is None
                        else float("inf")
                    )
                else:
                    value_error = abs(original_number - converted_number)
                check.max_value_error = max(check.max_value_error, value_error)

                if _chart_value_matches(
                    original_value,
                    converted_value,
                    tolerance=tolerance,
                ):
                    continue

                row_matches = False
                if mismatch_detail is None:
                    mismatch_detail = (
                        f"row {row_index} {key}[{value_index}] differs: "
                        f"before={original_number!r}, after={converted_number!r}, "
                        f"abs_error={value_error:.6g}"
                    )

        if row_matches:
            continue

        check.mismatched_rows += 1
        if check.first_mismatch is None:
            check.first_mismatch = mismatch_detail

    return check


def _format_c_roundtrip_failure(check: CRoundTripCheck) -> str:
    return (
        "c-to-iter round-trip assertion failed: "
        f"{check.mismatched_values}/{check.checked_values} stored c values differ from c(iter); "
        f"max_abs_error={check.max_abs_error:.6g}, "
        f"max_rel_error={check.max_rel_error:.6g}; "
        f"{check.first_mismatch}"
    )


def _format_r_roundtrip_failure(check: RRoundTripCheck) -> str:
    return (
        "R-to-iter round-trip assertion failed: "
        f"{check.mismatched_values}/{check.checked_values} stored R values differ from R(iter); "
        f"max_abs_error={check.max_abs_error:.6g}, "
        f"max_rel_error={check.max_rel_error:.6g}; "
        f"{check.first_mismatch}"
    )


def _format_chart_roundtrip_failure(check: ChartEquivalenceCheck) -> str:
    return (
        "chart equivalence assertion failed: "
        f"{check.mismatched_rows}/{check.checked_rows} chart rows differ after conversion; "
        f"max_iter_ratio_error={check.max_iter_ratio_error:.6g}, "
        f"max_value_error={check.max_value_error:.6g}; "
        f"{check.first_mismatch}"
    )


def _normalize_iter_only_history(
    doc: Document,
    *,
    tolerance: float,
) -> list[list[dict[str, Any]]]:
    history = _read_param_history(doc)
    if all(
        isinstance(sample, list) and (not sample or _iter_sample_is_iter_only(sample))
        for sample in history
    ):
        normalized_history: list[list[dict[str, Any]]] = []
        for sample_index, sample in enumerate(history, start=1):
            if not isinstance(sample, list):
                raise ValueError(f"history sample {sample_index} is not a list")
            if not sample:
                normalized_history.append([])
                continue

            sample_iter = _extract_sample_iter(sample, tolerance=tolerance)
            normalized_history.append(
                [
                    {
                        "theta": dict(sample_param).get("theta"),
                        "iter": sample_iter,
                    }
                    for sample_param in sample
                    if isinstance(sample_param, Mapping)
                ]
            )
        return normalized_history

    converted_history = _convert_history_c_to_iter(doc, tolerance=tolerance)
    if converted_history is not None:
        return converted_history

    return []


def _history_roundtrip_requirements(doc: Document) -> _HistoryRoundtripRequirements:
    spsa = _read_spsa(doc)
    gamma = _as_nonnegative_float_value(spsa.get("gamma"), field_name="args.spsa.gamma")
    A = _as_optional_nonnegative_float_value(spsa.get("A"))
    alpha = _as_optional_nonnegative_float_value(spsa.get("alpha"))
    constant_c = _history_field_is_constant(
        doc,
        field_name="c",
        tolerance=DEFAULT_C_TOLERANCE,
    )
    constant_r = _history_field_is_constant(
        doc,
        field_name="R",
        tolerance=DEFAULT_R_TOLERANCE,
    )

    require_c_roundtrip = isfinite(gamma) and gamma > 0 and not constant_c
    require_r_roundtrip = (
        A is not None
        and alpha is not None
        and isfinite(alpha)
        and alpha > 0
        and not constant_r
    )
    require_chart_equivalence = not require_c_roundtrip and not require_r_roundtrip

    return _HistoryRoundtripRequirements(
        require_c_roundtrip=require_c_roundtrip,
        require_r_roundtrip=require_r_roundtrip,
        require_chart_equivalence=require_chart_equivalence,
    )


def _build_history_conversion_report(
    doc: Document,
    *,
    iter_tolerance: float,
    c_tolerance: float,
    r_tolerance: float,
    chart_tolerance: float,
) -> HistoryConversionReport:
    history = _read_param_history(doc)
    converted_history = _normalize_iter_only_history(
        doc,
        tolerance=iter_tolerance,
    )
    requirements = _history_roundtrip_requirements(doc)
    c_check = (
        _inspect_c_to_iter_roundtrip(
            doc,
            converted_history,
            tolerance=c_tolerance,
        )
        if requirements.require_c_roundtrip
        else CRoundTripCheck()
    )
    r_check = (
        _inspect_r_to_iter_roundtrip(
            doc,
            converted_history,
            tolerance=r_tolerance,
        )
        if requirements.require_r_roundtrip
        else RRoundTripCheck()
    )
    chart_check = _inspect_chart_roundtrip(
        doc,
        converted_history,
        tolerance=chart_tolerance,
    )

    errors: list[str] = []
    warnings: list[str] = []
    if _history_has_non_empty_samples(history):
        warnings.extend(_collect_invalid_history_base_c_warnings(doc))
    else:
        warnings.extend(
            (
                f"{error}; args.spsa.param_history is empty, "
                "so there is no legacy history to convert"
            )
            for error in _collect_invalid_base_c_errors(doc)
        )
    if requirements.require_c_roundtrip and c_check.mismatched_values > 0:
        errors.append(_format_c_roundtrip_failure(c_check))
    if requirements.require_chart_equivalence and chart_check.mismatched_rows > 0:
        errors.append(_format_chart_roundtrip_failure(chart_check))
    if requirements.require_r_roundtrip and r_check.mismatched_values > 0:
        warnings.append(_format_r_roundtrip_failure(r_check))

    return HistoryConversionReport(
        converted_history=converted_history,
        c_check=c_check,
        r_check=r_check,
        chart_check=chart_check,
        errors=errors,
        warnings=warnings,
    )


@dataclass(slots=True)
class _ConvertHistoryCToIterTransform:
    iter_tolerance: float
    c_tolerance: float
    chart_tolerance: float
    r_tolerance: float = DEFAULT_R_TOLERANCE
    roundtrip_stats: CRoundTripStats = field(default_factory=CRoundTripStats)
    r_stats: RRoundTripStats = field(default_factory=RRoundTripStats)
    chart_stats: ChartEquivalenceStats = field(default_factory=ChartEquivalenceStats)

    def __call__(self, doc: Document) -> list[list[dict[str, Any]]] | None:
        original_history = _read_param_history(doc)
        run_id = str(doc.get("_id", "<unknown>"))
        report = _build_history_conversion_report(
            doc,
            iter_tolerance=self.iter_tolerance,
            c_tolerance=self.c_tolerance,
            r_tolerance=self.r_tolerance,
            chart_tolerance=self.chart_tolerance,
        )
        self.roundtrip_stats.checked_values += report.c_check.checked_values
        self.roundtrip_stats.mismatched_values += report.c_check.mismatched_values
        self.roundtrip_stats.max_abs_error = max(
            self.roundtrip_stats.max_abs_error,
            report.c_check.max_abs_error,
        )
        self.roundtrip_stats.max_rel_error = max(
            self.roundtrip_stats.max_rel_error,
            report.c_check.max_rel_error,
        )

        self.r_stats.checked_values += report.r_check.checked_values
        self.r_stats.mismatched_values += report.r_check.mismatched_values
        self.r_stats.max_abs_error = max(
            self.r_stats.max_abs_error,
            report.r_check.max_abs_error,
        )
        self.r_stats.max_rel_error = max(
            self.r_stats.max_rel_error,
            report.r_check.max_rel_error,
        )

        if report.c_check.mismatched_values > 0:
            self.roundtrip_stats.mismatch_runs += 1
            if len(self.roundtrip_stats.previews) < DEFAULT_PREVIEW_COUNT:
                self.roundtrip_stats.previews.append(
                    f"{run_id}: {_format_c_roundtrip_failure(report.c_check)}"
                )

            raise ValueError(_format_c_roundtrip_failure(report.c_check))

        if report.r_check.mismatched_values > 0:
            self.r_stats.mismatch_runs += 1
            if len(self.r_stats.previews) < DEFAULT_PREVIEW_COUNT:
                self.r_stats.previews.append(
                    f"{run_id}: {_format_r_roundtrip_failure(report.r_check)}"
                )

            raise ValueError(_format_r_roundtrip_failure(report.r_check))

        self.chart_stats.checked_rows += report.chart_check.checked_rows
        self.chart_stats.mismatched_rows += report.chart_check.mismatched_rows
        self.chart_stats.max_iter_ratio_error = max(
            self.chart_stats.max_iter_ratio_error,
            report.chart_check.max_iter_ratio_error,
        )
        self.chart_stats.max_value_error = max(
            self.chart_stats.max_value_error,
            report.chart_check.max_value_error,
        )

        if report.chart_check.mismatched_rows > 0:
            self.chart_stats.mismatch_runs += 1
            if len(self.chart_stats.previews) < DEFAULT_PREVIEW_COUNT:
                self.chart_stats.previews.append(
                    f"{run_id}: {_format_chart_roundtrip_failure(report.chart_check)}"
                )

        return (
            report.converted_history
            if report.converted_history != original_history
            else None
        )


def _extract_sample_iter(sample: list[Any], *, tolerance: float) -> float:
    sample_iters: list[float] = []
    for index, sample_param in enumerate(sample, start=1):
        if not isinstance(sample_param, Mapping):
            raise ValueError(f"history sample entry {index} is not a mapping")
        sample_iter = _as_nonnegative_float_value(
            sample_param.get("iter"),
            field_name=f"history sample entry {index}.iter",
        )
        sample_iters.append(sample_iter)

    if not sample_iters:
        raise ValueError("history sample does not contain any iter values")

    sample_iters.sort()
    median_iter = sample_iters[len(sample_iters) // 2]
    if any(abs(value - median_iter) > tolerance for value in sample_iters):
        raise ValueError("history sample contains inconsistent iter values")
    return median_iter


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


def _history_sampling_regime(created: datetime) -> tuple[str, Callable[..., int]]:
    for boundary, regime_name, sample_counter in _HISTORY_SAMPLING_REGIMES:
        if created >= boundary:
            return regime_name, sample_counter
    return "2014-fixed-100", _whole_interval_samples_fixed_100


def _history_density_info(doc: Document) -> tuple[datetime, str, int, int]:
    created = _read_run_start_time(doc)
    param_count = len(_read_params(_read_spsa(doc)))
    num_iter = _read_num_iter_for_history(doc)
    regime_name, sample_counter = _history_sampling_regime(created)
    whole_interval_samples = sample_counter(num_iter=num_iter, param_count=param_count)
    current_target = _target_history_samples(param_count)
    return created, regime_name, whole_interval_samples, current_target


def _stabilize_integer_boundary(value: float, *, tolerance: float) -> float:
    rounded = round(value)
    if abs(value - rounded) <= tolerance:
        return float(rounded)
    return value


def _history_sampling_prior(
    *,
    created: datetime,
    num_iter: int,
    param_count: int,
) -> _HistorySamplingPrior:
    regime_name, _ = _history_sampling_regime(created)

    if regime_name == "2014-fixed-100":
        return _HistorySamplingPrior(
            regime_name=regime_name,
            period=100.0,
            append_rule="strict-lt",
        )

    if regime_name == "2018-03-17-optimize":
        if param_count < 20:
            period = 100.0
            max_history_length = 5001
        else:
            period = 1000.0
            max_history_length = 201
        return _HistorySamplingPrior(
            regime_name=regime_name,
            period=period,
            append_rule="strict-lt",
            max_history_length=max_history_length,
        )

    if regime_name == "2018-03-24-adjust":
        if param_count < 20:
            period = 100.0
            max_history_length = 5001
        elif param_count < 50:
            period = 1000.0
            max_history_length = 201
        else:
            period = 10000.0
            max_history_length = 41
        return _HistorySamplingPrior(
            regime_name=regime_name,
            period=period,
            append_rule="strict-lt",
            max_history_length=max_history_length,
        )

    if regime_name == "2018-04-25-freq":
        if param_count <= 0:
            return _HistorySamplingPrior(
                regime_name=regime_name,
                period=0.0,
                append_rule="strict-lt",
            )
        period = float(max(100, 25 * param_count))
        max_history_length = int(250000 / period)
        return _HistorySamplingPrior(
            regime_name=regime_name,
            period=period,
            append_rule="strict-lt",
            max_history_length=max_history_length,
        )

    if regime_name == "2021-09-07-multiples":
        if param_count <= 0:
            return _HistorySamplingPrior(
                regime_name=regime_name,
                period=0.0,
                append_rule="strict-lt",
            )
        frequency_limit = max(100, min(25 * param_count, 250000))
        period = float(int(frequency_limit // 100) * 100)
        while period >= 100.0:
            if 250000 % int(period) == 0:
                break
            period -= 100.0
        if period < 100.0:
            period = 100.0
        max_history_length = 250000 // int(period)
        return _HistorySamplingPrior(
            regime_name=regime_name,
            period=period,
            append_rule="strict-lt",
            max_history_length=max_history_length,
        )

    if regime_name == "2022-03-29-101":
        if param_count <= 0:
            return _HistorySamplingPrior(
                regime_name=regime_name,
                period=0.0,
                append_rule="strict-lt",
            )
        if param_count < 100:
            samples = 101.0
        elif param_count < 1000:
            samples = 10000.0 / param_count
        else:
            samples = 1.0
        period = 0.0 if num_iter <= 0 or samples <= 0 else num_iter / samples
        return _HistorySamplingPrior(
            regime_name=regime_name,
            period=period,
            append_rule="strict-lt",
        )

    if param_count <= 0:
        return _HistorySamplingPrior(
            regime_name=regime_name,
            period=0.0,
            append_rule="inclusive-le",
        )
    if param_count < 100:
        samples = 100.0
    elif param_count < 1000:
        samples = 10000.0 / param_count
    else:
        samples = 1.0
    period = 0.0 if num_iter <= 0 or samples <= 0 else num_iter / samples
    return _HistorySamplingPrior(
        regime_name=regime_name,
        period=period,
        append_rule="inclusive-le",
    )


def _read_history_terminal_iter(
    doc: Document,
    *,
    tolerance: float,
) -> int:
    spsa = _read_spsa(doc)
    iter_value = _as_nonnegative_float_value(
        spsa.get("iter"),
        field_name="args.spsa.iter",
    )
    rounded = round(iter_value)
    if abs(iter_value - rounded) > tolerance:
        raise ValueError(
            f"invalid args.spsa.iter: expected an integer iteration count, got {iter_value!r}"
        )
    return int(rounded)


def _history_sample_count_at_iter(
    iter_value: int,
    prior: _HistorySamplingPrior,
    *,
    tolerance: float,
) -> int:
    if iter_value <= 0 or prior.period <= 0:
        return 0

    ratio = _stabilize_integer_boundary(
        iter_value / prior.period,
        tolerance=tolerance,
    )
    if prior.append_rule == "inclusive-le":
        sample_count = int(floor(ratio))
    else:
        sample_count = int(ceil(ratio))

    if prior.max_history_length is not None:
        sample_count = min(sample_count, prior.max_history_length)

    return max(sample_count, 0)


def _history_sample_lower_bound(
    *,
    prior: _HistorySamplingPrior,
    sample_index: int,
    tolerance: float,
) -> int:
    if sample_index <= 0:
        raise ValueError(f"invalid sample index: expected > 0, got {sample_index!r}")

    if prior.append_rule == "inclusive-le":
        boundary = _stabilize_integer_boundary(
            sample_index * prior.period,
            tolerance=tolerance,
        )
        return int(ceil(boundary))

    boundary = _stabilize_integer_boundary(
        (sample_index - 1) * prior.period,
        tolerance=tolerance,
    )
    return int(floor(boundary)) + 1


def _history_sample_validation_targets(
    sample: list[Any],
    params: Sequence[Mapping[str, Any]],
    *,
    sample_index: int,
) -> list[_HistorySampleValidationTarget]:
    validation_targets: list[_HistorySampleValidationTarget] = []
    saw_validation_value = False
    first_invalid_base_c_error: str | None = None
    for entry_index, sample_param in enumerate(sample, start=1):
        if not isinstance(sample_param, Mapping):
            raise ValueError(
                f"history sample {sample_index} entry {entry_index} is not a mapping"
            )

        param_index = entry_index - 1
        if param_index >= len(params):
            raise ValueError(
                f"history sample {sample_index} entry {entry_index} has no matching SPSA param"
            )

        sample_param_dict = dict(sample_param)
        stored_c = _as_finite_float(sample_param_dict.get("c"))
        stored_r = _as_finite_float(sample_param_dict.get("R"))
        if stored_c is None and stored_r is None:
            continue
        saw_validation_value = True

        base_param = params[param_index]
        base_c = _as_finite_float(base_param.get("c"))
        if base_c is None or base_c <= 0:
            if first_invalid_base_c_error is None:
                first_invalid_base_c_error = f"invalid args.spsa.params[{param_index}].c: expected a finite number > 0"
            continue
        base_a = _as_optional_nonnegative_float_value(base_param.get("a"))
        if stored_r is not None and stored_c is None and base_a is None:
            raise ValueError(
                f"history sample {sample_index} entry {entry_index} cannot validate stored R without args.spsa.params[{param_index}].a"
            )
        validation_targets.append(
            _HistorySampleValidationTarget(
                stored_c=stored_c,
                base_c=base_c,
                stored_r=stored_r,
                base_a=base_a,
            )
        )

    if saw_validation_value and not validation_targets and first_invalid_base_c_error:
        raise ValueError(first_invalid_base_c_error)

    return validation_targets


def _score_history_sample_iter_validation_error(
    validation_targets: Sequence[_HistorySampleValidationTarget],
    *,
    A: float | None,
    alpha: float | None,
    gamma: float,
    sample_iter: int,
) -> tuple[float, float, float, float]:
    (
        c_max_rel_error,
        c_total_rel_error,
        c_max_abs_error,
        c_total_abs_error,
        r_max_rel_error,
        r_total_rel_error,
        r_max_abs_error,
        r_total_abs_error,
    ) = _score_history_sample_iter_validation_priority(
        validation_targets,
        A=A,
        alpha=alpha,
        gamma=gamma,
        sample_iter=sample_iter,
    )

    return (
        max(c_max_rel_error, r_max_rel_error),
        c_total_rel_error + r_total_rel_error,
        max(c_max_abs_error, r_max_abs_error),
        c_total_abs_error + r_total_abs_error,
    )


def _score_history_sample_iter_validation_priority(
    validation_targets: Sequence[_HistorySampleValidationTarget],
    *,
    A: float | None,
    alpha: float | None,
    gamma: float,
    sample_iter: int,
) -> tuple[float, float, float, float, float, float, float, float]:
    c_max_rel_error = 0.0
    c_total_rel_error = 0.0
    c_max_abs_error = 0.0
    c_total_abs_error = 0.0
    r_max_rel_error = 0.0
    r_total_rel_error = 0.0
    r_max_abs_error = 0.0
    r_total_abs_error = 0.0

    for target in validation_targets:
        if target.stored_c is not None:
            recomputed_c = _recompute_sample_c_from_iter(
                base_c=target.base_c,
                gamma=gamma,
                sample_iter=float(sample_iter),
            )
            abs_error = abs(recomputed_c - target.stored_c)
            rel_denominator = max(abs(recomputed_c), abs(target.stored_c), 1.0e-300)
            rel_error = abs_error / rel_denominator
            c_max_rel_error = max(c_max_rel_error, rel_error)
            c_total_rel_error += rel_error
            c_max_abs_error = max(c_max_abs_error, abs_error)
            c_total_abs_error += abs_error

        if (
            target.stored_r is None
            or target.base_a is None
            or A is None
            or alpha is None
        ):
            continue

        recomputed_r = _recompute_sample_r_from_iter(
            base_a=target.base_a,
            base_c=target.base_c,
            A=A,
            alpha=alpha,
            gamma=gamma,
            sample_iter=float(sample_iter),
        )
        abs_error = abs(recomputed_r - target.stored_r)
        rel_denominator = max(abs(recomputed_r), abs(target.stored_r), 1.0e-300)
        rel_error = abs_error / rel_denominator
        r_max_rel_error = max(r_max_rel_error, rel_error)
        r_total_rel_error += rel_error
        r_max_abs_error = max(r_max_abs_error, abs_error)
        r_total_abs_error += abs_error

    return (
        c_max_rel_error,
        c_total_rel_error,
        c_max_abs_error,
        c_total_abs_error,
        r_max_rel_error,
        r_total_rel_error,
        r_max_abs_error,
        r_total_abs_error,
    )


def _estimate_history_sample_iter_from_c(
    validation_targets: Sequence[_HistorySampleValidationTarget],
    *,
    gamma: float,
) -> float | None:
    if not isfinite(gamma) or gamma <= 0:
        return None

    sample_iters: list[float] = []
    for target in validation_targets:
        if target.stored_c is None:
            continue

        try:
            sample_iter = _as_finite_float(
                (target.base_c / target.stored_c) ** (1.0 / gamma) - 1.0
            )
        except ArithmeticError, OverflowError, ValueError, ZeroDivisionError:
            continue

        if sample_iter is None or sample_iter < 0:
            continue
        sample_iters.append(sample_iter)

    if not sample_iters:
        return None

    sample_iters.sort()
    return sample_iters[len(sample_iters) // 2]


def _estimate_history_sample_iter_from_r_target(
    target: _HistorySampleValidationTarget,
    *,
    A: float | None,
    alpha: float | None,
    gamma: float,
    seed: float,
) -> float | None:
    if (
        target.stored_r is None
        or target.stored_r <= 0
        or target.base_a is None
        or target.base_a <= 0
        or A is None
        or alpha is None
        or alpha <= 0
    ):
        return None

    if gamma == 0:
        try:
            iter_local = float(
                (target.base_a / (target.stored_r * target.base_c**2)) ** (1.0 / alpha)
                - A
            )
        except ArithmeticError, OverflowError, ValueError, ZeroDivisionError:
            return None
        sample_iter = _as_finite_float(iter_local - 1.0)
        if sample_iter is None or sample_iter < 0:
            return None
        return sample_iter

    iter_local = max(seed + 1.0, 1.0)
    for _ in range(24):
        sample_iter = iter_local - 1.0
        recomputed_r = _recompute_sample_r_from_iter(
            base_a=target.base_a,
            base_c=target.base_c,
            A=A,
            alpha=alpha,
            gamma=gamma,
            sample_iter=sample_iter,
        )
        if recomputed_r <= 0 or not isfinite(recomputed_r):
            return None

        log_error = log(recomputed_r / target.stored_r)
        if abs(log_error) <= 1.0e-14:
            break

        derivative = 2.0 * gamma / iter_local - alpha / (A + iter_local)
        if not isfinite(derivative) or abs(derivative) <= 1.0e-14:
            return None

        next_iter_local = iter_local - log_error / derivative
        if not isfinite(next_iter_local) or next_iter_local <= 0:
            return None

        if abs(next_iter_local - iter_local) <= 1.0e-12:
            iter_local = next_iter_local
            break
        iter_local = next_iter_local

    sample_iter = _as_finite_float(iter_local - 1.0)
    if sample_iter is None or sample_iter < 0:
        return None
    return sample_iter


def _estimate_history_sample_iter_from_r(
    validation_targets: Sequence[_HistorySampleValidationTarget],
    *,
    A: float | None,
    alpha: float | None,
    gamma: float,
    seed: float,
    upper_bound: int | None = None,
) -> float | None:
    sample_iters: list[float] = []
    has_r_targets = False
    for target in validation_targets:
        if (
            target.stored_r is not None
            and target.base_a is not None
            and A is not None
            and alpha is not None
            and alpha > 0
        ):
            has_r_targets = True
        sample_iter = _estimate_history_sample_iter_from_r_target(
            target,
            A=A,
            alpha=alpha,
            gamma=gamma,
            seed=seed,
        )
        if sample_iter is None:
            continue
        sample_iters.append(sample_iter)

    if not sample_iters:
        if not has_r_targets or upper_bound is None or upper_bound < 0:
            return None

        search_lower = 0
        search_upper = upper_bound
        step = max(1, upper_bound // 128)
        best_candidate = max(0, min(int(floor(seed + 0.5)), upper_bound))
        while True:
            candidates = list(range(search_lower, search_upper + 1, step))
            if not candidates or candidates[-1] != search_upper:
                candidates.append(search_upper)

            best_candidate = min(
                candidates,
                key=lambda candidate: (
                    _score_history_sample_iter_validation_priority(
                        validation_targets,
                        A=A,
                        alpha=alpha,
                        gamma=gamma,
                        sample_iter=candidate,
                    ),
                    abs(candidate - seed),
                    candidate,
                ),
            )
            if step == 1:
                return float(best_candidate)

            search_lower = max(0, best_candidate - step)
            search_upper = min(upper_bound, best_candidate + step)
            step = max(1, step // 2)

    sample_iters.sort()
    return sample_iters[len(sample_iters) // 2]


def _distance_to_history_sample_iter_estimates(
    sample_iter: int,
    estimates: Sequence[float],
) -> float:
    if not estimates:
        return 0.0
    return min(abs(sample_iter - estimate) for estimate in estimates)


def _signal_estimate_is_informative(
    estimate: float | None,
    *,
    is_constant: bool,
    requires_positive_exponent: float | None = None,
) -> bool:
    if estimate is None or is_constant:
        return False
    if requires_positive_exponent is None:
        return True
    return isfinite(requires_positive_exponent) and requires_positive_exponent > 0


def _refine_integer_history_sample_iter(
    *,
    validation_targets: Sequence[_HistorySampleValidationTarget],
    A: float | None,
    alpha: float | None,
    gamma: float,
    estimates: Sequence[float],
    lower_bound: int,
    upper_bound: int,
    candidate: int,
) -> int:
    if not validation_targets or lower_bound >= upper_bound:
        return candidate

    search_candidates: set[int] = {candidate}
    for estimate in estimates:
        seed = int(floor(estimate + 0.5))
        seed = max(lower_bound, min(seed, upper_bound))
        search_lower = max(lower_bound, seed - DEFAULT_ITER_REFINEMENT_RADIUS)
        search_upper = min(upper_bound, seed + DEFAULT_ITER_REFINEMENT_RADIUS)
        search_candidates.update(range(search_lower, search_upper + 1))

    best_candidate = candidate
    best_score = _score_history_sample_iter_validation_priority(
        validation_targets,
        A=A,
        alpha=alpha,
        gamma=gamma,
        sample_iter=candidate,
    ) + (_distance_to_history_sample_iter_estimates(candidate, estimates),)

    for neighbor in sorted(search_candidates):
        score = _score_history_sample_iter_validation_priority(
            validation_targets,
            A=A,
            alpha=alpha,
            gamma=gamma,
            sample_iter=neighbor,
        ) + (_distance_to_history_sample_iter_estimates(neighbor, estimates),)
        if score < best_score:
            best_candidate = neighbor
            best_score = score

    return best_candidate


def _integerize_resolved_history_iters(
    doc: Document,
    resolved_iters: Sequence[float | int | None],
    *,
    tolerance: float,
) -> list[int | None]:
    non_empty_positions = [
        index
        for index, sample_iter in enumerate(resolved_iters)
        if sample_iter is not None
    ]
    if not non_empty_positions:
        return [None] * len(resolved_iters)

    history = _read_param_history(doc)
    spsa = _read_spsa(doc)
    params = _read_params(spsa)
    A = _as_optional_nonnegative_float_value(spsa.get("A"))
    alpha = _as_optional_nonnegative_float_value(spsa.get("alpha"))
    gamma = _as_nonnegative_float_value(spsa.get("gamma"), field_name="args.spsa.gamma")
    actual_iter = _read_history_terminal_iter(doc, tolerance=tolerance)
    if actual_iter < 1:
        raise ValueError(
            "non-empty param_history requires a positive terminal SPSA iter"
        )
    constant_c = _history_field_is_constant(
        doc,
        field_name="c",
        tolerance=DEFAULT_C_TOLERANCE,
    )
    constant_r = _history_field_is_constant(
        doc,
        field_name="R",
        tolerance=DEFAULT_R_TOLERANCE,
    )

    integerized: list[int | None] = [None] * len(resolved_iters)
    for position in non_empty_positions:
        resolved_estimate = _as_nonnegative_float_value(
            resolved_iters[position],
            field_name=f"resolved history sample {position + 1}.iter",
        )
        # Preserve each sample's direct c/R-derived checkpoint instead of clamping
        # it to a neighboring row's fallback chart position.
        # A stored history row is always a post-start checkpoint. The starting
        # theta vector at iter=0 is implicit and is never written to
        # param_history.
        lower_bound = 1
        upper_bound = actual_iter

        sample = history[position]
        if not isinstance(sample, list):
            raise ValueError(f"history sample {position + 1} is not a list")
        validation_targets = _history_sample_validation_targets(
            sample,
            params,
            sample_index=position + 1,
        )
        c_estimate = _estimate_history_sample_iter_from_c(
            validation_targets,
            gamma=gamma,
        )
        r_seed = c_estimate if c_estimate is not None else resolved_estimate
        r_estimate = _estimate_history_sample_iter_from_r(
            validation_targets,
            A=A,
            alpha=alpha,
            gamma=gamma,
            seed=r_seed,
            upper_bound=actual_iter,
        )
        c_informative = _signal_estimate_is_informative(
            c_estimate,
            is_constant=constant_c,
            requires_positive_exponent=gamma,
        )
        r_informative = _signal_estimate_is_informative(
            r_estimate,
            is_constant=constant_r,
        )

        estimates = []
        if c_informative:
            estimates.append(c_estimate)
        if r_informative:
            estimates.append(r_estimate)
        if not estimates:
            integerized[position] = max(
                lower_bound,
                min(int(floor(resolved_estimate + 0.5)), upper_bound),
            )
            continue

        seed_candidates = {
            max(lower_bound, min(int(floor(estimate + 0.5)), upper_bound))
            for estimate in estimates
        }
        candidate = min(
            seed_candidates,
            key=lambda sample_iter: (
                _score_history_sample_iter_validation_priority(
                    validation_targets,
                    A=A,
                    alpha=alpha,
                    gamma=gamma,
                    sample_iter=sample_iter,
                )
                + (_distance_to_history_sample_iter_estimates(sample_iter, estimates),)
            ),
        )

        candidate = _refine_integer_history_sample_iter(
            validation_targets=validation_targets,
            A=A,
            alpha=alpha,
            gamma=gamma,
            estimates=estimates,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            candidate=candidate,
        )

        integerized[position] = candidate

    return integerized


def _resolve_history_sample_iter(
    doc: Document,
    sample: list[Any],
    *,
    sample_index: int,
    total_samples: int,
    tolerance: float,
) -> float | int:
    if _iter_sample_is_iter_only(sample):
        sample_iter = _extract_sample_iter(sample, tolerance=tolerance)
        return _canonicalize_sample_iter(sample_iter, tolerance=tolerance)

    resolved_iters = _resolve_history_sample_iters(
        doc,
        [sample],
        tolerance=tolerance,
    )
    sample_iter = resolved_iters[0]
    if sample_iter is None:
        raise ValueError(f"history sample {sample_index} is not a valid list")
    return sample_iter


def _resolve_history_sample_iters(
    doc: Document,
    history: list[Any],
    *,
    tolerance: float,
) -> list[float | int | None]:
    resolved_iters: list[float | int | None] = [None] * len(history)
    non_empty_samples: list[list[Any]] = []
    non_empty_indexes: list[int] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if not sample:
            continue
        non_empty_samples.append(sample)
        non_empty_indexes.append(sample_index)

    if not non_empty_samples:
        return resolved_iters

    if all(_iter_sample_is_iter_only(sample) for sample in non_empty_samples):
        for sample_index, sample in zip(
            non_empty_indexes, non_empty_samples, strict=False
        ):
            sample_iter = _extract_sample_iter(sample, tolerance=tolerance)
            resolved_iters[sample_index - 1] = _canonicalize_sample_iter(
                sample_iter,
                tolerance=tolerance,
            )
        return resolved_iters

    spsa = _read_spsa(doc)
    resolved_samples = resolve_spsa_history_samples(
        non_empty_samples,
        params=_read_params(spsa),
        A=_as_optional_nonnegative_float_value(spsa.get("A")),
        alpha=_as_optional_nonnegative_float_value(spsa.get("alpha")),
        gamma=_as_nonnegative_float_value(
            spsa.get("gamma"), field_name="args.spsa.gamma"
        ),
        num_iter=float(_read_num_iter_for_history(doc)),
        iter_value=_as_finite_float(spsa.get("iter")) or 0.0,
    )
    if len(resolved_samples) != len(non_empty_samples):
        raise ValueError("history sample resolution lost entries")

    for sample_index, resolved_sample in zip(
        non_empty_indexes, resolved_samples, strict=False
    ):
        sample_iter, _ = resolved_sample
        resolved_iters[sample_index - 1] = _canonicalize_sample_iter(
            sample_iter,
            tolerance=tolerance,
        )

    return resolved_iters


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
                {str(key): value for key, value in sample_param.items() if key != "R"}
            )
        new_history.append(new_sample)

    return new_history if new_history != history else None


def _convert_history_c_to_iter(
    doc: Document,
    *,
    tolerance: float,
) -> list[list[dict[str, Any]]] | None:
    history = _read_param_history(doc)
    resolved_iters = _resolve_history_sample_iters(
        doc,
        history,
        tolerance=tolerance,
    )
    resolved_iters = _integerize_resolved_history_iters(
        doc,
        resolved_iters,
        tolerance=tolerance,
    )
    new_history: list[list[dict[str, Any]]] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if not sample:
            new_history.append([])
            continue

        sample_iter = resolved_iters[sample_index - 1]
        if sample_iter is None:
            raise ValueError(f"history sample {sample_index} did not resolve to iter")

        new_sample: list[dict[str, Any]] = []
        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping",
                )
            sample_param_dict = dict(sample_param)
            new_sample.append(
                {
                    "theta": sample_param_dict.get("theta"),
                    "iter": sample_iter,
                }
            )
        new_history.append(new_sample)

    return new_history if new_history != history else None


def _resample_dense_history(doc: Document) -> list[list[dict[str, Any]]] | None:
    history = _read_param_history(doc)
    created, regime_name, whole_interval_samples, current_target = (
        _history_density_info(doc)
    )
    del created, regime_name
    if whole_interval_samples <= DEFAULT_RESAMPLE_LIMIT:
        return None

    num_iter = _read_num_iter_for_history(doc)
    param_count = len(_read_params(_read_spsa(doc)))
    period = _target_history_period(num_iter=num_iter, param_count=param_count)
    if period <= 0 or current_target <= 0:
        return []

    resolved_iters = _resolve_history_sample_iters(
        doc,
        history,
        tolerance=DEFAULT_ITER_TOLERANCE,
    )
    if any(
        isinstance(sample, list) and sample and not _iter_sample_is_iter_only(sample)
        for sample in history
    ):
        resolved_iters = _integerize_resolved_history_iters(
            doc,
            resolved_iters,
            tolerance=DEFAULT_ITER_TOLERANCE,
        )
    iter_samples: list[tuple[float, list[dict[str, Any]]]] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if not sample:
            continue

        sample_iter = resolved_iters[sample_index - 1]
        if sample_iter is None:
            raise ValueError(f"history sample {sample_index} did not resolve to iter")
        normalized_sample: list[dict[str, Any]] = []
        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping",
                )
            sample_param_dict = dict(sample_param)
            normalized_sample.append(
                {
                    "theta": sample_param_dict.get("theta"),
                    "iter": sample_iter,
                }
            )
        iter_samples.append((sample_iter, normalized_sample))

    iter_samples.sort(key=lambda item: item[0])
    resampled: list[list[dict[str, Any]]] = []
    for sample_iter, sample in iter_samples:
        threshold = period * (len(resampled) + 1)
        if sample_iter + DEFAULT_ITER_TOLERANCE >= threshold:
            resampled.append(sample)
        if len(resampled) >= current_target:
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
        "start_time": 1,
        "args.num_games": 1,
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
        "start_time": 1,
        "args.num_games": 1,
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


def _print_mutation_stats(
    action: str,
    stats: MutationStats,
    *,
    show_all_errors: bool = False,
) -> None:
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
        errors_to_print = (
            stats.errors if show_all_errors else stats.errors[:DEFAULT_PREVIEW_COUNT]
        )
        for error in errors_to_print:
            print(f"- {error}")
        if not show_all_errors and len(stats.errors) > DEFAULT_PREVIEW_COUNT:
            print(f"- ... and {len(stats.errors) - DEFAULT_PREVIEW_COUNT} more")


def _print_transform_summary(transform: HistoryTransform) -> None:
    roundtrip_stats = getattr(transform, "roundtrip_stats", None)
    if not isinstance(roundtrip_stats, CRoundTripStats):
        return

    print()
    print("c(iter) round-trip validation:")
    print(f"Checked stored c values: {roundtrip_stats.checked_values}")
    print(f"Runs with mismatch: {roundtrip_stats.mismatch_runs}")
    print(f"Mismatched stored c values: {roundtrip_stats.mismatched_values}")
    if roundtrip_stats.checked_values > 0:
        print(f"Max abs error: {roundtrip_stats.max_abs_error:.6g}")
        print(f"Max rel error: {roundtrip_stats.max_rel_error:.6g}")
    if roundtrip_stats.previews:
        print("Mismatch previews:")
        for preview in roundtrip_stats.previews:
            print(f"- {preview}")

    r_stats = getattr(transform, "r_stats", None)
    if isinstance(r_stats, RRoundTripStats):
        print()
        print("R(iter) round-trip validation:")
        print(f"Checked stored R values: {r_stats.checked_values}")
        print(f"Runs with mismatch: {r_stats.mismatch_runs}")
        print(f"Mismatched stored R values: {r_stats.mismatched_values}")
        if r_stats.checked_values > 0:
            print(f"Max abs error: {r_stats.max_abs_error:.6g}")
            print(f"Max rel error: {r_stats.max_rel_error:.6g}")
        if r_stats.previews:
            print("Mismatch previews:")
            for preview in r_stats.previews:
                print(f"- {preview}")

    chart_stats = getattr(transform, "chart_stats", None)
    if not isinstance(chart_stats, ChartEquivalenceStats):
        return

    print()
    print("legacy chart equivalence validation:")
    print(f"Compared chart rows: {chart_stats.checked_rows}")
    print(f"Runs with mismatch: {chart_stats.mismatch_runs}")
    print(f"Mismatched chart rows: {chart_stats.mismatched_rows}")
    if chart_stats.checked_rows > 0:
        print(f"Max iter_ratio error: {chart_stats.max_iter_ratio_error:.6g}")
        print(f"Max chart value error: {chart_stats.max_value_error:.6g}")
    if chart_stats.previews:
        print("Mismatch previews:")
        for preview in chart_stats.previews:
            print(f"- {preview}")


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
        if not args.write:
            _print_mutation_stats(action, stats, show_all_errors=True)
            _print_transform_summary(transform)
            print()
            print("Dry run only. No writes applied.")
            if stats.errors:
                print("Fix or filter the listed runs before re-running with --write.")
            else:
                print("Re-run with --write to apply this mutation.")
            return 0

        _print_mutation_stats(action, stats)
        _print_transform_summary(transform)
        if stats.errors:
            print()
            print("Refusing to apply mutation while validation errors are present.")
            return 1

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


def _history_shape(doc: Document) -> str:
    history = _read_param_history(doc)
    non_empty_samples = [
        sample for sample in history if isinstance(sample, list) and sample
    ]
    if not non_empty_samples:
        return "empty"

    has_iter = False
    has_c = False
    has_r = False
    mixed = False
    for sample_index, sample in enumerate(non_empty_samples, start=1):
        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping"
                )
            entry_has_iter = "iter" in sample_param
            entry_has_c = "c" in sample_param
            entry_has_r = "R" in sample_param
            has_iter = has_iter or entry_has_iter
            has_c = has_c or entry_has_c
            has_r = has_r or entry_has_r
            if entry_has_iter and (entry_has_c or entry_has_r):
                mixed = True

    if mixed:
        return "mixed"
    if has_iter and not has_c and not has_r:
        return "theta-iter"
    if has_c and has_r and not has_iter:
        return "theta-R-c"
    if has_c and not has_r and not has_iter:
        return "theta-c"
    return "mixed"


def _history_field_vectors(
    doc: Document,
    *,
    field_name: str,
) -> list[tuple[float, ...]]:
    history = _read_param_history(doc)
    vectors: list[tuple[float, ...]] = []
    for sample_index, sample in enumerate(history, start=1):
        if not isinstance(sample, list):
            raise ValueError(f"history sample {sample_index} is not a list")
        if not sample:
            continue

        vector: list[float] = []
        for entry_index, sample_param in enumerate(sample, start=1):
            if not isinstance(sample_param, Mapping):
                raise ValueError(
                    f"history sample {sample_index} entry {entry_index} is not a mapping"
                )
            sample_param_dict = dict(sample_param)
            value = _as_finite_float(sample_param_dict.get(field_name))
            if value is None:
                return []
            vector.append(value)

        if vector:
            vectors.append(tuple(vector))

    return vectors


def _history_vectors_match(
    left: Sequence[float],
    right: Sequence[float],
    *,
    tolerance: float,
) -> bool:
    return len(left) == len(right) and all(
        isclose(left_value, right_value, rel_tol=tolerance, abs_tol=tolerance)
        for left_value, right_value in zip(left, right, strict=False)
    )


def _history_field_is_constant(
    doc: Document,
    *,
    field_name: str,
    tolerance: float,
) -> bool:
    vectors = _history_field_vectors(doc, field_name=field_name)
    if len(vectors) < 2:
        return False

    first_vector = vectors[0]
    return all(
        _history_vectors_match(first_vector, vector, tolerance=tolerance)
        for vector in vectors[1:]
    )


def _collect_constant_history_rows(
    collection: Collection[Document],
    query: Document,
    *,
    limit: int | None,
    tolerance: float,
) -> tuple[int, list[list[object]], list[str]]:
    projection = {
        "start_time": 1,
        "args.num_games": 1,
        "args.spsa": 1,
    }
    scanned = 0
    rows: list[list[object]] = []
    errors: list[str] = []
    for doc in _find_runs(collection, query, projection=projection, limit=limit):
        scanned += 1
        run_date = _format_run_date(doc.get("start_time"))
        run_label = _format_run_label(doc.get("_id", "<unknown>"), run_date)
        try:
            constant_c = _history_field_is_constant(
                doc,
                field_name="c",
                tolerance=tolerance,
            )
            constant_r = _history_field_is_constant(
                doc,
                field_name="R",
                tolerance=tolerance,
            )
            if not constant_c and not constant_r:
                continue

            spsa = _read_spsa(doc)
            flags: list[str] = []
            if constant_c:
                flags.append("c")
            if constant_r:
                flags.append("R")

            history = _read_param_history(doc)
            non_empty_samples = sum(
                1 for sample in history if isinstance(sample, list) and len(sample) > 0
            )
            rows.append(
                [
                    doc.get("_id"),
                    run_date,
                    ",".join(flags),
                    _history_shape(doc),
                    len(history),
                    non_empty_samples,
                    len(_read_params(spsa)),
                    _as_finite_float(spsa.get("gamma")),
                    _as_finite_float(spsa.get("alpha")),
                ]
            )
        except ValueError as error:
            errors.append(f"{run_label}: {error}")

    return scanned, rows, errors


def _serialize_c_roundtrip_check(check: CRoundTripCheck) -> Document:
    return {
        "checked_values": check.checked_values,
        "mismatched_values": check.mismatched_values,
        "max_abs_error": check.max_abs_error,
        "max_rel_error": check.max_rel_error,
        "first_mismatch": check.first_mismatch,
    }


def _serialize_r_roundtrip_check(check: RRoundTripCheck) -> Document:
    return {
        "checked_values": check.checked_values,
        "mismatched_values": check.mismatched_values,
        "max_abs_error": check.max_abs_error,
        "max_rel_error": check.max_rel_error,
        "first_mismatch": check.first_mismatch,
    }


def _serialize_chart_roundtrip_check(check: ChartEquivalenceCheck) -> Document:
    return {
        "checked_rows": check.checked_rows,
        "mismatched_rows": check.mismatched_rows,
        "max_iter_ratio_error": check.max_iter_ratio_error,
        "max_value_error": check.max_value_error,
        "first_mismatch": check.first_mismatch,
    }


def _read_stage_history_length(doc: Document) -> int:
    try:
        return len(_read_param_history(doc))
    except ValueError:
        return 0


def _build_stage_base_doc(
    doc: Document,
    *,
    kind: str,
    source_collection: str,
) -> Document:
    args = doc.get("args")
    if not isinstance(args, Mapping):
        raise ValueError("missing args mapping")

    spsa = _read_spsa(doc)
    return {
        "_id": doc.get("_id"),
        "start_time": _read_run_start_time(doc),
        "args": {
            "username": args.get("username"),
            "tc": args.get("tc"),
            "num_games": args.get("num_games"),
            "spsa": deepcopy({str(key): value for key, value in spsa.items()}),
        },
        "stage": {
            "kind": kind,
            "source_collection": source_collection,
            "source_history_shape": _history_shape(doc),
            "source_history_len": len(_read_param_history(doc)),
            "source_param_count": len(_read_params(spsa)),
        },
    }


def _build_spsa_orig_stage(
    doc: Document,
    *,
    source_collection: str,
) -> StageBuildResult:
    stage_doc = _build_stage_base_doc(
        doc,
        kind=DEFAULT_ORIG_COLLECTION,
        source_collection=source_collection,
    )
    stage_doc["stage"]["status"] = "snapshot"
    return StageBuildResult(stage_doc=stage_doc, status="snapshot")


def _build_spsa_new_stage(
    doc: Document,
    *,
    source_collection: str,
    iter_tolerance: float,
    c_tolerance: float,
    r_tolerance: float,
    chart_tolerance: float,
) -> StageBuildResult:
    stage_doc = _build_stage_base_doc(
        doc,
        kind=DEFAULT_NEW_COLLECTION,
        source_collection=source_collection,
    )
    try:
        report = _build_history_conversion_report(
            doc,
            iter_tolerance=iter_tolerance,
            c_tolerance=c_tolerance,
            r_tolerance=r_tolerance,
            chart_tolerance=chart_tolerance,
        )
    except ValueError as error:
        stage_doc["args"]["spsa"].pop("param_history", None)
        stage_doc["stage"]["status"] = "conversion-error"
        stage_doc["stage"]["errors"] = [str(error)]
        return StageBuildResult(
            stage_doc=stage_doc,
            status="conversion-error",
            errors=[str(error)],
        )

    stage_doc["args"]["spsa"]["param_history"] = report.converted_history
    stage_doc["stage"]["converted_history_len"] = len(report.converted_history)
    stage_doc["stage"]["validation"] = {
        "c": _serialize_c_roundtrip_check(report.c_check),
        "r": _serialize_r_roundtrip_check(report.r_check),
        "chart": _serialize_chart_roundtrip_check(report.chart_check),
    }
    stage_doc["stage"]["errors"] = report.errors
    stage_doc["stage"]["warnings"] = report.warnings
    stage_doc["stage"]["status"] = "ready" if not report.errors else "validation-failed"
    return StageBuildResult(
        stage_doc=stage_doc,
        status=cast(str, stage_doc["stage"]["status"]),
        errors=report.errors,
        warnings=report.warnings,
    )


def _collect_stage_build_stats(
    collection: Collection[Document],
    query: Document,
    *,
    limit: int | None,
    builder: Callable[[Document], StageBuildResult],
) -> StageBuildStats:
    stats = StageBuildStats()
    projection = {
        "start_time": 1,
        "args.username": 1,
        "args.tc": 1,
        "args.num_games": 1,
        "args.spsa": 1,
        "stage": 1,
    }
    for doc in _find_runs(collection, query, projection=projection, limit=limit):
        stats.scanned += 1
        run_date = _format_run_date(doc.get("start_time"))
        run_label = _format_run_label(doc.get("_id", "<unknown>"), run_date)
        try:
            result = builder(doc)
        except ValueError as error:
            stats.conversion_errors += 1
            stats.errors.append(f"{run_label}: {error}")
            continue

        stats.staged += 1
        if result.status in {"snapshot", "ready"}:
            stats.ready += 1
        elif result.status == "validation-failed":
            stats.validation_failed += 1
        elif result.status == "conversion-error":
            stats.conversion_errors += 1
        else:
            stats.skipped += 1

        stats.errors.extend(f"{run_label}: {error}" for error in result.errors)
        stats.warnings.extend(f"{run_label}: {warning}" for warning in result.warnings)
        if len(stats.previews) < DEFAULT_PREVIEW_COUNT:
            stats.previews.append(
                (
                    str(doc.get("_id", "<unknown>")),
                    run_date,
                    result.status,
                    len(_read_param_history(doc)),
                    _read_stage_history_length(result.stage_doc),
                )
            )

    return stats


def _write_stage_collection(
    source_collection: Collection[Document],
    target_collection: Collection[Document],
    query: Document,
    *,
    limit: int | None,
    batch_size: int,
    builder: Callable[[Document], StageBuildResult],
) -> int:
    projection = {
        "start_time": 1,
        "args.username": 1,
        "args.tc": 1,
        "args.num_games": 1,
        "args.spsa": 1,
        "stage": 1,
    }
    operations: list[Any] = []
    written = 0
    for doc in _find_runs(source_collection, query, projection=projection, limit=limit):
        try:
            result = builder(doc)
        except ValueError:
            continue
        operations.append(
            ReplaceOne({"_id": doc["_id"]}, result.stage_doc, upsert=True)
        )
        if len(operations) >= batch_size:
            target_collection.bulk_write(operations, ordered=False)
            written += len(operations)
            operations.clear()

    if operations:
        target_collection.bulk_write(operations, ordered=False)
        written += len(operations)
    return written


def _print_stage_build_stats(
    action: str,
    stats: StageBuildStats,
    *,
    show_all_errors: bool = False,
) -> None:
    print(f"Action: {action}")
    print(f"Scanned: {stats.scanned}")
    print(f"Stage docs: {stats.staged}")
    print(f"Ready: {stats.ready}")
    print(f"Validation failed: {stats.validation_failed}")
    print(f"Conversion errors: {stats.conversion_errors}")
    print(f"Skipped: {stats.skipped}")
    print(f"Warnings: {len(stats.warnings)}")
    if stats.previews:
        print()
        _print_table(
            ["run_id", "date", "status", "history_before", "history_after"],
            [list(preview) for preview in stats.previews],
        )
    if stats.errors:
        print()
        print("Assertions:")
        errors_to_print = (
            stats.errors if show_all_errors else stats.errors[:DEFAULT_PREVIEW_COUNT]
        )
        for error in errors_to_print:
            print(f"- {error}")
        if not show_all_errors and len(stats.errors) > DEFAULT_PREVIEW_COUNT:
            print(f"- ... and {len(stats.errors) - DEFAULT_PREVIEW_COUNT} more")
    if stats.warnings:
        print()
        print("Warnings:")
        warnings_to_print = (
            stats.warnings
            if show_all_errors
            else stats.warnings[:DEFAULT_PREVIEW_COUNT]
        )
        for warning in warnings_to_print:
            print(f"- {warning}")
        if not show_all_errors and len(stats.warnings) > DEFAULT_PREVIEW_COUNT:
            print(f"- ... and {len(stats.warnings) - DEFAULT_PREVIEW_COUNT} more")


def _read_stage_status(doc: Document) -> str:
    stage = doc.get("stage")
    if not isinstance(stage, Mapping):
        return "ready"
    status = stage.get("status")
    return str(status) if isinstance(status, str) else "ready"


def _read_stage_errors(doc: Document) -> list[str]:
    stage = doc.get("stage")
    if not isinstance(stage, Mapping):
        return []
    errors = stage.get("errors")
    if not isinstance(errors, list):
        return []
    return [str(error) for error in errors]


def _read_stage_history_for_apply(
    doc: Document,
    *,
    allow_validation_errors: bool,
) -> list[list[dict[str, Any]]]:
    status = _read_stage_status(doc)
    if (
        status in {"validation-failed", "conversion-error"}
        and not allow_validation_errors
    ):
        stage_errors = _read_stage_errors(doc)
        detail = stage_errors[0] if stage_errors else f"stage status is {status}"
        raise ValueError(detail)
    return _read_param_history(doc)


def _collect_apply_stage_stats(
    collection: Collection[Document],
    query: Document,
    *,
    limit: int | None,
    allow_validation_errors: bool,
) -> ApplyStageStats:
    stats = ApplyStageStats()
    projection = {
        "stage": 1,
        "args.spsa": 1,
    }
    for doc in _find_runs(collection, query, projection=projection, limit=limit):
        stats.scanned += 1
        run_id = str(doc.get("_id", "<unknown>"))
        try:
            history = _read_stage_history_for_apply(
                doc,
                allow_validation_errors=allow_validation_errors,
            )
        except ValueError as error:
            stats.skipped += 1
            stats.errors.append(f"{run_id}: {error}")
            continue

        stats.ready += 1
        if len(stats.previews) < DEFAULT_PREVIEW_COUNT:
            stats.previews.append((run_id, _read_stage_status(doc), len(history)))

    return stats


def _apply_stage_history(
    source_collection: Collection[Document],
    target_collection: Collection[Document],
    query: Document,
    *,
    limit: int | None,
    batch_size: int,
    allow_validation_errors: bool,
) -> int:
    projection = {
        "stage": 1,
        "args.spsa": 1,
    }
    operations: list[Any] = []
    written = 0
    for doc in _find_runs(source_collection, query, projection=projection, limit=limit):
        try:
            history = _read_stage_history_for_apply(
                doc,
                allow_validation_errors=allow_validation_errors,
            )
        except ValueError:
            continue

        operations.append(
            UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {"args.spsa.param_history": history}},
            )
        )
        if len(operations) >= batch_size:
            target_collection.bulk_write(operations, ordered=False)
            written += len(operations)
            operations.clear()

    if operations:
        target_collection.bulk_write(operations, ordered=False)
        written += len(operations)
    return written


def _print_apply_stage_stats(
    stats: ApplyStageStats, *, show_all_errors: bool = False
) -> None:
    print("Action: apply staged SPSA history to runs")
    print(f"Scanned: {stats.scanned}")
    print(f"Ready: {stats.ready}")
    print(f"Skipped: {stats.skipped}")
    if stats.previews:
        print()
        _print_table(
            ["run_id", "status", "history"],
            [list(preview) for preview in stats.previews],
        )
    if stats.errors:
        print()
        print("Stage errors:")
        errors_to_print = (
            stats.errors if show_all_errors else stats.errors[:DEFAULT_PREVIEW_COUNT]
        )
        for error in errors_to_print:
            print(f"- {error}")
        if not show_all_errors and len(stats.errors) > DEFAULT_PREVIEW_COUNT:
            print(f"- ... and {len(stats.errors) - DEFAULT_PREVIEW_COUNT} more")


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


def main_stage_original_history(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy original SPSA history snapshots into spsa_orig"
    )
    _add_mutation_args(parser)
    parser.add_argument(
        "--target-collection",
        default=DEFAULT_ORIG_COLLECTION,
        help="Destination collection for the original SPSA snapshot",
    )
    parser.add_argument(
        "--drop-target",
        action="store_true",
        help="Drop the destination collection before copying",
    )
    args = parser.parse_args(argv)
    query = _build_spsa_query(args)
    action = f"stage original SPSA history in {args.target_collection}"
    with _connect(args) as client:
        db = client[args.db]
        source = _runs_collection(client, args)
        target = db[args.target_collection]

        def builder(doc: Document) -> StageBuildResult:
            return _build_spsa_orig_stage(
                doc,
                source_collection=args.collection,
            )

        stats = _collect_stage_build_stats(
            source,
            query,
            limit=args.limit,
            builder=builder,
        )
        _print_stage_build_stats(action, stats, show_all_errors=True)
        if not args.write:
            print()
            print("Dry run only. No stage docs written.")
            print("Re-run with --write to create or refresh spsa_orig.")
            return 1 if stats.conversion_errors > 0 else 0

        if args.drop_target:
            target.drop()

        written = _write_stage_collection(
            source,
            target,
            query,
            limit=args.limit,
            batch_size=args.batch_size,
            builder=builder,
        )
        print()
        print(f"Wrote {written} stage docs to {args.target_collection}.")
    return 1 if stats.conversion_errors > 0 else 0


def main_stage_converted_history(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build iter-only staged SPSA history docs in spsa_new"
    )
    _add_mutation_args(parser)
    parser.set_defaults(collection=DEFAULT_ORIG_COLLECTION)
    parser.add_argument(
        "--target-collection",
        default=DEFAULT_NEW_COLLECTION,
        help="Destination collection for the iter-only SPSA stage",
    )
    parser.add_argument(
        "--drop-target",
        action="store_true",
        help="Drop the destination collection before writing",
    )
    parser.add_argument(
        "--iter-tolerance",
        type=_nonnegative_float,
        default=DEFAULT_ITER_TOLERANCE,
        help="Maximum allowed recovery error before a sample is rejected",
    )
    parser.add_argument(
        "--c-tolerance",
        type=_nonnegative_float,
        default=DEFAULT_C_TOLERANCE,
        help="Maximum absolute/relative c(iter) error tolerated after conversion",
    )
    parser.add_argument(
        "--r-tolerance",
        type=_nonnegative_float,
        default=DEFAULT_R_TOLERANCE,
        help="Maximum absolute/relative R(iter) error tolerated after conversion",
    )
    parser.add_argument(
        "--chart-tolerance",
        type=_nonnegative_float,
        default=DEFAULT_CHART_TOLERANCE,
        help="Maximum absolute/relative chart payload error tolerated after conversion",
    )
    args = parser.parse_args(argv)
    query = _build_spsa_query(args)
    action = f"stage converted SPSA history in {args.target_collection}"
    with _connect(args) as client:
        db = client[args.db]
        source = _runs_collection(client, args)
        target = db[args.target_collection]

        def builder(doc: Document) -> StageBuildResult:
            return _build_spsa_new_stage(
                doc,
                source_collection=args.collection,
                iter_tolerance=args.iter_tolerance,
                c_tolerance=args.c_tolerance,
                r_tolerance=args.r_tolerance,
                chart_tolerance=args.chart_tolerance,
            )

        stats = _collect_stage_build_stats(
            source,
            query,
            limit=args.limit,
            builder=builder,
        )
        _print_stage_build_stats(action, stats, show_all_errors=True)
        if not args.write:
            print()
            print("Dry run only. No stage docs written.")
            print("Re-run with --write to create or refresh spsa_new.")
            return (
                1 if (stats.validation_failed > 0 or stats.conversion_errors > 0) else 0
            )

        if args.drop_target:
            target.drop()

        written = _write_stage_collection(
            source,
            target,
            query,
            limit=args.limit,
            batch_size=args.batch_size,
            builder=builder,
        )
        print()
        print(f"Wrote {written} stage docs to {args.target_collection}.")
        if stats.validation_failed > 0 or stats.conversion_errors > 0:
            print(
                "spsa_new contains validation-failed or conversion-error docs; "
                "apply-stage refuses them unless explicitly overridden."
            )
    return 1 if (stats.validation_failed > 0 or stats.conversion_errors > 0) else 0


def main_apply_staged_history(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply param_history from a chosen staged SPSA snapshot back into runs"
    )
    _add_mutation_args(parser)
    parser.add_argument(
        "stage_kind",
        choices=("orig", "new"),
        help="Which staged history to apply: 'orig' reads spsa_orig, 'new' reads spsa_new",
    )
    parser.add_argument(
        "--allow-validation-errors",
        action="store_true",
        help="Allow applying validation-failed spsa_new docs when they still carry iter-only history",
    )
    args = parser.parse_args(argv)
    query = _build_spsa_query(args)
    source_collection = (
        DEFAULT_ORIG_COLLECTION if args.stage_kind == "orig" else DEFAULT_NEW_COLLECTION
    )
    with _connect(args) as client:
        db = client[args.db]
        source = db[source_collection]
        target = _runs_collection(client, args)
        stats = _collect_apply_stage_stats(
            source,
            query,
            limit=args.limit,
            allow_validation_errors=args.allow_validation_errors,
        )
        _print_apply_stage_stats(stats, show_all_errors=True)
        if not args.write:
            print()
            print("Dry run only. No writes applied.")
            if stats.skipped > 0:
                print(
                    "Fix or filter the listed stage docs before re-running with --write."
                )
            else:
                print("Re-run with --write to update runs from the staged history.")
            return 1 if stats.skipped > 0 else 0

        if stats.skipped > 0:
            print()
            print("Refusing to apply staged history while stage errors are present.")
            return 1

        written = _apply_stage_history(
            source,
            target,
            query,
            limit=args.limit,
            batch_size=args.batch_size,
            allow_validation_errors=args.allow_validation_errors,
        )
        print()
        print(f"Applied staged history to {written} runs.")
    return 0


def main_resample_dense_histories(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Resample only the SPSA histories whose creation-time regime exceeds "
            "the current operational limit"
        )
    )
    _add_mutation_args(parser)
    args = parser.parse_args(argv)
    return _run_history_mutation(
        args,
        action="resample dense param_history rows",
        transform=_resample_dense_history,
    )


def main_list_constant_history(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List SPSA runs whose stored param_history keeps c, R, or both "
            "constant across non-empty samples"
        )
    )
    _add_connection_args(parser)
    _add_run_filter_arg(parser)
    _add_limit_arg(parser)
    parser.add_argument(
        "--tolerance",
        type=_nonnegative_float,
        default=DEFAULT_C_TOLERANCE,
        help=(
            "Maximum absolute/relative difference tolerated when comparing "
            "history c/R vectors across samples"
        ),
    )
    args = parser.parse_args(argv)
    query = _build_spsa_query(args)
    action = "list SPSA runs with constant param_history c or R"
    with _connect(args) as client:
        collection = _runs_collection(client, args)
        scanned, rows, errors = _collect_constant_history_rows(
            collection,
            query,
            limit=args.limit,
            tolerance=args.tolerance,
        )

    constant_c_only = sum(1 for row in rows if row[2] == "c")
    constant_r_only = sum(1 for row in rows if row[2] == "R")
    constant_c_and_r = sum(1 for row in rows if row[2] == "c,R")

    print(f"Action: {action}")
    print(f"Scanned: {scanned}")
    print(f"Matched: {len(rows)}")
    print(f"Constant c only: {constant_c_only}")
    print(f"Constant R only: {constant_r_only}")
    print(f"Constant c and R: {constant_c_and_r}")
    print(f"Errors: {len(errors)}")

    if rows:
        print()
        _print_table(
            [
                "run_id",
                "date",
                "flags",
                "history_shape",
                "history_len",
                "non_empty_samples",
                "param_count",
                "gamma",
                "alpha",
            ],
            rows,
        )
    if errors:
        print()
        print("Errors:")
        for error in errors[:DEFAULT_PREVIEW_COUNT]:
            print(f"- {error}")
        if len(errors) > DEFAULT_PREVIEW_COUNT:
            print(f"- ... and {len(errors) - DEFAULT_PREVIEW_COUNT} more")

    return 1 if errors else 0


def _analyze_history_sample_iter_window(
    doc: Document,
    *,
    sample_index: int,
    radius: int,
    top: int,
    tolerance: float,
) -> dict[str, Any]:
    history = _read_param_history(doc)
    if sample_index <= 0:
        raise ValueError(f"invalid sample index: expected > 0, got {sample_index!r}")
    if sample_index > len(history):
        raise ValueError(
            f"history sample {sample_index} is out of range for history length {len(history)}"
        )

    sample = history[sample_index - 1]
    if not isinstance(sample, list):
        raise ValueError(f"history sample {sample_index} is not a list")
    if not sample:
        raise ValueError(f"history sample {sample_index} is empty")

    spsa = _read_spsa(doc)
    params = _read_params(spsa)
    resolved_iters = _resolve_history_sample_iters(
        doc,
        history,
        tolerance=tolerance,
    )
    resolved_estimate = _as_nonnegative_float_value(
        resolved_iters[sample_index - 1],
        field_name=f"resolved history sample {sample_index}.iter",
    )
    integerized_iters = _integerize_resolved_history_iters(
        doc,
        resolved_iters,
        tolerance=tolerance,
    )
    established_iter = integerized_iters[sample_index - 1]
    if established_iter is None:
        raise ValueError(f"history sample {sample_index} did not resolve to iter")

    validation_targets = _history_sample_validation_targets(
        sample,
        params,
        sample_index=sample_index,
    )
    if not validation_targets:
        raise ValueError(
            f"history sample {sample_index} has no recoverable c/R validation targets"
        )

    A = _as_optional_nonnegative_float_value(spsa.get("A"))
    alpha = _as_optional_nonnegative_float_value(spsa.get("alpha"))
    gamma = _as_nonnegative_float_value(spsa.get("gamma"), field_name="args.spsa.gamma")
    actual_iter = _read_history_terminal_iter(doc, tolerance=tolerance)
    window_lower = max(0, established_iter - radius)
    window_upper = min(actual_iter, established_iter + radius)

    rows: list[dict[str, Any]] = []
    for iter_value in range(window_lower, window_upper + 1):
        priority = _score_history_sample_iter_validation_priority(
            validation_targets,
            A=A,
            alpha=alpha,
            gamma=gamma,
            sample_iter=iter_value,
        )
        max_rel_error, total_rel_error, max_abs_error, total_abs_error = (
            _score_history_sample_iter_validation_error(
                validation_targets,
                A=A,
                alpha=alpha,
                gamma=gamma,
                sample_iter=iter_value,
            )
        )
        rows.append(
            {
                "iter": iter_value,
                "delta_from_established": iter_value - established_iter,
                "delta_from_estimate": iter_value - resolved_estimate,
                "max_rel_error": max_rel_error,
                "total_rel_error": total_rel_error,
                "max_abs_error": max_abs_error,
                "total_abs_error": total_abs_error,
                "priority": priority,
            }
        )

    rows.sort(
        key=lambda row: (
            *row["priority"],
            abs(row["delta_from_estimate"]),
            abs(row["delta_from_established"]),
            row["iter"],
        )
    )

    established_rank = next(
        index
        for index, row in enumerate(rows, start=1)
        if row["iter"] == established_iter
    )
    c_targets = sum(1 for target in validation_targets if target.stored_c is not None)
    r_targets = sum(
        1
        for target in validation_targets
        if target.stored_r is not None
        and target.base_a is not None
        and A is not None
        and alpha is not None
    )

    return {
        "resolved_estimate": resolved_estimate,
        "established_iter": established_iter,
        "window_lower": window_lower,
        "window_upper": window_upper,
        "candidate_count": len(rows),
        "established_rank": established_rank,
        "best_iter": rows[0]["iter"],
        "c_targets": c_targets,
        "r_targets": r_targets,
        "rows": rows[:top],
    }


def main_inspect_iter_window(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect nearby integer iter candidates for a single SPSA history sample "
            "and rank them by c/R reconstruction error"
        )
    )
    _add_connection_args(parser)
    _add_run_filter_arg(parser)
    parser.add_argument(
        "--sample-index",
        type=_positive_int,
        default=1,
        help="1-based param_history sample index to inspect",
    )
    parser.add_argument(
        "--radius",
        type=_positive_int,
        default=25,
        help="Search this many integer iters on each side of the established iter",
    )
    parser.add_argument(
        "--top",
        type=_positive_int,
        default=10,
        help="Show the top N candidates ranked by reconstruction error",
    )
    parser.add_argument(
        "--iter-tolerance",
        type=_nonnegative_float,
        default=DEFAULT_ITER_TOLERANCE,
        help="Tolerance used while resolving and integerizing the sample history",
    )
    args = parser.parse_args(argv)
    if args.run_id is None:
        parser.error("--run-id is required")

    query = _build_spsa_query(args)
    projection = {
        "start_time": 1,
        "args.num_games": 1,
        "args.spsa": 1,
    }
    with _connect(args) as client:
        collection = _runs_collection(client, args)
        doc = collection.find_one(query, projection=projection)

    if not isinstance(doc, dict):
        print("No matching run found.")
        return 1

    try:
        analysis = _analyze_history_sample_iter_window(
            doc,
            sample_index=args.sample_index,
            radius=args.radius,
            top=args.top,
            tolerance=args.iter_tolerance,
        )
    except ValueError as error:
        print(f"Error: {error}")
        return 1

    history_len = len(_read_param_history(doc))
    run_date = _format_run_date(doc.get("start_time"))
    run_label = _format_run_label(doc.get("_id", "<unknown>"), run_date)
    print("Action: inspect iter window around established SPSA sample iter")
    print(f"Run: {run_label}")
    print(f"Sample index: {args.sample_index}/{history_len}")
    print(f"Resolved estimate: {analysis['resolved_estimate']:.6g}")
    print(f"Established iter: {analysis['established_iter']}")
    print(f"Search window: {analysis['window_lower']}..{analysis['window_upper']}")
    print(f"Candidate count: {analysis['candidate_count']}")
    print(f"Established iter rank: {analysis['established_rank']}")
    print(f"Best iter in window: {analysis['best_iter']}")
    print(f"Stored c targets: {analysis['c_targets']}")
    print(f"Stored R targets: {analysis['r_targets']}")
    print()
    _print_table(
        [
            "iter",
            "delta_established",
            "delta_estimate",
            "max_rel_error",
            "total_rel_error",
            "max_abs_error",
            "total_abs_error",
        ],
        [
            [
                row["iter"],
                row["delta_from_established"],
                f"{row['delta_from_estimate']:.6g}",
                f"{row['max_rel_error']:.6g}",
                f"{row['total_rel_error']:.6g}",
                f"{row['max_abs_error']:.6g}",
                f"{row['total_abs_error']:.6g}",
            ]
            for row in analysis["rows"]
        ],
    )
    return 0


_COMMANDS: dict[str, Callable[[Sequence[str] | None], int]] = {
    "stage-orig": main_stage_original_history,
    "stage-new": main_stage_converted_history,
    "apply-stage": main_apply_staged_history,
    "resample-dense-histories": main_resample_dense_histories,
    "inspect-iter-window": main_inspect_iter_window,
    "list-constant-history": main_list_constant_history,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local staged SPSA param_history migration helper"
    )
    parser.add_argument("command", choices=sorted(_COMMANDS))
    args, remaining = parser.parse_known_args(argv)
    return _COMMANDS[args.command](remaining)


if __name__ == "__main__":
    raise SystemExit(main())
