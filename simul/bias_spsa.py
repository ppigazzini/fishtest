# SPSA simulation refactored to mimic the schedule-free SGD script structure.
# We compare:
# - Macro (uncorrected): one shot per report using g(k0) at the block start
# - Macro (corrected): one shot using mean per-pair gain Ḡ = (1/N) Σ a_k/c_k
# - Micro (const-mean): sequential, distributing result/N across N steps
# - Micro (real): sequential, using the actual outcomes sequence
#
# Plot overlay: original order vs end-adjacent shuffled order (same as SGD).
# Assertions: corrected macro == micro const-mean (for both orders).
# ruff: noqa: I001

import random
from dataclasses import dataclass
from typing import Sequence

import matplotlib.pyplot as plt

try:
    from .bias_util import (
        Line,
        plot_many,
        make_schedule,
        end_adjacent_shuffle,
        build_sequence,
        series_allclose,
        compute_A_from_outcomes,
    )
except Exception:  # direct run
    from bias_util import (  # type: ignore
        Line,
        plot_many,
        make_schedule,
        end_adjacent_shuffle,
        build_sequence,
        series_allclose,
        compute_A_from_outcomes,
    )

# ----- data models -----


@dataclass(slots=True)
class GlobalState:
    iter_pairs: int = 0  # cumulative pairs processed


@dataclass(slots=True)
class SpsaSchedule:
    a: float
    A: float
    alpha: float
    c: float
    gamma: float


@dataclass(slots=True)
class Series:
    t_pairs: list[int]
    theta: list[float]


# ----- core math -----


def a_k(schedule: SpsaSchedule, k: int) -> float:
    return schedule.a / ((schedule.A + k) ** schedule.alpha)


def c_k(schedule: SpsaSchedule, k: int) -> float:
    return schedule.c / (k**schedule.gamma)


def gain(schedule: SpsaSchedule, k: int) -> float:
    # g(k) = a_k / c_k
    ak = a_k(schedule, k)
    ck = c_k(schedule, k)
    return ak / ck if ck != 0.0 else 0.0


def mean_gain_over_block(schedule: SpsaSchedule, k0: int, N: int) -> float:
    if N <= 0:
        return 0.0
    return sum(gain(schedule, k0 + j) for j in range(N)) / N


def macro_update_uncorrected(
    glob: GlobalState, theta: float, *, outcomes: Sequence[int], sched: SpsaSchedule
) -> float:
    # Uncorrected: use g(k0) for the whole block
    N = len(outcomes)
    if N == 0:
        return theta
    k0 = glob.iter_pairs + 1
    g0 = gain(sched, k0)
    result = float(sum(outcomes))
    theta = theta + g0 * result
    glob.iter_pairs += N
    return theta


def macro_update_corrected(
    glob: GlobalState, theta: float, *, outcomes: Sequence[int], sched: SpsaSchedule
) -> float:
    # Corrected: use mean per-pair gain Ḡ across the block
    N = len(outcomes)
    if N == 0:
        return theta
    k0 = glob.iter_pairs + 1
    g_bar = mean_gain_over_block(sched, k0, N)
    result = float(sum(outcomes))
    theta = theta + g_bar * result
    glob.iter_pairs += N
    return theta


def micro_apply_sequence(
    glob0: GlobalState, theta0: float, *, seq_num: Sequence[float], sched: SpsaSchedule
) -> float:
    # True per-pair sequential updates (local copy of glob for per-step k)
    glob = GlobalState(glob0.iter_pairs)
    theta = theta0
    for num in seq_num:
        k = glob.iter_pairs + 1
        theta = theta + gain(sched, k) * float(num)
        glob.iter_pairs += 1
    return theta


# ----- runners -----


def run_macro_uncorrected(
    outcomes_by_report: list[list[int]], *, sched: SpsaSchedule
) -> Series:
    glob = GlobalState()
    theta = 0.0
    t: list[int] = [0]
    th: list[float] = [theta]
    for outs in outcomes_by_report:
        theta = macro_update_uncorrected(glob, theta, outcomes=outs, sched=sched)
        t.append(glob.iter_pairs)
        th.append(theta)
    return Series(t_pairs=t, theta=th)


def run_macro_corrected(
    outcomes_by_report: list[list[int]], *, sched: SpsaSchedule
) -> Series:
    glob = GlobalState()
    theta = 0.0
    t: list[int] = [0]
    th: list[float] = [theta]
    for outs in outcomes_by_report:
        theta = macro_update_corrected(glob, theta, outcomes=outs, sched=sched)
        t.append(glob.iter_pairs)
        th.append(theta)
    return Series(t_pairs=t, theta=th)


def run_micro(
    seqs_by_report: list[list[float]],
    *,
    sched: SpsaSchedule,
) -> Series:
    glob = GlobalState()
    theta = 0.0
    # Start at t=0 for parity with SGD/Adam
    t: list[int] = [0]
    th: list[float] = [theta]
    for seq_num in seqs_by_report:
        theta = micro_apply_sequence(glob, theta, seq_num=seq_num, sched=sched)
        # advance outer time to the end of the report (derive N from sequence)
        N_block = len(seq_num)
        glob.iter_pairs += N_block
        t.append(glob.iter_pairs)
        th.append(theta)
    return Series(t_pairs=t, theta=th)


# ----- main -----

