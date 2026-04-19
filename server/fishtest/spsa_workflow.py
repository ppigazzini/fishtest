"""Share classic SPSA lifecycle behavior across form, worker, and chart code."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from math import floor, isclose, isfinite, log
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
        and _chart_numbers_match(sample_param.get("c"), live_param.get("c"))
        for sample_param, live_param in zip(sample, live_sample)
    )


def _normalize_chart_sample_iter(value: object) -> float | None:
    if isinstance(value, bool):
        return None

    number = _finite_float(value)
    if number is None or number <= 0:
        return None

    return number


def _normalize_chart_sample_c(value: object) -> float | None:
    number = _finite_float(value)
    if number is None or number <= 0:
        return None
    return number


def _normalize_chart_sample_r(value: object) -> float | None:
    number = _finite_float(value)
    if number is None or number < 0:
        return None
    return number


def _recover_chart_sample_iter_from_c(
    sample: list[dict[str, float | None]],
    params: Sequence[Mapping[str, Any]],
    *,
    gamma: float,
) -> float | None:
    if not isfinite(gamma) or gamma <= 0:
        return None

    sample_iters: list[float] = []
    for sample_param, param in zip(sample, params):
        sample_c = _normalize_chart_sample_c(sample_param.get("c"))
        base_c = _normalize_chart_sample_c(param.get("c"))
        if sample_c is None or base_c is None:
            continue

        try:
            sample_iter = _finite_float((base_c / sample_c) ** (1.0 / gamma) - 1.0)
        except ArithmeticError, OverflowError, ValueError, ZeroDivisionError:
            continue

        if sample_iter is None or sample_iter < 0:
            continue
        sample_iters.append(sample_iter)

    if not sample_iters:
        return None

    sample_iters.sort()
    return sample_iters[len(sample_iters) // 2]


def _recover_chart_sample_iter_from_r_target(
    *,
    stored_r: float,
    base_a: float,
    base_c: float,
    A: float,
    alpha: float,
    gamma: float,
    seed: float,
) -> float | None:
    if stored_r <= 0 or base_a <= 0 or base_c <= 0 or alpha <= 0:
        return None

    if gamma == 0:
        try:
            iter_local = float((base_a / (stored_r * base_c**2)) ** (1.0 / alpha) - A)
        except ArithmeticError, OverflowError, ValueError, ZeroDivisionError:
            return None
        sample_iter = _finite_float(iter_local - 1.0)
        if sample_iter is None or sample_iter < 0:
            return None
        return sample_iter

    iter_local = max(seed + 1.0, 1.0)
    for _ in range(24):
        sample_iter = iter_local - 1.0
        try:
            sample_c = float(base_c / iter_local**gamma)
            recomputed_r = float(base_a / (A + iter_local) ** alpha / sample_c**2)
        except ArithmeticError, OverflowError, ValueError, ZeroDivisionError:
            return None

        if recomputed_r <= 0 or not isfinite(recomputed_r):
            return None

        log_error = log(recomputed_r / stored_r)
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

    sample_iter = _finite_float(iter_local - 1.0)
    if sample_iter is None or sample_iter < 0:
        return None
    return sample_iter


def _recover_chart_sample_iter_from_r(
    sample: list[dict[str, float | None]],
    params: Sequence[Mapping[str, Any]],
    *,
    A: float | None,
    alpha: float | None,
    gamma: float,
    seed: float,
) -> float | None:
    if A is None or alpha is None or not isfinite(alpha) or alpha <= 0:
        return None

    sample_iters: list[float] = []
    for sample_param, param in zip(sample, params):
        stored_r = _normalize_chart_sample_r(sample_param.get("R"))
        base_c = _normalize_chart_sample_c(param.get("c"))
        base_a = _finite_float(param.get("a"))
        if stored_r is None or stored_r <= 0 or base_c is None or base_a is None:
            continue

        sample_iter = _recover_chart_sample_iter_from_r_target(
            stored_r=stored_r,
            base_a=base_a,
            base_c=base_c,
            A=A,
            alpha=alpha,
            gamma=gamma,
            seed=seed,
        )
        if sample_iter is None or sample_iter < 0:
            continue
        sample_iters.append(sample_iter)

    if not sample_iters:
        return None

    sample_iters.sort()
    return sample_iters[len(sample_iters) // 2]


def _chart_history_field_is_constant(
    chart_history: Sequence[list[dict[str, float | None]]],
    *,
    field_name: str,
) -> bool:
    vectors: list[list[float | None]] = []
    for sample in chart_history:
        vector = [sample_param.get(field_name) for sample_param in sample]
        if vector:
            vectors.append(vector)

    if len(vectors) < 2:
        return False

    first_vector = vectors[0]
    for vector in vectors[1:]:
        if len(vector) != len(first_vector):
            return False
        for left_value, right_value in zip(first_vector, vector, strict=False):
            if left_value is None and right_value is None:
                continue
            if not _chart_numbers_match(left_value, right_value):
                return False

    return True


def _score_chart_sample_iter_validation_error(
    sample: list[dict[str, float | None]],
    params: Sequence[Mapping[str, Any]],
    *,
    A: float | None,
    alpha: float | None,
    gamma: float,
    sample_iter: float,
) -> tuple[float, float, float, float]:
    c_max_rel_error = 0.0
    c_total_rel_error = 0.0
    r_max_rel_error = 0.0
    r_total_rel_error = 0.0

    iter_local = sample_iter + 1.0
    if iter_local <= 0:
        return (float("inf"),) * 4

    for sample_param, param in zip(sample, params):
        stored_c = _normalize_chart_sample_c(sample_param.get("c"))
        base_c = _normalize_chart_sample_c(param.get("c"))
        if stored_c is not None and base_c is not None:
            try:
                recomputed_c = float(base_c / iter_local**gamma)
            except ArithmeticError, OverflowError, ValueError, ZeroDivisionError:
                return (float("inf"),) * 4
            abs_error = abs(recomputed_c - stored_c)
            rel_denominator = max(abs(recomputed_c), abs(stored_c), 1.0e-300)
            rel_error = abs_error / rel_denominator
            c_max_rel_error = max(c_max_rel_error, rel_error)
            c_total_rel_error += rel_error

        stored_r = _normalize_chart_sample_r(sample_param.get("R"))
        base_a = _finite_float(param.get("a"))
        if (
            stored_r is None
            or stored_r <= 0
            or base_c is None
            or base_a is None
            or A is None
            or alpha is None
            or alpha <= 0
        ):
            continue

        try:
            sample_c = float(base_c / iter_local**gamma)
            recomputed_r = float(base_a / (A + iter_local) ** alpha / sample_c**2)
        except ArithmeticError, OverflowError, ValueError, ZeroDivisionError:
            return (float("inf"),) * 4
        abs_error = abs(recomputed_r - stored_r)
        rel_denominator = max(abs(recomputed_r), abs(stored_r), 1.0e-300)
        rel_error = abs_error / rel_denominator
        r_max_rel_error = max(r_max_rel_error, rel_error)
        r_total_rel_error += rel_error

    return (
        c_max_rel_error,
        c_total_rel_error,
        r_max_rel_error,
        r_total_rel_error,
    )


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


def _interpolate_legacy_history_iters(
    recovered_history_iters: Sequence[float | None],
    fallback_history_iters: Sequence[float],
) -> list[float]:
    if not recovered_history_iters:
        return []

    history_len = len(recovered_history_iters)
    if history_len != len(fallback_history_iters):
        return list(fallback_history_iters)

    monotone_anchors: list[float | None] = [None] * history_len
    last_anchor: float | None = None
    for index, sample_iter in enumerate(recovered_history_iters):
        if sample_iter is None:
            continue
        if last_anchor is not None and sample_iter + 1.0e-9 < last_anchor:
            continue
        monotone_anchors[index] = sample_iter
        last_anchor = sample_iter

    anchor_indexes = [
        index
        for index, sample_iter in enumerate(monotone_anchors)
        if sample_iter is not None
    ]
    if not anchor_indexes:
        return list(fallback_history_iters)

    interpolated = [0.0] * history_len

    first_anchor_index = anchor_indexes[0]
    first_anchor_iter = monotone_anchors[first_anchor_index]
    assert first_anchor_iter is not None
    for index in range(first_anchor_index):
        interpolated[index] = (
            first_anchor_iter * float(index + 1) / float(first_anchor_index + 1)
        )
    interpolated[first_anchor_index] = first_anchor_iter

    for left_index, right_index in zip(anchor_indexes, anchor_indexes[1:]):
        left_iter = monotone_anchors[left_index]
        right_iter = monotone_anchors[right_index]
        assert left_iter is not None
        assert right_iter is not None
        interpolated[left_index] = left_iter
        gap = right_index - left_index
        for offset in range(1, gap):
            weight = float(offset) / float(gap)
            interpolated[left_index + offset] = left_iter + weight * (
                right_iter - left_iter
            )
        interpolated[right_index] = right_iter

    last_anchor_index = anchor_indexes[-1]
    last_anchor_iter = monotone_anchors[last_anchor_index]
    assert last_anchor_iter is not None
    interpolated[last_anchor_index] = last_anchor_iter
    if last_anchor_index < history_len - 1:
        tail_target = max(last_anchor_iter, fallback_history_iters[-1])
        tail_gap = history_len - last_anchor_index - 1
        for offset in range(1, tail_gap + 1):
            weight = float(offset) / float(tail_gap + 1)
            interpolated[last_anchor_index + offset] = last_anchor_iter + weight * (
                tail_target - last_anchor_iter
            )

    return interpolated


def _estimate_chart_sample_iter(
    *,
    sample_index: int,
    total_samples: int,
    num_iter: float,
    param_count: int,
    iter_value: float,
) -> float:
    period = get_spsa_history_period(num_iter=num_iter, param_count=param_count)
    if period > 0:
        sample_iter = float(sample_index) * period
    elif iter_value > 0 and total_samples > 0:
        sample_iter = iter_value * sample_index / total_samples
    else:
        sample_iter = float(sample_index)

    if iter_value > 0:
        sample_iter = min(sample_iter, iter_value)
    return sample_iter


def _normalize_spsa_history_row(
    sample: object,
) -> tuple[list[dict[str, float | None]], list[float]] | None:
    if not isinstance(sample, list):
        return None

    normalized_params: list[dict[str, float | None]] = []
    sample_iters: list[float] = []
    for sample_param in sample:
        if not isinstance(sample_param, Mapping):
            continue

        normalized_params.append(
            {
                "theta": _finite_float(sample_param.get("theta")),
                "c": _normalize_chart_sample_c(sample_param.get("c")),
                "R": _normalize_chart_sample_r(sample_param.get("R")),
            }
        )
        sample_iter = _normalize_chart_sample_iter(sample_param.get("iter"))
        if sample_iter is not None:
            sample_iters.append(sample_iter)

    if not normalized_params:
        return None

    return normalized_params, sample_iters


def _build_spsa_live_point(
    params: Sequence[Mapping[str, Any]],
    *,
    gamma: float,
    iter_value: float,
) -> list[dict[str, float | None]]:
    iter_local = iter_value + 1.0
    live_point: list[dict[str, float | None]] = []
    for param in params:
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
    return live_point


def _build_legacy_chart_history_iters(
    params: Sequence[Mapping[str, Any]],
    chart_history: list[list[dict[str, float | None]]],
    *,
    A: float | None,
    alpha: float | None,
    gamma: float,
    iter_value: float,
    num_iter: float,
    has_live_point: bool,
) -> list[float]:
    fallback_history_iters = _build_master_fallback_history_iters(
        len(chart_history),
        iter_value=iter_value,
        num_iter=num_iter,
        has_live_point=has_live_point,
    )
    constant_c = _chart_history_field_is_constant(chart_history, field_name="c")
    constant_r = _chart_history_field_is_constant(chart_history, field_name="R")
    recovered_history_iters: list[float | None] = []
    for sample, fallback_iter in zip(
        chart_history, fallback_history_iters, strict=False
    ):
        c_estimate = None
        if not constant_c:
            c_estimate = _recover_chart_sample_iter_from_c(sample, params, gamma=gamma)

        r_estimate = None
        if not constant_r:
            r_estimate = _recover_chart_sample_iter_from_r(
                sample,
                params,
                A=A,
                alpha=alpha,
                gamma=gamma,
                seed=c_estimate if c_estimate is not None else fallback_iter,
            )

        estimates = [
            estimate for estimate in (c_estimate, r_estimate) if estimate is not None
        ]
        if not estimates:
            recovered_history_iters.append(None)
            continue

        sample_iter = min(
            estimates,
            key=lambda estimate: (
                _score_chart_sample_iter_validation_error(
                    sample,
                    params,
                    A=A,
                    alpha=alpha,
                    gamma=gamma,
                    sample_iter=estimate,
                )
                + (abs(estimate - fallback_iter), estimate)
            ),
        )

        if iter_value > 0:
            sample_iter = min(max(sample_iter, 0.0), iter_value)
        else:
            sample_iter = max(sample_iter, 0.0)
        recovered_history_iters.append(sample_iter)
    return _interpolate_legacy_history_iters(
        recovered_history_iters,
        fallback_history_iters,
    )


def _chart_history_uses_stored_c(
    chart_history: Sequence[tuple[float, list[dict[str, float | None]]]],
    *,
    gamma: float,
) -> bool:
    if not isfinite(gamma) or gamma <= 0:
        return True

    if not any(
        sample_param.get("c") is not None
        for _, sample in chart_history
        for sample_param in sample
    ):
        return True

    return not _chart_history_field_is_constant(
        [sample for _, sample in chart_history],
        field_name="c",
    )


def _history_uses_legacy_sample_signals(
    chart_history: Sequence[list[dict[str, float | None]]],
) -> bool:
    return any(
        sample_param.get("c") is not None or sample_param.get("R") is not None
        for sample in chart_history
        for sample_param in sample
    )


def _explicit_iters_are_rounded_master_fallback(
    explicit_history: Sequence[tuple[float, list[dict[str, float | None]]]],
    fallback_history_iters: Sequence[float],
) -> bool:
    if len(explicit_history) != len(fallback_history_iters):
        return False

    return all(
        abs(sample_iter - fallback_iter) <= 1.0
        for (sample_iter, _), fallback_iter in zip(
            explicit_history,
            fallback_history_iters,
            strict=False,
        )
    )


def _canonicalize_chart_sample_iter_for_c(
    sample_iter: float,
    *,
    use_stored_sample_c: bool,
) -> float:
    if use_stored_sample_c:
        return sample_iter
    return float(int(floor(sample_iter + 0.5)))


def resolve_spsa_history_samples(
    param_history: object,
    *,
    params: Sequence[Mapping[str, Any]],
    A: float | None = None,
    alpha: float | None = None,
    gamma: float,
    num_iter: float,
    iter_value: float,
) -> list[tuple[float, list[dict[str, float | None]]]]:
    if not isinstance(param_history, list):
        return []

    normalized_history: list[list[dict[str, float | None]]] = []
    explicit_history: list[tuple[float, list[dict[str, float | None]]]] = []
    has_legacy_history = False
    for sample in param_history:
        normalized_sample = _normalize_spsa_history_row(sample)
        if normalized_sample is None:
            continue

        normalized_params, sample_iters = normalized_sample
        normalized_history.append(normalized_params)
        if sample_iters:
            sample_iters.sort()
            sample_iter = sample_iters[len(sample_iters) // 2]
            if iter_value > 0:
                sample_iter = min(sample_iter, iter_value)
            explicit_history.append((sample_iter, normalized_params))
        else:
            has_legacy_history = True

    if not normalized_history:
        return []

    if not has_legacy_history and len(explicit_history) == len(normalized_history):
        if not _history_uses_legacy_sample_signals(normalized_history):
            live_point = _build_spsa_live_point(
                params,
                gamma=gamma if gamma is not None else 0.0,
                iter_value=iter_value,
            )
            has_live_point = not _chart_sample_matches(
                normalized_history[-1],
                live_point,
            )
            fallback_history_iters = _build_master_fallback_history_iters(
                len(normalized_history),
                iter_value=iter_value,
                num_iter=num_iter,
                has_live_point=has_live_point,
            )
            if _explicit_iters_are_rounded_master_fallback(
                explicit_history,
                fallback_history_iters,
            ):
                return list(
                    zip(
                        fallback_history_iters,
                        normalized_history,
                        strict=False,
                    )
                )

        explicit_history.sort(key=lambda sample: sample[0])
        return explicit_history

    live_point = _build_spsa_live_point(
        params,
        gamma=gamma if gamma is not None else 0.0,
        iter_value=iter_value,
    )
    has_live_point = not _chart_sample_matches(normalized_history[-1], live_point)
    history_iters = _build_legacy_chart_history_iters(
        params,
        normalized_history,
        A=A,
        alpha=alpha,
        gamma=gamma if gamma is not None else 0.0,
        iter_value=iter_value,
        num_iter=num_iter,
        has_live_point=has_live_point,
    )
    return list(zip(history_iters, normalized_history, strict=False))


def normalize_spsa_history_sample(
    sample: object,
    *,
    params: Sequence[Mapping[str, Any]],
    A: float | None = None,
    alpha: float | None = None,
    gamma: float,
    num_iter: float,
    iter_value: float,
    sample_index: int,
    total_samples: int,
) -> tuple[float, list[dict[str, float | None]]] | None:
    normalized_sample = _normalize_spsa_history_row(sample)
    if normalized_sample is None:
        return None

    normalized_params, sample_iters = normalized_sample

    if sample_iters:
        sample_iters.sort()
        sample_iter = sample_iters[len(sample_iters) // 2]
    else:
        fallback_iter = _estimate_chart_sample_iter(
            sample_index=sample_index,
            total_samples=total_samples,
            num_iter=num_iter,
            param_count=len(params),
            iter_value=iter_value,
        )
        sample_iter = _recover_chart_sample_iter_from_c(
            normalized_params,
            params,
            gamma=gamma,
        )
        if sample_iter is None:
            sample_iter = _recover_chart_sample_iter_from_r(
                normalized_params,
                params,
                A=A,
                alpha=alpha,
                gamma=gamma,
                seed=fallback_iter,
            )
        if sample_iter is None:
            sample_iter = fallback_iter

    if iter_value > 0:
        sample_iter = min(sample_iter, iter_value)

    return sample_iter, normalized_params


def _build_chart_sample_c_values(
    params: list[Mapping[str, Any]],
    sample: list[dict[str, float | None]],
    *,
    gamma: float,
    sample_iter: float,
    use_stored_sample_c: bool,
) -> list[float | None]:
    iter_local = (
        _canonicalize_chart_sample_iter_for_c(
            sample_iter,
            use_stored_sample_c=use_stored_sample_c,
        )
        + 1.0
    )
    c_values: list[float | None] = []
    for index, param in enumerate(params):
        sample_param = sample[index] if index < len(sample) else None
        sample_c = sample_param.get("c") if sample_param is not None else None
        if use_stored_sample_c and sample_c is not None:
            c_values.append(sample_c)
            continue

        base_c = _finite_float(param.get("c"))
        sample_c = None
        if base_c is not None:
            try:
                sample_c = _finite_float(base_c / iter_local**gamma)
            except ArithmeticError, OverflowError, ValueError:
                sample_c = None
        c_values.append(sample_c)
    return c_values


def _build_effective_chart_sample(
    params: list[Mapping[str, Any]],
    sample: list[dict[str, float | None]],
    *,
    gamma: float,
    sample_iter: float,
    use_stored_sample_c: bool,
) -> list[dict[str, float | None]]:
    c_values = _build_chart_sample_c_values(
        params,
        sample,
        gamma=gamma,
        sample_iter=sample_iter,
        use_stored_sample_c=use_stored_sample_c,
    )
    effective_sample: list[dict[str, float | None]] = []
    for index, param in enumerate(params):
        del param
        sample_param = sample[index] if index < len(sample) else None
        theta = sample_param.get("theta") if sample_param is not None else None
        effective_sample.append(
            {
                "theta": _finite_float(theta),
                "c": c_values[index] if index < len(c_values) else None,
            }
        )
    return effective_sample


def _build_spsa_chart_rows(
    params: list[Mapping[str, Any]],
    chart_history: list[tuple[float, list[dict[str, float | None]]]],
    live_point: list[dict[str, float | None]],
    *,
    gamma: float,
    iter_value: float,
    num_iter: float,
    use_stored_sample_c: bool,
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
        and _chart_sample_matches(
            _build_effective_chart_sample(
                params,
                chart_history[-1][1],
                gamma=gamma,
                sample_iter=chart_history[-1][0],
                use_stored_sample_c=use_stored_sample_c,
            ),
            live_point,
        )
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
                    sample,
                    gamma=gamma,
                    sample_iter=sample_iter,
                    use_stored_sample_c=use_stored_sample_c,
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
    A = _finite_float(spsa.get("A"))
    alpha = _finite_float(spsa.get("alpha"))
    gamma = _finite_float(spsa.get("gamma"), 0.0)
    num_iter = _finite_float(spsa.get("num_iter"), 0.0)

    params: list[Mapping[str, Any]] = []
    param_names: list[str] = []
    for param in spsa.get("params", []):
        if not isinstance(param, Mapping):
            continue
        params.append(param)
        param_names.append(str(param.get("name", "")))
    live_point = _build_spsa_live_point(
        params,
        gamma=gamma if gamma is not None else 0.0,
        iter_value=iter_value,
    )

    param_history = spsa.get("param_history")
    chart_history = resolve_spsa_history_samples(
        param_history,
        params=params,
        A=A,
        alpha=alpha,
        gamma=gamma if gamma is not None else 0.0,
        num_iter=num_iter if num_iter is not None else 0.0,
        iter_value=iter_value,
    )

    chart_history.sort(key=lambda sample: sample[0])
    use_stored_sample_c = _chart_history_uses_stored_c(
        chart_history,
        gamma=gamma if gamma is not None else 0.0,
    )
    if chart_history and _chart_sample_matches(
        _build_effective_chart_sample(
            params,
            chart_history[-1][1],
            gamma=gamma if gamma is not None else 0.0,
            sample_iter=chart_history[-1][0],
            use_stored_sample_c=use_stored_sample_c,
        ),
        live_point,
    ):
        last_iter, last_sample = chart_history[-1]
        chart_history[-1] = (max(last_iter, iter_value), last_sample)

    return {
        "param_names": param_names,
        "chart_rows": _build_spsa_chart_rows(
            params,
            chart_history,
            live_point,
            gamma=gamma if gamma is not None else 0.0,
            iter_value=iter_value,
            num_iter=num_iter if num_iter is not None else 0.0,
            use_stored_sample_c=use_stored_sample_c,
        ),
    }
