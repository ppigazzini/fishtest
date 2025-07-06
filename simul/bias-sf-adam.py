# Clean-room simulation for schedule-free Adam vs micro loop, mirroring the SGD structure.
# States:
# - z: fast iterate (unclamped update state in θ-space)
# - x: Polyak surrogate (slow moving average of z via schedule-free mass)
# - theta: blended state, theta = (1 - beta1) * z + beta1 * x  (the exported value)
#
# Three paths over a shared schedule (Ns and outcomes per report):
# - Fishtest (macro): per-report closed-form v with online μ2; mass blend x with a_k
# - Micro loop (const_mean_online): N equal micro-steps; per-step v uses online μ2; per-step mass blend
# - Real micro step (outcomes): N per-outcome steps; per-step v, per-step mass blend
#
# Start from z=x=theta=0, v=0, iter_pairs=0, sf_weight_sum=0.
# Plot x, z, theta vs cumulative pairs for original vs end-adjacent shuffled order.

import math
import random
from dataclasses import dataclass
from typing import Optional, Sequence

import matplotlib.pyplot as plt

# ----- data models -----


@dataclass(slots=True)
class GlobalState:
    iter_pairs: int = 0
    sf_weight_sum: float = 0.0
    # Online μ2 estimator state (from report-level summaries only)
    # Use exact block-averaged aggregates like simul/online_stats.py
    reports: float = 0.0
    sum_N: float = 0.0
    sum_s: float = 0.0
    sum_s2_over_N: float = 0.0
    mu2_init: float = 1.0  # used only before the first report


@dataclass(slots=True)
class ParamState:
    theta: float = 0.0
    z: float = 0.0
    v: float = 0.0
    c: float = 0.5
    beta1: float = 0.9


@dataclass(slots=True)
class Update:
    x: float
    z: float
    theta: float
    v: float


@dataclass(slots=True)
class Series:
    t_pairs: list[int]
    x: list[float]
    z: list[float]
    theta: list[float]


@dataclass(slots=True)
class InitStats:
    """Precomputed initialization aggregates (virtual prior), passed into functions."""

    reports: float = 0.0
    sum_N: float = 0.0
    sum_s: float = 0.0
    sum_s2_over_N: float = 0.0


# ----- core math -----


def reconstruct_x_prev(theta_prev: float, z_prev: float, beta1: float) -> float:
    # If beta1 == 0, we won't call this; x=z is used directly.
    return (theta_prev - (1.0 - beta1) * z_prev) / beta1


def sf_weighting_update(glob: GlobalState, N: int, lr: float) -> float:
    # schedule-free mass increment
    report_weight = lr * N
    glob.sf_weight_sum += report_weight
    return report_weight / glob.sf_weight_sum if glob.sf_weight_sum > 0 else 1.0


def adam_k(N: int, beta2: float) -> float:
    # Intra-block geometric mean adjustment for Adam's denominator when using a single report step
    if not (N > 1 and 0.0 < beta2 < 1.0):
        return 1.0
    q = math.sqrt(beta2)
    tiny = 1e-12
    if abs(1.0 - q) > tiny:
        k = (1.0 - (beta2 ** (0.5 * N))) / (N * (1.0 - q))
    else:
        k = 1.0 - ((N - 1) * 0.25) * (1.0 - beta2)
    return max(min(k, 1.0), 1e-12)


def adam_v_closed_form(
    v_prev: float,
    beta2: float,
    N: int,
    g_sq_mean: float,
    micro_steps_after: int,
    eps: float,
) -> tuple[float, float]:
    # Closed form v update over N steps with constant mean g^2
    if beta2 < 1.0:
        v_new = (beta2**N) * v_prev + (1.0 - beta2**N) * g_sq_mean
        bc = 1.0 - (beta2**micro_steps_after)
        v_hat = v_new / bc if bc > 1e-16 else v_new
    else:
        v_new = v_prev
        v_hat = v_new
    denom = math.sqrt(v_hat) + eps
    return v_new, denom


# ----- online μ2 estimation (report-level only: uses N and sum s) -----


def _mu_hat(glob: GlobalState) -> float:
    # Block-average mean per pair: μ̂ = (Σ s_i) / (Σ N_i)
    return (glob.sum_s / glob.sum_N) if glob.sum_N > 0.0 else 0.0


