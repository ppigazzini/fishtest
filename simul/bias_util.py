from __future__ import annotations

import random
from dataclasses import dataclass
from math import isclose
from typing import Callable, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt

try:
    from .bias_stats import (
        compute_pentanomial_moments,
        gen_pentanomial_outcomes,
    )
except Exception:  # fallback for direct script run
    from bias_stats import (  # type: ignore
        compute_pentanomial_moments,
        gen_pentanomial_outcomes,
    )


# ----- plotting -----


@dataclass(slots=True)
class Line:
    t: Sequence[int]
    y: Sequence[float]
    label: str
    linestyle: str = "-"
    linewidth: float = 2.0
    alpha: float = 1.0


def plot_many(
    ax: plt.Axes,
    *lines: Line,
    y_label: Optional[str] = None,
    legend_ncol: int = 2,
) -> None:
    for ln in lines:
        ax.plot(
            ln.t,
            ln.y,
            label=ln.label,
            linestyle=ln.linestyle,
            linewidth=ln.linewidth,
            alpha=ln.alpha,
        )
    if y_label:
        ax.set_ylabel(y_label)
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=legend_ncol)


# ----- schedules + sequences -----


def make_schedule(
    num_reports: int,
    N_min: int,
    N_max: int,
    p5: Tuple[float, float, float, float, float],
    base_seed: int,
    *,
    outcome_fn: Callable[
        [int, int, Tuple[float, float, float, float, float]], List[int]
    ] = gen_pentanomial_outcomes,
) -> Tuple[List[int], List[List[int]]]:
    """Create a list of N per report and the corresponding outcomes with a local RNG."""
    rng = random.Random(base_seed)
    Ns = [rng.randint(N_min, N_max) for _ in range(num_reports)]
    outcomes_by_report = [
        outcome_fn(base_seed + r, Ns[r], p5) for r in range(num_reports)
    ]
    return Ns, outcomes_by_report


def end_adjacent_shuffle(order: List[int], p: float, rng: random.Random) -> List[int]:
    """Single backward sweep: for pos from end→1, swap (pos,pos-1) with probability p."""
    idx = order.copy()
    for pos in range(len(idx) - 1, 0, -1):
        if rng.random() < p:
            idx[pos], idx[pos - 1] = idx[pos - 1], idx[pos]
    return idx


def build_sequence(outcomes: Sequence[int], kind: str) -> List[float]:
    """
    Generic sequence builder for SPSA/SGD:
      - 'outcomes': per-outcome values
      - 'const_mean': N copies of the block mean
    """
    N = len(outcomes)
    if N == 0:
        return []
    s = float(sum(outcomes))
    mean = s / N
    if kind == "outcomes":
        return [float(o) for o in outcomes]
    if kind == "const_mean":
        return [mean] * N
    raise ValueError("kind must be 'outcomes' or 'const_mean'")


# ----- small utilities -----


def series_allclose(
    a: Sequence[float], b: Sequence[float], rel: float = 1e-12, abs_tol: float = 1e-12
) -> bool:
    return all(isclose(x, y, rel_tol=rel, abs_tol=abs_tol) for x, y in zip(a, b))


def compute_A_from_outcomes(
    outcomes_by_report: Sequence[Sequence[int]], frac: float = 0.1
) -> float:
    """SPSA convenience: A = frac * total_pairs based on realized block lengths."""
    total_pairs = float(sum(len(outs) for outs in outcomes_by_report))
    return frac * total_pairs


__all__ = [
    # plotting
    "Line",
    "plot_many",
    # schedules + sequences
    "make_schedule",
    "end_adjacent_shuffle",
    "build_sequence",
    # utils
    "series_allclose",
    "compute_A_from_outcomes",
    # re-exports
    "compute_pentanomial_moments",
    "gen_pentanomial_outcomes",
]
