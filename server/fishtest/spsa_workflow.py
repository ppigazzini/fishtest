"""Share classic SPSA lifecycle behavior across form, worker, and chart code."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from math import isclose, isfinite
from typing import Any

CLASSIC_SPSA_ALGORITHM = "classic"

_CLASSIC_SPSA_FORM_DEFAULTS = {
    "algorithm": CLASSIC_SPSA_ALGORITHM,
    "A": 0.1,
    "alpha": 0.602,
    "gamma": 0.101,
    "raw_params": "",
}
_SPSA_PARAM_FIELDS = 6


def _as_float(value: object, fallback: float | None = None) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return fallback


def _finite_float(value: object, fallback: float | None = None) -> float | None:
    number = _as_float(value)
    if number is None or not isfinite(number):
        return fallback
    return number


def _require_spsa_number(
    value: object,
    *,
    field_name: str,
    line_number: int | None = None,
    minimum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    number = _finite_float(value)
    location = (
        f"line {line_number} field {field_name}"
        if line_number is not None
        else field_name
    )
    if number is None:
        msg = f"Invalid SPSA {location}: expected a finite number"
        raise ValueError(msg)

    if minimum is None:
        return number

    if strict_minimum:
        if number <= minimum:
            msg = f"Invalid SPSA {location}: expected a finite number > {minimum:g}"
            raise ValueError(msg)
        return number

    if number < minimum:
        msg = f"Invalid SPSA {location}: expected a finite number >= {minimum:g}"
        raise ValueError(msg)

    return number


def _chart_numbers_match(left: object, right: object) -> bool:
    left_number = _finite_float(left)
    right_number = _finite_float(right)
    if left_number is None or right_number is None:
        return False

    return isclose(left_number, right_number, rel_tol=0.0, abs_tol=1e-9)


def _chart_sample_matches(
    sample: list[dict[str, float | None]] | None,
    live_sample: list[dict[str, float | None]],
) -> bool:
    if sample is None or not sample or len(sample) != len(live_sample):
        return False

    return all(
        _chart_numbers_match(sample_param.get("theta"), live_param.get("theta"))
        for sample_param, live_param in zip(sample, live_sample)
    )


def _normalize_chart_sample_iter(value: object) -> float | None:
    if isinstance(value, bool):
        return None

    number = _finite_float(value)
    if number is None or number < 0:
        return None

    rounded = round(number)
    if not isclose(number, rounded, rel_tol=0.0, abs_tol=1.0e-9):
        return None

    return float(rounded)


def _normalize_chart_history_sample(
    sample: object,
    *,
    iter_value: float,
) -> tuple[float, list[dict[str, float | None]]] | None:
    if not isinstance(sample, list):
        return None

    normalized_params: list[dict[str, float | None]] = []
    sample_iters: list[float] = []
    for sample_param in sample:
        if not isinstance(sample_param, Mapping):
            continue

        normalized_params.append({"theta": _finite_float(sample_param.get("theta"))})
        sample_iter = _normalize_chart_sample_iter(sample_param.get("iter"))
        if sample_iter is not None:
            sample_iters.append(sample_iter)

    if not normalized_params or not sample_iters:
        return None

    sample_iters.sort()
    sample_iter = sample_iters[len(sample_iters) // 2]
    if iter_value > 0:
        sample_iter = min(sample_iter, iter_value)

    return sample_iter, normalized_params


def _build_chart_sample_c_values(
    params: list[Mapping[str, Any]],
    *,
    gamma: float,
    sample_iter: float,
) -> list[float | None]:
    iter_local = sample_iter + 1.0
    c_values: list[float | None] = []
    for param in params:
        base_c = _finite_float(param.get("c"))
        sample_c = None
        if base_c is not None:
            try:
                sample_c = _finite_float(base_c / iter_local**gamma)
            except ArithmeticError, OverflowError, ValueError:
                sample_c = None
        c_values.append(sample_c)
    return c_values


def _build_spsa_chart_rows(
    params: list[Mapping[str, Any]],
    chart_history: list[tuple[float, list[dict[str, float | None]]]],
    live_point: list[dict[str, float | None]],
    *,
    gamma: float,
    iter_value: float,
    num_iter: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start_values: list[float] = []
    for param in params:
        start_value = _finite_float(
            param.get("start"),
            _finite_float(param.get("theta"), 0.0),
        )
        start_values.append(start_value if start_value is not None else 0.0)

    rows.append({"iter_ratio": 0.0, "values": start_values})

    if not chart_history:
        return rows

    has_live_point = not (
        _chart_numbers_match(chart_history[-1][0], iter_value)
        and _chart_sample_matches(chart_history[-1][1], live_point)
    )

    current_values = start_values.copy()
    for sample_iter, sample in chart_history:
        row_values: list[float] = []
        for index, current_value in enumerate(current_values):
            sample_param = sample[index] if index < len(sample) else None
            next_value = (
                _finite_float(sample_param.get("theta"), current_value)
                if sample_param is not None
                else current_value
            )
            current_values[index] = (
                next_value if next_value is not None else current_value
            )
            row_values.append(current_values[index])

        rows.append(
            {
                "iter_ratio": min(max(sample_iter / num_iter, 0.0), 1.0)
                if num_iter > 0
                else 0.0,
                "values": row_values,
                "c_values": _build_chart_sample_c_values(
                    params,
                    gamma=gamma,
                    sample_iter=sample_iter,
                ),
            }
        )

    if iter_value <= 0 or not has_live_point:
        return rows

    rows.append(
        {
            "iter_ratio": min(max(iter_value / num_iter, 0.0), 1.0)
            if num_iter > 0
            else 0.0,
            "values": [
                live_param["theta"]
                if live_param.get("theta") is not None
                else start_value
                for live_param, start_value in zip(live_point, start_values)
            ],
            "c_values": [live_param.get("c") for live_param in live_point],
        }
    )
    return rows


def _normalize_algorithm_name(name: object) -> str:
    if name == CLASSIC_SPSA_ALGORITHM:
        return CLASSIC_SPSA_ALGORITHM
    msg = f"Unknown SPSA algorithm: {name}"
    raise ValueError(msg)


def _read_spsa_algorithm_name(spsa: Mapping[str, Any] | None) -> str:
    if not isinstance(spsa, Mapping):
        return CLASSIC_SPSA_ALGORITHM

    algorithm = spsa.get("algorithm")
    if algorithm is None:
        return CLASSIC_SPSA_ALGORITHM

    return _normalize_algorithm_name(algorithm)


def clip_spsa_param_value(param: Mapping[str, Any], increment: float) -> float:
    return min(max(param["theta"] + increment, param["min"]), param["max"])


def get_spsa_history_period(*, num_iter: int | float, param_count: int) -> float:
    if num_iter <= 0 or param_count <= 0:
        return 0.0

    samples = (
        100 if param_count < 100 else 10000 / param_count if param_count < 1000 else 1
    )
    return float(num_iter) / samples


def build_spsa_form_values(
    spsa: Mapping[str, Any] | None,
    *,
    num_games: int | None = None,
) -> dict[str, Any]:
    _read_spsa_algorithm_name(spsa)

    values = dict(_CLASSIC_SPSA_FORM_DEFAULTS)
    if not isinstance(spsa, Mapping):
        return values

    values["raw_params"] = str(spsa.get("raw_params", values["raw_params"]))
    for field in ("A", "alpha", "gamma"):
        field_value = _finite_float(spsa.get(field))
        if field_value is not None:
            values[field] = field_value

    if isinstance(num_games, int) and num_games > 0:
        stored_A = _finite_float(spsa.get("A"))
        if stored_A is not None:
            values["A"] = round(1000 * 2 * stored_A / num_games) / 1000

    return values


def build_spsa_state(
    post: Mapping[str, Any],
    *,
    num_games: int,
) -> dict[str, Any]:
    algorithm_name = _normalize_algorithm_name(
        post.get("spsa_algorithm", CLASSIC_SPSA_ALGORITHM),
    )
    A_ratio = _require_spsa_number(
        post["spsa_A"],
        field_name="A ratio",
        minimum=0.0,
    )
    alpha = _require_spsa_number(
        post["spsa_alpha"],
        field_name="alpha",
        minimum=0.0,
    )
    gamma = _require_spsa_number(
        post["spsa_gamma"],
        field_name="gamma",
        minimum=0.0,
    )
    spsa: dict[str, Any] = {
        "algorithm": algorithm_name,
        "A": int(A_ratio * num_games / 2),
        "alpha": alpha,
        "gamma": gamma,
        "raw_params": str(post["spsa_raw_params"]),
        "iter": 0,
        "num_iter": num_games // 2,
    }
    spsa["params"] = parse_spsa_params(spsa)
    return spsa


def parse_spsa_params(spsa: dict[str, Any]) -> list[dict[str, Any]]:
    _read_spsa_algorithm_name(spsa)

    A = _require_spsa_number(spsa.get("A"), field_name="A", minimum=0.0)
    alpha = _require_spsa_number(
        spsa.get("alpha"),
        field_name="alpha",
        minimum=0.0,
    )
    gamma = _require_spsa_number(
        spsa.get("gamma"),
        field_name="gamma",
        minimum=0.0,
    )
    num_iter = _require_spsa_number(
        spsa.get("num_iter"),
        field_name="num_iter",
        minimum=0.0,
    )
    raw = spsa["raw_params"]
    params = []
    for line_number, line in enumerate(raw.split("\n"), start=1):
        chunks = line.strip().split(",")
        if len(chunks) == 1 and chunks[0] == "":
            continue
        if len(chunks) != _SPSA_PARAM_FIELDS:
            msg = f"the line {chunks} does not have {_SPSA_PARAM_FIELDS} entries"
            raise ValueError(msg)
        param = {
            "name": chunks[0],
            "start": _require_spsa_number(
                chunks[1],
                field_name="start",
                line_number=line_number,
            ),
            "min": _require_spsa_number(
                chunks[2],
                field_name="min",
                line_number=line_number,
            ),
            "max": _require_spsa_number(
                chunks[3],
                field_name="max",
                line_number=line_number,
            ),
            "c_end": _require_spsa_number(
                chunks[4],
                field_name="c_end",
                line_number=line_number,
                minimum=0.0,
                strict_minimum=True,
            ),
            "r_end": _require_spsa_number(
                chunks[5],
                field_name="r_end",
                line_number=line_number,
                minimum=0.0,
            ),
        }
        param["c"] = param["c_end"] * num_iter**gamma
        param["a_end"] = param["r_end"] * param["c_end"] ** 2
        param["a"] = param["a_end"] * (A + num_iter) ** alpha
        param["theta"] = param["start"]
        params.append(param)
    return params


def format_spsa_value(
    spsa: Mapping[str, Any],
    *,
    logger: logging.Logger | None = None,
    run_id: str | None = None,
) -> list[str | list[str]]:
    _read_spsa_algorithm_name(spsa)

    iter_local = spsa["iter"] + 1
    A = spsa["A"]
    alpha = spsa["alpha"]
    gamma = spsa["gamma"]
    summary = (
        f"iter: {iter_local:d}, A: {A:d}, alpha: {alpha:0.3f}, gamma: {gamma:0.3f}"
    )
    spsa_value: list[str | list[str]] = [summary]
    for param in spsa["params"]:
        try:
            c_iter = param["c"] / (iter_local**gamma)
            r_iter = param["a"] / (A + iter_local) ** alpha / c_iter**2
        except (ArithmeticError, TypeError, ValueError) as error:
            if logger is not None and run_id is not None:
                logger.warning(
                    "Invalid SPSA param state while rendering "
                    "run %s (iter=%d, param=%s): %s",
                    run_id,
                    iter_local,
                    param.get("name", "<unknown>"),
                    error,
                )
            c_iter = float("nan")
            r_iter = float("nan")
        spsa_value.append(
            [
                param["name"],
                "{:.2f}".format(param["theta"]),
                str(int(param["start"])),
                str(int(param["min"])),
                str(int(param["max"])),
                f"{c_iter:.3f}",
                "{:.3f}".format(param["c_end"]),
                f"{r_iter:.2e}",
                "{:.2e}".format(param["r_end"]),
            ],
        )
    return spsa_value


def build_spsa_worker_step(
    spsa: Mapping[str, Any],
    param: Mapping[str, Any],
    *,
    iter_value: int,
    flip: int,
) -> dict[str, Any]:
    _read_spsa_algorithm_name(spsa)

    iter_local = iter_value + 1
    c = param["c"] / iter_local ** spsa["gamma"]
    return {
        "c": c,
        "R": param["a"] / (spsa["A"] + iter_local) ** spsa["alpha"] / c**2,
        "flip": flip,
    }


def apply_spsa_result_updates(
    spsa: dict[str, Any],
    w_params: list[dict[str, Any]],
    *,
    result: int,
    game_pairs: int,
) -> None:
    _read_spsa_algorithm_name(spsa)
    del game_pairs

    if len(spsa["params"]) != len(w_params):
        msg = (
            "SPSA parameter update length mismatch: "
            f"{len(spsa['params'])} params, {len(w_params)} worker params"
        )
        raise ValueError(msg)

    for param, w_param in zip(spsa["params"], w_params):
        param["theta"] = clip_spsa_param_value(
            param,
            w_param["R"] * w_param["c"] * result * w_param["flip"],
        )


def build_spsa_chart_payload(spsa: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(spsa, Mapping):
        return {}

    _read_spsa_algorithm_name(spsa)
    iter_value = _finite_float(spsa.get("iter"), 0.0)
    iter_local = iter_value + 1.0
    gamma = _finite_float(spsa.get("gamma"), 0.0)
    num_iter = _finite_float(spsa.get("num_iter"), 0.0)

    params: list[Mapping[str, Any]] = []
    param_names: list[str] = []
    live_point: list[dict[str, float | None]] = []
    for param in spsa.get("params", []):
        if not isinstance(param, Mapping):
            continue
        params.append(param)
        param_names.append(str(param.get("name", "")))
        theta = _finite_float(param.get("theta"), 0.0)
        base_c = _finite_float(param.get("c"))
        live_c = None
        if base_c is not None:
            try:
                live_c = _finite_float(base_c / iter_local**gamma)
            except ArithmeticError, OverflowError, ValueError:
                live_c = None
        live_point.append(
            {
                "theta": theta if theta is not None else 0.0,
                "c": live_c,
            }
        )

    param_history = spsa.get("param_history")
    chart_history: list[tuple[float, list[dict[str, float | None]]]] = []
    if isinstance(param_history, list):
        for sample in param_history:
            normalized_sample = _normalize_chart_history_sample(
                sample,
                iter_value=iter_value,
            )
            if normalized_sample is not None:
                chart_history.append(normalized_sample)

    chart_history.sort(key=lambda sample: sample[0])

    return {
        "param_names": param_names,
        "chart_rows": _build_spsa_chart_rows(
            params,
            chart_history,
            live_point,
            gamma=gamma if gamma is not None else 0.0,
            iter_value=iter_value,
            num_iter=num_iter if num_iter is not None else 0.0,
        ),
    }