def mu2_hat(glob: GlobalState) -> float:
    """
    Exact block-averaged estimator, using only (N, s) per report:
      E_blocks[s^2 / N] = σ^2 + μ^2 E_blocks[N]
      ⇒ σ̂^2 = E_s2_over_N - μ̂^2 E_N
      ⇒ μ̂2  = μ̂^2 + σ̂^2
    """
    # Before any reports, use the configured initial guess
    if glob.reports <= 0.0:
        return glob.mu2_init
    mu = _mu_hat(glob)
    E_s2_over_N = glob.sum_s2_over_N / glob.reports
    E_N = glob.sum_N / glob.reports
    sigma2 = E_s2_over_N - (mu * mu) * E_N
    sigma2 = max(sigma2, 0.0)  # numerical guard
    mu2 = mu * mu + sigma2
    # clamp to plausible range for outcomes in [-2..2]
    return min(max(mu2, 1e-12), 4.0)


def update_mu2_stats(glob: GlobalState, N: int, s: float) -> None:
    # Update estimator AFTER using it for the current report
    glob.reports += 1.0
    glob.sum_N += float(N)
    glob.sum_s += float(s)
    glob.sum_s2_over_N += (float(s) * float(s)) / max(float(N), 1.0)


# ----- macro + micro -----


def macro_update(
    glob: GlobalState,
    param: ParamState,
    *,
    N: int,
    result: float,
    lr: float,
    beta2: float,
    eps: float,
    use_k: bool = True,
) -> Update:
    """
    Single-report (macro) update that only depends on the block summary:
    - N: number of pairs in the report
    - result: sum of outcomes over the block
    Uses online μ2 estimated from previous reports (no per-outcome squares).
    """
    # advance time/mass
    glob.iter_pairs += N
    a_k = sf_weighting_update(glob, N, lr)

    # v via closed form: online μ2 with exact block-averaged estimator (prior to this block)
    g_sq_mean = mu2_hat(glob)
    v_new, denom_end = adam_v_closed_form(
        param.v, beta2, N, g_sq_mean, glob.iter_pairs, eps
    )

    # fast iterate
    step_phi = (lr * result) / denom_end if denom_end > 0.0 else 0.0
    if use_k:
        step_phi *= adam_k(N, beta2)
    z_new = param.z + step_phi * param.c

    # surrogate
    if param.beta1 == 0.0:
        x_new = z_new
    else:
        x_prev = reconstruct_x_prev(param.theta, param.z, param.beta1)
        x_new = (1.0 - a_k) * x_prev + a_k * z_new

    theta_new = (1.0 - param.beta1) * z_new + param.beta1 * x_new
    return Update(x=x_new, z=z_new, theta=theta_new, v=v_new)


def micro_apply_sequence(
    glob0: GlobalState,
    param0: ParamState,
    *,
    seq_num: Sequence[float],
    seq_gsq: Sequence[float],
    lr: float,
    beta2: float,
    eps: float,
) -> Update:
    # local copies for per-step evolution
    glob = GlobalState(glob0.iter_pairs, glob0.sf_weight_sum)
    z = param0.z
    v = param0.v
    if param0.beta1 == 0.0:
        x = z
    else:
        x = reconstruct_x_prev(param0.theta, z, param0.beta1)

    for num, g_sq in zip(seq_num, seq_gsq):
        glob.iter_pairs += 1
        a_k = sf_weighting_update(glob, 1, lr)
        if beta2 < 1.0:
            v = beta2 * v + (1.0 - beta2) * g_sq
            bc = 1.0 - (beta2**glob.iter_pairs)
            v_hat = v / bc if bc > 1e-16 else v
        else:
            v_hat = v
        denom = math.sqrt(v_hat) + eps
        z = z + ((lr * num) / denom) * param0.c
        if param0.beta1 != 0.0:
            x = (1.0 - a_k) * x + a_k * z

    theta = (1.0 - param0.beta1) * z + param0.beta1 * x
    return Update(x=x, z=z, theta=theta, v=v)


# ----- schedule + sequences -----


def compute_pentanomial_moments(
    p5: tuple[float, float, float, float, float],
) -> tuple[float, float, float]:
    # Values correspond to [-2, -1, 0, +1, +2]
    vals = (-2.0, -1.0, 0.0, 1.0, 2.0)
    mu = sum(p * v for p, v in zip(p5, vals))
    mu2 = sum(p * (v * v) for p, v in zip(p5, vals))
    var = mu2 - mu * mu
    return mu, mu2, var


def build_sequence(
    outcomes: Sequence[int], kind: str
) -> tuple[list[float], list[float]]:
    N = len(outcomes)
    if N == 0:
        return [], []
    s = float(sum(outcomes))
    mean = s / N
    if kind == "outcomes":
        # per-outcome numerators and per-outcome squares (for the real micro path only)
        out_sq = [float(o * o) for o in outcomes]
        return [float(o) for o in outcomes], out_sq
    raise ValueError("kind must be 'outcomes'")


