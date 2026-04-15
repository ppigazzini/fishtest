"""Share SPSA lifecycle behavior across form, worker, detail, and chart code."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from math import isclose, isfinite
from typing import Any

CLASSIC_SPSA_ALGORITHM = "classic"
SF_SGD_SPSA_ALGORITHM = "sf-sgd"
DEFAULT_SPSA_SF_LR = 0.005
DEFAULT_SPSA_SF_BETA = 0.9
MIN_SPSA_SF_LR = 1e-8

_SPSA_SF_SGD_PARAM_FIELDS = 5
_SPSA_CLASSIC_PARAM_FIELDS = 6


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
        and _chart_numbers_match(sample_param.get("c"), live_param.get("c"))
        for sample_param, live_param in zip(sample, live_sample)
    )


def _recover_chart_sample_iter(
    sample: list[dict[str, float | None]],
    params: list[Mapping[str, Any]],
    *,
    gamma: float,
) -> float | None:
    if not isfinite(gamma) or gamma <= 0:
        return None

    recovered_iters: list[float] = []
    for param, sample_param in zip(params, sample):
        base_c = _finite_float(param.get("c"))
        sample_c = _finite_float(sample_param.get("c"))
        if base_c is None or sample_c is None or base_c <= 0 or sample_c <= 0:
            continue

        iter_local = (base_c / sample_c) ** (1.0 / gamma)
        if not isfinite(iter_local) or iter_local <= 0:
            continue

        recovered_iters.append(max(iter_local - 1.0, 0.0))

    if not recovered_iters:
        return None

    recovered_iters.sort()
    middle = len(recovered_iters) // 2
    if len(recovered_iters) % 2:
        return recovered_iters[middle]

    return (recovered_iters[middle - 1] + recovered_iters[middle]) / 2.0


def _build_master_fallback_history_iters(
    history_len: int,
    *,
    iter_value: float,
    num_iter: float,
    has_live_point: bool,
) -> list[float]:
    total_points = history_len + int(has_live_point)
    if total_points <= 0:
        return []

    final_iter_ratio = (
        min(max(iter_value / num_iter, 0.0), 1.0) if num_iter > 0 else 0.0
    )
    return [
        (index + 1) / total_points * final_iter_ratio * num_iter
        for index in range(history_len)
    ]


def _build_chart_history_iters(
    params: list[Mapping[str, Any]],
    chart_history: list[list[dict[str, float | None]]],
    *,
    gamma: float,
    iter_value: float,
    num_iter: float,
    has_live_point: bool,
) -> list[float]:
    recovered_history_iters: list[float] = []
    for sample in chart_history:
        sample_iter = _recover_chart_sample_iter(sample, params, gamma=gamma)
        if sample_iter is None:
            return _build_master_fallback_history_iters(
                len(chart_history),
                iter_value=iter_value,
                num_iter=num_iter,
                has_live_point=has_live_point,
            )

        if iter_value > 0:
            sample_iter = min(max(sample_iter, 0.0), iter_value)
        else:
            sample_iter = max(sample_iter, 0.0)

        if (
            recovered_history_iters
            and sample_iter + 1.0e-9 < recovered_history_iters[-1]
        ):
            return _build_master_fallback_history_iters(
                len(chart_history),
                iter_value=iter_value,
                num_iter=num_iter,
                has_live_point=has_live_point,
            )

        recovered_history_iters.append(sample_iter)

    return recovered_history_iters


def _build_spsa_chart_rows(
    params: list[Mapping[str, Any]],
    chart_history: list[list[dict[str, float | None]]],
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

    has_live_point = not _chart_sample_matches(chart_history[-1], live_point)
    history_iters = _build_chart_history_iters(
        params,
        chart_history,
        gamma=gamma,
        iter_value=iter_value,
        num_iter=num_iter,
        has_live_point=has_live_point,
    )

    if (
        history_iters
        and iter_value > 0
        and _chart_sample_matches(chart_history[-1], live_point)
    ):
        history_iters[-1] = iter_value

    current_values = start_values.copy()
    for sample, sample_iter in zip(chart_history, history_iters):
        row_values: list[float] = []
        c_values: list[float | None] = []
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
            c_values.append(
                _finite_float(sample_param.get("c"))
                if sample_param is not None
                else None
            )

        rows.append(
            {
                "iter_ratio": min(max(sample_iter / num_iter, 0.0), 1.0)
                if num_iter > 0
                else 0.0,
                "values": row_values,
                "c_values": c_values,
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
    if name == SF_SGD_SPSA_ALGORITHM:
        return SF_SGD_SPSA_ALGORITHM
    msg = f"Unknown SPSA algorithm: {name}"
    raise ValueError(msg)


def _is_schedule_free_sgd_state(spsa: Mapping[str, Any]) -> bool:
    return any(key in spsa for key in ("sf_lr", "sf_beta", "sf_weight_sum"))


def _read_spsa_algorithm_name(spsa: Mapping[str, Any] | None) -> str:
    if not isinstance(spsa, Mapping):
        return CLASSIC_SPSA_ALGORITHM

    algorithm = spsa.get("algorithm")
    if algorithm is None:
        return (
            SF_SGD_SPSA_ALGORITHM
            if _is_schedule_free_sgd_state(spsa)
            else CLASSIC_SPSA_ALGORITHM
        )

    return _normalize_algorithm_name(algorithm)


def _parse_finite_float(value: object, label: str) -> float:
    if not isinstance(value, (str, int, float)):
        raise TypeError

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        msg = f"{label} must be a number"
        raise ValueError(msg) from exc

    if not isfinite(number):
        msg = f"{label} must be finite"
        raise ValueError(msg)

    return number


def _parse_sf_sgd_learning_rate(value: object) -> float:
    learning_rate = _parse_finite_float(value, "SPSA learning rate")
    if not MIN_SPSA_SF_LR <= learning_rate <= 1.0:
        msg = "SPSA learning rate must be between 1e-8 and 1"
        raise ValueError(msg)
    return learning_rate


def _parse_sf_sgd_beta(value: object) -> float:
    beta = _parse_finite_float(value, "SPSA beta")
    if not 0.0 <= beta <= 1.0:
        msg = "SPSA beta must be between 0 and 1"
        raise ValueError(msg)
    return beta


def _parse_spsa_param_value(raw_value: str, *, name: str, field: str) -> float:
    return _parse_finite_float(raw_value, f"SPSA param '{name}' {field}")


def _coerce_spsa_form_float(value: object, default: float) -> float:
    if not isinstance(value, (str, int, float)):
        return default

    try:
        number = float(value)
    except TypeError, ValueError:
        return default
    return number if isfinite(number) else default


def build_spsa_form_values(args: Mapping[str, Any]) -> dict[str, str | float]:
    raw_spsa = args.get("spsa")
    spsa = raw_spsa if isinstance(raw_spsa, Mapping) else None
    if spsa is None:
        return {
            "algorithm": SF_SGD_SPSA_ALGORITHM,
            "raw_params": "",
            "sf_lr": DEFAULT_SPSA_SF_LR,
            "sf_beta": DEFAULT_SPSA_SF_BETA,
        }

    _read_spsa_algorithm_name(spsa)

    raw_params = spsa.get("raw_params", "")
    if not isinstance(raw_params, str):
        raw_params = ""

    return {
        "algorithm": SF_SGD_SPSA_ALGORITHM,
        "raw_params": raw_params,
        "sf_lr": _coerce_spsa_form_float(spsa.get("sf_lr"), DEFAULT_SPSA_SF_LR),
        "sf_beta": _coerce_spsa_form_float(
            spsa.get("sf_beta"),
            DEFAULT_SPSA_SF_BETA,
        ),
    }


def build_spsa_state(
    post: Mapping[str, Any],
    *,
    num_games: int,
) -> dict[str, Any]:
    algorithm_name = _normalize_algorithm_name(
        post.get("spsa_algorithm", SF_SGD_SPSA_ALGORITHM),
    )
    spsa: dict[str, Any] = {
        "algorithm": algorithm_name,
        "raw_params": str(post["spsa_raw_params"]),
        "iter": 0,
        "num_iter": num_games // 2,
    }

    if algorithm_name == CLASSIC_SPSA_ALGORITHM:
        A_ratio = _require_spsa_number(
            post["spsa_A"],
            field_name="A ratio",
            minimum=0.0,
        )
        spsa["A"] = int(A_ratio * num_games / 2)
        spsa["alpha"] = _require_spsa_number(
            post["spsa_alpha"],
            field_name="alpha",
            minimum=0.0,
        )
        spsa["gamma"] = _require_spsa_number(
            post["spsa_gamma"],
            field_name="gamma",
            minimum=0.0,
        )
    else:
        spsa["sf_lr"] = _parse_sf_sgd_learning_rate(
            post.get("spsa_sf_lr", str(DEFAULT_SPSA_SF_LR)),
        )
        spsa["sf_beta"] = _parse_sf_sgd_beta(
            post.get("spsa_sf_beta", str(DEFAULT_SPSA_SF_BETA)),
        )
        spsa["sf_weight_sum"] = 0.0

    spsa["params"] = parse_spsa_params(spsa)
    return spsa


def _parse_spsa_param_prefix(chunks: list[str]) -> dict[str, float | str]:
    name = chunks[0]
    if name == "":
        msg = "SPSA param name must not be empty"
        raise ValueError(msg)

    start = _parse_spsa_param_value(chunks[1], name=name, field="start")
    min_value = _parse_spsa_param_value(chunks[2], name=name, field="min")
    max_value = _parse_spsa_param_value(chunks[3], name=name, field="max")

    if min_value > max_value:
        msg = f"SPSA param '{name}' min must be <= max"
        raise ValueError(msg)
    if not min_value <= start <= max_value:
        msg = f"SPSA param '{name}' start must be within min/max"
        raise ValueError(msg)

    c_value = _parse_spsa_param_value(chunks[4], name=name, field="c")
    if c_value <= 0.0:
        msg = f"SPSA param '{name}' c must be > 0"
        raise ValueError(msg)

    return {
        "name": name,
        "start": start,
        "min": min_value,
        "max": max_value,
        "theta": start,
        "c": c_value,
    }


def _parse_classic_param_line(
    chunks: list[str],
    *,
    spsa: Mapping[str, Any],
) -> dict[str, Any]:
    if len(chunks) != _SPSA_CLASSIC_PARAM_FIELDS:
        msg = f"the line {chunks} does not have {_SPSA_CLASSIC_PARAM_FIELDS} entries"
        raise ValueError(msg)

    param = dict(_parse_spsa_param_prefix(chunks))
    c_end = float(param.pop("c"))
    r_end = _parse_spsa_param_value(chunks[5], name=str(param["name"]), field="r_end")
    if r_end < 0.0:
        msg = f"SPSA param '{param['name']}' r_end must be >= 0"
        raise ValueError(msg)

    param["c_end"] = c_end
    param["r_end"] = r_end
    param["c"] = c_end * spsa["num_iter"] ** spsa["gamma"]
    param["a_end"] = r_end * c_end**2
    param["a"] = param["a_end"] * (spsa["A"] + spsa["num_iter"]) ** spsa["alpha"]
    return param


def _parse_schedule_free_param_line(chunks: list[str]) -> dict[str, Any]:
    if len(chunks) not in (_SPSA_SF_SGD_PARAM_FIELDS, _SPSA_CLASSIC_PARAM_FIELDS):
        msg = (
            f"the line {chunks} does not have "
            f"{_SPSA_SF_SGD_PARAM_FIELDS} or {_SPSA_CLASSIC_PARAM_FIELDS} entries"
        )
        raise ValueError(msg)

    param = dict(_parse_spsa_param_prefix(chunks))
    r_end = 0.0
    if len(chunks) == _SPSA_CLASSIC_PARAM_FIELDS:
        r_end = _parse_spsa_param_value(
            chunks[5], name=str(param["name"]), field="r_end"
        )
        if r_end < 0.0:
            msg = f"SPSA param '{param['name']}' r_end must be >= 0"
            raise ValueError(msg)

    param["c_end"] = param["c"]
    param["r_end"] = r_end
    param["a_end"] = 0.0
    param["a"] = 0.0
    param["z"] = param["start"]
    param["v"] = 0.0
    return param


def parse_spsa_params(spsa: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = spsa["raw_params"]
    algorithm_name = _read_spsa_algorithm_name(spsa)
    strict_classic = algorithm_name == CLASSIC_SPSA_ALGORITHM and all(
        key in spsa for key in ("A", "alpha", "gamma")
    )

    params = []
    for line in str(raw).split("\n"):
        chunks = [chunk.strip() for chunk in line.split(",")]
        if len(chunks) == 1 and chunks[0] == "":
            continue
        if strict_classic:
            params.append(_parse_classic_param_line(chunks, spsa=spsa))
        else:
            params.append(_parse_schedule_free_param_line(chunks))
    return params


def clip_spsa_param_value(param: Mapping[str, Any], increment: float) -> float:
    return min(max(param["theta"] + increment, param["min"]), param["max"])


def get_spsa_history_period(*, num_iter: int | float, param_count: int) -> float:
    if num_iter <= 0 or param_count <= 0:
        return 0.0

    samples = (
        100 if param_count < 100 else 10000 / param_count if param_count < 1000 else 1
    )
    return float(num_iter) / samples


def format_spsa_value(
    spsa: Mapping[str, Any],
    *,
    logger: logging.Logger | None = None,
    run_id: str | None = None,
) -> list[str | list[str]]:
    algorithm_name = _read_spsa_algorithm_name(spsa)
    iter_local = spsa["iter"] + 1
    params = spsa["params"]

    if algorithm_name == SF_SGD_SPSA_ALGORITHM:
        sf_lr = spsa.get("sf_lr", DEFAULT_SPSA_SF_LR)
        sf_beta = spsa.get("sf_beta", DEFAULT_SPSA_SF_BETA)
        spsa_value: list[str | list[str]] = [
            f"iter: {iter_local:d}, lr: {sf_lr:0.5f}, beta: {sf_beta:0.3f}",
            ["param", "value", "start", "min", "max", "c"],
        ]
        spsa_value.extend(
            [
                [
                    param["name"],
                    "{:.2f}".format(param["theta"]),
                    str(int(param["start"])),
                    str(int(param["min"])),
                    str(int(param["max"])),
                    "{:.3f}".format(param["c"]),
                ]
                for param in params
            ],
        )
        return spsa_value

    A = spsa["A"]
    alpha = spsa["alpha"]
    gamma = spsa["gamma"]
    summary = (
        f"iter: {iter_local:d}, A: {A:d}, alpha: {alpha:0.3f}, gamma: {gamma:0.3f}"
    )
    spsa_value: list[str | list[str]] = [
        summary,
        ["param", "value", "start", "min", "max", "c", "c_end", "r", "r_end"],
    ]
    for param in params:
        try:
            c_iter = param["c"] / (iter_local**gamma)
            r_iter = param["a"] / (A + iter_local) ** alpha / c_iter**2
        except (ArithmeticError, TypeError, ValueError) as error:
            if logger is not None and run_id is not None:
                logger.warning(
                    "Invalid SPSA param state while rendering run %s (iter=%d, param=%s): %s",
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
    algorithm_name = _read_spsa_algorithm_name(spsa)
    if algorithm_name == SF_SGD_SPSA_ALGORITHM:
        return {"c": param["c"], "flip": flip}

    iter_local = iter_value + 1
    c_value = param["c"] / iter_local ** spsa["gamma"]
    return {
        "c": c_value,
        "R": param["a"] / (spsa["A"] + iter_local) ** spsa["alpha"] / c_value**2,
        "flip": flip,
    }


def _classic_param_update(
    param: dict[str, Any],
    w_param: Mapping[str, Any],
    result: int,
) -> float:
    param["theta"] = clip_spsa_param_value(
        param,
        w_param["R"] * w_param["c"] * result * w_param["flip"],
    )
    return param["theta"]


def _update_sf_weight_sum(
    spsa: dict[str, Any],
    *,
    game_pairs: int,
    learning_rate: float,
) -> tuple[float, float]:
    report_weight = learning_rate * game_pairs
    weight_sum_prev = spsa["sf_weight_sum"]
    weight_sum_curr = weight_sum_prev + report_weight
    spsa["sf_weight_sum"] = weight_sum_curr
    return weight_sum_prev, weight_sum_curr


def _reconstruct_schedule_free_x(
    theta_value: float,
    z_value: float,
    beta: float,
    min_value: float,
    max_value: float,
) -> float | None:
    if beta == 0.0:
        return None
    x_value = (theta_value - (1.0 - beta) * z_value) / beta
    return min(max(x_value, min_value), max_value)


def _blend_schedule_free_theta(
    z_value: float,
    x_value: float,
    beta: float,
    min_value: float,
    max_value: float,
) -> float:
    theta_value = z_value if beta == 0.0 else (1.0 - beta) * z_value + beta * x_value
    return min(max(theta_value, min_value), max_value)


def _schedule_free_history_value(
    beta: float,
    x_value: float | None,
    theta_value: float,
) -> float:
    return x_value if beta > 0.0 and x_value is not None else theta_value


def _schedule_free_param_update(
    param: dict[str, Any],
    w_param: Mapping[str, Any],
    *,
    result: int,
    game_pairs: int,
    learning_rate: float,
    beta: float,
    weight_sum_prev: float,
    weight_sum_curr: float,
) -> float:
    c_value = w_param["c"]
    flip = w_param["flip"]
    z_prev = param["z"]

    x_prev = _reconstruct_schedule_free_x(
        param["theta"],
        z_prev,
        beta,
        param["min"],
        param["max"],
    )
    delta_total_step = learning_rate * c_value * result * flip
    z_new = z_prev + delta_total_step

    if beta == 0.0:
        theta_new = _blend_schedule_free_theta(
            z_new,
            z_new,
            beta,
            param["min"],
            param["max"],
        )
        param["theta"] = theta_new
        param["z"] = z_new
        param.setdefault("v", 0.0)
        return _schedule_free_history_value(beta, None, theta_new)

    assert x_prev is not None
    report_weight = learning_rate * game_pairs
    tri_factor = (game_pairs + 1) / 2.0
    x_new = (
        weight_sum_prev * x_prev
        + report_weight * z_prev
        + learning_rate * delta_total_step * tri_factor
    ) / weight_sum_curr
    x_new = min(max(x_new, param["min"]), param["max"])
    theta_new = _blend_schedule_free_theta(
        z_new,
        x_new,
        beta,
        param["min"],
        param["max"],
    )

    param["theta"] = theta_new
    param["z"] = z_new
    param.setdefault("v", 0.0)
    return _schedule_free_history_value(beta, x_new, theta_new)


def apply_spsa_result_updates(
    spsa: dict[str, Any],
    w_params: list[dict[str, Any]],
    *,
    result: int,
    game_pairs: int,
) -> list[float]:
    algorithm_name = _read_spsa_algorithm_name(spsa)

    if len(spsa["params"]) != len(w_params):
        msg = (
            "SPSA parameter update length mismatch: "
            f"{len(spsa['params'])} params, {len(w_params)} worker params"
        )
        raise ValueError(msg)

    if algorithm_name == SF_SGD_SPSA_ALGORITHM:
        learning_rate = spsa["sf_lr"]
        beta = spsa["sf_beta"]
        weight_sum_prev, weight_sum_curr = _update_sf_weight_sum(
            spsa,
            game_pairs=game_pairs,
            learning_rate=learning_rate,
        )
        show_values = []
        for param, w_param in zip(spsa["params"], w_params):
            if "z" not in param:
                param["z"] = param["theta"]
                param["v"] = 0.0
            show_values.append(
                _schedule_free_param_update(
                    param,
                    w_param,
                    result=result,
                    game_pairs=game_pairs,
                    learning_rate=learning_rate,
                    beta=beta,
                    weight_sum_prev=weight_sum_prev,
                    weight_sum_curr=weight_sum_curr,
                ),
            )
        return show_values

    return [
        _classic_param_update(param, w_param, result)
        for param, w_param in zip(spsa["params"], w_params)
    ]


def _build_chart_live_theta(
    *,
    algorithm_name: str,
    param: Mapping[str, Any],
    spsa: Mapping[str, Any],
) -> float:
    theta_value = _finite_float(
        param.get("theta"),
        _finite_float(param.get("start"), 0.0),
    )
    if theta_value is None:
        theta_value = 0.0
    if algorithm_name == CLASSIC_SPSA_ALGORITHM:
        return theta_value

    beta = _finite_float(spsa.get("sf_beta"), DEFAULT_SPSA_SF_BETA)
    z_value = _finite_float(param.get("z"), theta_value)
    if beta is None or z_value is None:
        return theta_value

    x_value = _reconstruct_schedule_free_x(
        theta_value,
        z_value,
        beta,
        _finite_float(param.get("min"), theta_value) or theta_value,
        _finite_float(param.get("max"), theta_value) or theta_value,
    )
    return _schedule_free_history_value(beta, x_value, theta_value)


def build_spsa_chart_payload(spsa: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(spsa, Mapping):
        return {}

    algorithm_name = _read_spsa_algorithm_name(spsa)
    iter_value = _finite_float(spsa.get("iter"), 0.0) or 0.0
    num_iter = _finite_float(spsa.get("num_iter"), 0.0) or 0.0
    iter_local = iter_value + 1.0
    gamma = (
        _finite_float(spsa.get("gamma"), 0.0) or 0.0
        if algorithm_name == CLASSIC_SPSA_ALGORITHM
        else 0.0
    )

    params: list[Mapping[str, Any]] = []
    param_names: list[str] = []
    live_point: list[dict[str, float | None]] = []
    for param in spsa.get("params", []):
        if not isinstance(param, Mapping):
            continue

        normalized_param = {
            "name": str(param.get("name", "")),
            "start": _finite_float(
                param.get("start"),
                _finite_float(param.get("theta"), 0.0),
            ),
            "theta": _finite_float(
                param.get("theta"),
                _finite_float(param.get("start"), 0.0),
            ),
            "c": _finite_float(param.get("c")),
        }
        if algorithm_name == SF_SGD_SPSA_ALGORITHM:
            for field in ("min", "max", "z"):
                field_value = _finite_float(param.get(field))
                if field_value is not None:
                    normalized_param[field] = field_value
        params.append(normalized_param)
        param_names.append(normalized_param["name"])

        live_theta = _build_chart_live_theta(
            algorithm_name=algorithm_name,
            param=param,
            spsa=spsa,
        )
        base_c = _finite_float(param.get("c"))
        live_c = None
        if base_c is not None:
            if algorithm_name == CLASSIC_SPSA_ALGORITHM:
                try:
                    live_c = _finite_float(base_c / iter_local**gamma)
                except ArithmeticError, OverflowError, ValueError:
                    live_c = None
            else:
                live_c = base_c
        live_point.append({"theta": live_theta, "c": live_c})

    chart_history: list[list[dict[str, float | None]]] = []
    param_history = spsa.get("param_history")
    if isinstance(param_history, list):
        for sample in param_history:
            if not isinstance(sample, list):
                continue

            normalized_sample = []
            for sample_param in sample:
                if not isinstance(sample_param, Mapping):
                    continue
                normalized_sample.append(
                    {
                        "theta": _finite_float(sample_param.get("theta")),
                        "c": _finite_float(sample_param.get("c")),
                    },
                )

            if normalized_sample:
                chart_history.append(normalized_sample)

    return {
        "param_names": param_names,
        "chart_rows": _build_spsa_chart_rows(
            params,
            chart_history,
            live_point,
            gamma=gamma,
            iter_value=iter_value,
            num_iter=num_iter,
        ),
    }