if __name__ == "__main__":
    # schedule (mirror SGD)
    base_seed: int = 424242
    num_reports: int = 100
    N_min, N_max = 1, 32
    p5: tuple[float, float, float, float, float] = (0.025, 0.20, 0.55, 0.20, 0.025)

    # Build schedule; discard Ns to stay airtight (derive N from sequences)
    _, outcomes_by_report = make_schedule(num_reports, N_min, N_max, p5, base_seed)

    # For A we need total pairs; convenience helper from bias_util
    A_val: float = compute_A_from_outcomes(outcomes_by_report, frac=0.1)

    # Textbook SPSA params
    sched = SpsaSchedule(
        a=0.1,
        A=A_val,
        alpha=0.602,
        c=1.0,
        gamma=0.101,
    )

    # original order
    macro_cor = run_macro_corrected(outcomes_by_report, sched=sched)
    macro_unc = run_macro_uncorrected(outcomes_by_report, sched=sched)
    seqs_mean = [build_sequence(outs, "const_mean") for outs in outcomes_by_report]
    seqs_real = [build_sequence(outs, "outcomes") for outs in outcomes_by_report]
    micro_mean = run_micro(seqs_mean, sched=sched)
    micro_real = run_micro(seqs_real, sched=sched)

    # sanity: corrected macro == micro_mean exactly (by construction)
    assert (
        macro_cor.t_pairs
        == micro_mean.t_pairs
        == micro_real.t_pairs
        == macro_unc.t_pairs
    ), "time axes differ"
    assert series_allclose(macro_cor.theta, micro_mean.theta), (
        "corrected macro != micro const-mean"
    )

    # Figure 1: only the original schedule
    fig1, ax1 = plt.subplots(1, 1, figsize=(10, 6), sharex=True)
    plot_many(
        ax1,
        Line(macro_cor.t_pairs, macro_cor.theta, "theta — macro"),
        Line(
            micro_mean.t_pairs, micro_mean.theta, "theta — micro mean", linestyle="--"
        ),
        Line(
            micro_real.t_pairs, micro_real.theta, "theta — micro real", linestyle="-."
        ),
        Line(
            macro_unc.t_pairs,
            macro_unc.theta,
            "theta — macro (uncorrected)",
            linestyle=":",
            linewidth=2,
        ),
        y_label="theta",
    )
    ax1.set_xlabel("pairs")
    fig1.suptitle("SPSA — single schedule (theta)", y=0.98)
    plt.tight_layout()
    plt.show()

    # custom shuffled order (same end-adjacent scheme as SGD)
    p_swap = 4.0 / 5.0
    idx = end_adjacent_shuffle(
        list(range(num_reports)), p=p_swap, rng=random.Random(base_seed + 1337)
    )
    outcomes_by_report_shuf = [outcomes_by_report[i] for i in idx]

    macro_cor2 = run_macro_corrected(outcomes_by_report_shuf, sched=sched)
    macro_unc2 = run_macro_uncorrected(outcomes_by_report_shuf, sched=sched)
    seqs_mean_shuf = [
        build_sequence(outs, "const_mean") for outs in outcomes_by_report_shuf
    ]
    seqs_real_shuf = [
        build_sequence(outs, "outcomes") for outs in outcomes_by_report_shuf
    ]
    micro_mean2 = run_micro(seqs_mean_shuf, sched=sched)
    micro_real2 = run_micro(seqs_real_shuf, sched=sched)

    assert (
        macro_cor2.t_pairs
        == micro_mean2.t_pairs
        == micro_real2.t_pairs
        == macro_unc2.t_pairs
    ), "time axes differ (shuffled)"
    assert series_allclose(macro_cor2.theta, micro_mean2.theta), (
        "corrected macro != micro const-mean (shuffled)"
    )

    # Figure 2: original vs shuffled overlay
    fig2, ax2 = plt.subplots(1, 1, figsize=(10, 6), sharex=True)
    plot_many(
        ax2,
        # original
        Line(macro_cor.t_pairs, macro_cor.theta, "theta — macro (orig)"),
        Line(
            micro_mean.t_pairs,
            micro_mean.theta,
            "theta — micro mean (orig)",
            linestyle="--",
        ),
        Line(
            micro_real.t_pairs,
            micro_real.theta,
            "theta — micro real (orig)",
            linestyle="-.",
        ),
        Line(
            macro_unc.t_pairs,
            macro_unc.theta,
            "theta — macro unc. (orig)",
            linestyle=":",
            linewidth=2,
        ),
        # shuffled
        Line(
            macro_cor2.t_pairs,
            macro_cor2.theta,
            "theta — macro (shuf)",
            linewidth=1.5,
            alpha=0.6,
        ),
        Line(
            micro_mean2.t_pairs,
            micro_mean2.theta,
            "theta — micro mean (shuf)",
            linestyle="--",
            linewidth=1.5,
            alpha=0.6,
        ),
        Line(
            micro_real2.t_pairs,
            micro_real2.theta,
            "theta — micro real (shuf)",
            linestyle="-.",
            linewidth=1.5,
            alpha=0.6,
        ),
        Line(
            macro_unc2.t_pairs,
            macro_unc2.theta,
            "theta — macro unc. (shuf)",
            linestyle=":",
            linewidth=1.5,
            alpha=0.6,
        ),
        y_label="theta",
    )
    ax2.set_xlabel("pairs")
    fig2.suptitle("SPSA — original vs end-adjacent shuffled (theta)", y=0.98)
    plt.tight_layout()
    plt.show()