def build_const_mean_online_sequences(
    outcomes_by_report: list[list[int]],
    mu2_init: float,
    init_stats: Optional[InitStats] = None,
) -> list[tuple[list[float], list[float]]]:
    """
    Build per-report constant-mean sequences using the same exact block-averaged estimator
    as OnlineReportStats in simul/online_stats.py. Uses pre-block μ2_hat and updates after.
    Seeds with externally computed InitStats (virtual prior).
    """
    seqs: list[tuple[list[float], list[float]]] = []
    # Seed local aggregates from InitStats
    reports: float = init_stats.reports if init_stats else 0.0
    sum_N: float = init_stats.sum_N if init_stats else 0.0
    sum_s: float = init_stats.sum_s if init_stats else 0.0
    sum_s2_over_N: float = init_stats.sum_s2_over_N if init_stats else 0.0

    def _mu_hat_local() -> float:
        return (sum_s / sum_N) if sum_N > 0.0 else 0.0

    def _mu2_hat_local() -> float:
        if reports <= 0.0:
            return mu2_init
        mu = _mu_hat_local()
        E_s2_over_N = sum_s2_over_N / reports
        E_N = sum_N / reports
        sigma2 = E_s2_over_N - (mu * mu) * E_N
        sigma2 = max(sigma2, 0.0)
        mu2 = mu * mu + sigma2
        return min(max(mu2, 1e-12), 4.0)

    for outs in outcomes_by_report:
        N = len(outs)
        s = float(sum(outs))
        mean = s / N if N > 0 else 0.0
        g2 = _mu2_hat_local()
        seqs.append(([mean] * N, [g2] * N))
        # update stats after using them for this block
        reports += 1.0
        sum_N += float(N)
        sum_s += float(s)
        sum_s2_over_N += (float(s) * float(s)) / max(float(N), 1.0)
    return seqs


def gen_pentanomial_outcomes(
    seed: int, N: int, p5: tuple[float, float, float, float, float]
) -> list[int]:
    rng = random.Random(seed)
    vals = [-2, -1, 0, +1, +2]
    outs = rng.choices(vals, weights=p5, k=N)
    rng.shuffle(outs)
    return outs


def make_schedule(
    num_reports: int,
    N_min: int,
    N_max: int,
    p5: tuple[float, float, float, float, float],
    base_seed: int,
) -> tuple[list[int], list[list[int]]]:
    rng = random.Random(base_seed)
    Ns = [rng.randint(N_min, N_max) for _ in range(num_reports)]
    outcomes_by_report = [
        gen_pentanomial_outcomes(base_seed + r, Ns[r], p5) for r in range(num_reports)
    ]
    return Ns, outcomes_by_report


def end_adjacent_shuffle(order: list[int], p: float, rng: random.Random) -> list[int]:
    # Single backward sweep: for pos from end→1, swap (pos,pos-1) with prob p
    idx = order.copy()
    for pos in range(len(idx) - 1, 0, -1):
        if rng.random() < p:
            idx[pos], idx[pos - 1] = idx[pos - 1], idx[pos]
    return idx


# ----- runners -----


def run_macro(
    outcomes_by_report: list[list[int]],
    *,
    lr: float,
    beta1: float,
    beta2: float,
    eps: float,
    c: float,
    mu2_init: float,
    init_stats: Optional[InitStats] = None,
) -> Series:
    # Set up global state and seed with externally computed InitStats
    glob = GlobalState(mu2_init=mu2_init)
    if init_stats:
        glob.reports = float(init_stats.reports)
        glob.sum_N = float(init_stats.sum_N)
        glob.sum_s = float(init_stats.sum_s)
        glob.sum_s2_over_N = float(init_stats.sum_s2_over_N)

    param = ParamState(beta1=beta1, c=c)

    t: list[int] = [0]
    if param.beta1 == 0.0:
        x0 = param.z
    else:
        x0 = reconstruct_x_prev(param.theta, param.z, param.beta1)
    xs: list[float] = [x0]
    zs: list[float] = [param.z]
    ths: list[float] = [param.theta]

    for outs in outcomes_by_report:
        N_block = len(outs)
        result = float(sum(outs))
        upd = macro_update(
            glob,
            param,
            N=N_block,
            result=result,
            lr=lr,
            beta2=beta2,
            eps=eps,
            use_k=True,
        )
        # After using current online μ2, update stats with this block
        update_mu2_stats(glob, N_block, result)

        param.z, param.theta, param.v = upd.z, upd.theta, upd.v
        t.append(glob.iter_pairs)
        xs.append(upd.x)
        zs.append(upd.z)
        ths.append(upd.theta)
    return Series(t_pairs=t, x=xs, z=zs, theta=ths)


def run_micro(
    seqs_by_report: list[tuple[list[float], list[float]]],
    *,
    lr: float,
    beta1: float,
    beta2: float,
    eps: float,
    c: float,
) -> Series:
    glob = GlobalState()
    param = ParamState(beta1=beta1, c=c)

    t: list[int] = [0]
    if param.beta1 == 0.0:
        x0 = param.z
    else:
        x0 = reconstruct_x_prev(param.theta, param.z, param.beta1)
    xs: list[float] = [x0]
    zs: list[float] = [param.z]
    ths: list[float] = [param.theta]

    for seq_num, seq_gsq in seqs_by_report:
        # guard against accidental mismatch
        assert len(seq_num) == len(seq_gsq), "seq_num and seq_gsq length mismatch"
        upd = micro_apply_sequence(
            glob, param, seq_num=seq_num, seq_gsq=seq_gsq, lr=lr, beta2=beta2, eps=eps
        )
        param.z, param.theta, param.v = upd.z, upd.theta, upd.v
        N_block = len(seq_num)
        glob.iter_pairs += N_block
        glob.sf_weight_sum += lr * N_block
        t.append(glob.iter_pairs)
        xs.append(upd.x)
        zs.append(upd.z)
        ths.append(upd.theta)
    return Series(t_pairs=t, x=xs, z=zs, theta=ths)


def plot_triple_overlay(
    ax: plt.Axes,
    t1: list[int],
    m1: list[float],
    me1: list[float],
    r1: list[float],
    t2: list[int],
    m2: list[float],
    me2: list[float],
    r2: list[float],
    name: str,
) -> None:
    ax.plot(t1, m1, label=f"{name} — macro (orig)", linewidth=2)
    ax.plot(t1, me1, label=f"{name} — micro mean (orig)", linestyle="--", linewidth=2)
    ax.plot(t1, r1, label=f"{name} — micro real (orig)", linestyle="-.", linewidth=2)
    ax.plot(t2, m2, label=f"{name} — macro (shuf)", linewidth=1.5, alpha=0.6)
    ax.plot(
        t2,
        me2,
        label=f"{name} — micro mean (shuf)",
        linestyle="--",
        linewidth=1.5,
        alpha=0.6,
    )
    ax.plot(
        t2,
        r2,
        label=f"{name} — micro real (shuf)",
        linestyle="-.",
        linewidth=1.5,
        alpha=0.6,
    )
    ax.set_ylabel(name)
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=2)


def plot_triple_single(
    ax: plt.Axes,
    t: list[int],
    m: list[float],
    me: list[float],
    r: list[float],
    name: str,
) -> None:
    ax.plot(t, m, label=f"{name} — macro", linewidth=2)
    ax.plot(t, me, label=f"{name} — micro mean", linestyle="--", linewidth=2)
    ax.plot(t, r, label=f"{name} — micro real", linestyle="-.", linewidth=2)
    ax.set_ylabel(name)
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=2)


# ----- main -----

if __name__ == "__main__":
    # hyper
    lr: float = 0.1
    beta1: float = 0.9
    beta2: float = 0.999
    eps: float = 1e-8
    c: float = 0.5

    # schedule (mirror SGD)
    base_seed: int = 424242
    num_reports: int = 100
    N_min, N_max = 1, 32

    # Generator pentanomial (used to draw outcomes)
    p5: tuple[float, float, float, float, float] = (0.025, 0.20, 0.55, 0.20, 0.025)

    # Initial guess for μ2 before any data arrives (only used if no init_stats and no data yet)
    mu2_init: float = 1.0

    # Optional: compute initialization stats ONCE externally (from a prior you choose)
    # Example uses a symmetric draw-heavy prior; tweak or set prior_reports=0 to disable.
    prior_p5: tuple[float, float, float, float, float] = (0.05, 0.20, 0.50, 0.20, 0.05)
    prior_reports: float = 5.0  # 0.0 disables warm start
    prior_mean_N: float = (N_min + N_max) / 2.0

    # Compute InitStats externally; only aggregates are passed below.
    def compute_init_stats_from_prior(
        p5_: tuple[float, float, float, float, float], reports_: float, mean_N_: float
    ) -> InitStats:
        if reports_ <= 0.0 or mean_N_ <= 0.0:
            return InitStats()
        mu_p, mu2_p, var_p = compute_pentanomial_moments(p5_)
        return InitStats(
            reports=reports_,
            sum_N=reports_ * mean_N_,
            sum_s=reports_ * mean_N_ * mu_p,
            sum_s2_over_N=reports_ * (var_p + mean_N_ * (mu_p * mu_p)),
        )

    init_stats = compute_init_stats_from_prior(prior_p5, prior_reports, prior_mean_N)

    # Derive schedule
    _, outcomes_by_report = make_schedule(num_reports, N_min, N_max, p5, base_seed)

    # original order
    macro = run_macro(
        outcomes_by_report,
        lr=lr,
        beta1=beta1,
        beta2=beta2,
        eps=eps,
        c=c,
        mu2_init=mu2_init,
        init_stats=init_stats,
    )
    # Build micro mean sequences with the same online μ2 logic, seeded with the same init_stats
    seqs_mean = build_const_mean_online_sequences(
        outcomes_by_report,
        mu2_init,
        init_stats=init_stats,
    )
    seqs_real = [build_sequence(outs, "outcomes") for outs in outcomes_by_report]
    micro_mean = run_micro(seqs_mean, lr=lr, beta1=beta1, beta2=beta2, eps=eps, c=c)
    micro_real = run_micro(seqs_real, lr=lr, beta1=beta1, beta2=beta2, eps=eps, c=c)

    # Figure 1: only the original schedule
    fig1, axs1 = plt.subplots(3, 1, figsize=(10, 12), sharex=True)
    plot_triple_single(axs1[0], macro.t_pairs, macro.x, micro_mean.x, micro_real.x, "x")
    plot_triple_single(axs1[1], macro.t_pairs, macro.z, micro_mean.z, micro_real.z, "z")
    plot_triple_single(
        axs1[2], macro.t_pairs, macro.theta, micro_mean.theta, micro_real.theta, "theta"
    )
    axs1[-1].set_xlabel("pairs")
    fig1.suptitle(
        "Schedule-free Adam — single schedule (x, z, theta)",
        y=0.98,
    )
    plt.tight_layout()
    plt.show()

    # Figure 2: original vs shuffled
    p_swap = 4.0 / 5.0
    idx = end_adjacent_shuffle(
        list(range(num_reports)), p=p_swap, rng=random.Random(base_seed + 1337)
    )
    outcomes_by_report_shuf = [outcomes_by_report[i] for i in idx]

    macro2 = run_macro(
        outcomes_by_report_shuf,
        lr=lr,
        beta1=beta1,
        beta2=beta2,
        eps=eps,
        c=c,
        mu2_init=mu2_init,
        init_stats=init_stats,
    )
    seqs_mean_shuf = build_const_mean_online_sequences(
        outcomes_by_report_shuf,
        mu2_init,
        init_stats=init_stats,
    )
    seqs_real_shuf = [
        build_sequence(outs, "outcomes") for outs in outcomes_by_report_shuf
    ]
    micro_mean2 = run_micro(
        seqs_mean_shuf, lr=lr, beta1=beta1, beta2=beta2, eps=eps, c=c
    )
    micro_real2 = run_micro(
        seqs_real_shuf, lr=lr, beta1=beta1, beta2=beta2, eps=eps, c=c
    )

    assert macro2.t_pairs == micro_mean2.t_pairs == micro_real2.t_pairs, (
        "time axes differ (shuffled)"
    )

    fig2, axs2 = plt.subplots(3, 1, figsize=(10, 12), sharex=True)
    plot_triple_overlay(
        axs2[0],
        macro.t_pairs,
        macro.x,
        micro_mean.x,
        micro_real.x,
        macro2.t_pairs,
        macro2.x,
        micro_mean2.x,
        micro_real2.x,
        "x",
    )
    plot_triple_overlay(
        axs2[1],
        macro.t_pairs,
        macro.z,
        micro_mean.z,
        micro_real.z,
        macro2.t_pairs,
        macro2.z,
        micro_mean2.z,
        micro_real2.z,
        "z",
    )
    plot_triple_overlay(
        axs2[2],
        macro.t_pairs,
        macro.theta,
        micro_mean.theta,
        micro_real.theta,
        macro2.t_pairs,
        macro2.theta,
        micro_mean2.theta,
        micro_real2.theta,
        "theta",
    )
    axs2[-1].set_xlabel("pairs")
    fig2.suptitle(
        "Schedule-free Adam — original vs end-adjacent shuffled (x, z, theta)",
        y=0.98,
    )
    plt.tight_layout()
    plt.show()
