import random
from dataclasses import dataclass
from typing import Tuple

# ----- exact helpers copied from simul/bias-sf-adam.py -----


def compute_pentanomial_moments(
    p5: Tuple[float, float, float, float, float],
) -> Tuple[float, float, float]:
    # Values correspond to [-2, -1, 0, +1, +2]
    vals = (-2.0, -1.0, 0.0, 1.0, 2.0)
    mu = sum(p * v for p, v in zip(p5, vals))
    mu2 = sum(p * (v * v) for p, v in zip(p5, vals))
    var = mu2 - mu * mu
    return mu, mu2, var


def gen_pentanomial_outcomes(
    seed: int, N: int, p5: Tuple[float, float, float, float, float]
) -> list[int]:
    rng = random.Random(seed)
    vals = [-2, -1, 0, +1, +2]
    outs = rng.choices(vals, weights=p5, k=N)
    rng.shuffle(outs)
    return outs


# ----- init aggregates (same math as in Adam script, but no p5 leaks into the estimator) -----


@dataclass(slots=True)
class InitStats:
    reports: float = 0.0
    sum_N: float = 0.0
    sum_s: float = 0.0
    sum_s2_over_N: float = 0.0


def compute_init_stats_from_prior(
    p5: Tuple[float, float, float, float, float],
    reports: float,
    mean_N: float,
) -> InitStats:
    if reports <= 0.0 or mean_N <= 0.0:
        return InitStats()
    mu_p, mu2_p, var_p = compute_pentanomial_moments(p5)
    return InitStats(
        reports=reports,
        sum_N=reports * mean_N,
        sum_s=reports * mean_N * mu_p,
        sum_s2_over_N=reports * (var_p + mean_N * (mu_p * mu_p)),
    )


# ----- online estimator using only (s, N) -----


class OnlineReportStats:
    """
    Online estimator using only block-level summaries (s, N) per report.
    Maintains exact block-averaged aggregates (no EMA).
    """

    def __init__(self) -> None:
        self.reports: float = 0.0
        self.sum_N: float = 0.0
        self.sum_s: float = 0.0
        self.sum_s2_over_N: float = 0.0

    def apply_init_stats(self, init: InitStats) -> None:
        # Warm-start by adding externally computed aggregates.
        if init.reports <= 0.0:
            return
        self.reports += float(init.reports)
        self.sum_N += float(init.sum_N)
        self.sum_s += float(init.sum_s)
        self.sum_s2_over_N += float(init.sum_s2_over_N)

    def update(self, s: float, N: int) -> None:
        if N <= 0:
            return
        self.reports += 1.0
        self.sum_N += float(N)
        self.sum_s += float(s)
        self.sum_s2_over_N += (float(s) * float(s)) / float(N)

    # Exact block-averaged estimates

    def mean(self) -> float:
        return (self.sum_s / self.sum_N) if self.sum_N > 0.0 else float("nan")

    def variance_block_avg(self) -> float:
        if self.reports == 0.0 or self.sum_N == 0.0:
            return float("nan")
        E_s2_over_N = self.sum_s2_over_N / self.reports
        E_N = self.sum_N / self.reports
        mu = self.mean()
        sigma2 = E_s2_over_N - (mu * mu) * E_N
        return max(sigma2, 0.0)

    def second_moment_block_avg(self) -> float:
        mu = self.mean()
        sigma2 = self.variance_block_avg()
        if mu != mu or sigma2 != sigma2:
            return float("nan")
        return mu * mu + sigma2


def main() -> None:
    # True generator pentanomial (WL domain), same as in Adam script
    p5_true: Tuple[float, float, float, float, float] = (0.025, 0.20, 0.55, 0.20, 0.025)

    # External warm-start (adjust or set reports to 0.0 to disable)
    prior_p5: Tuple[float, float, float, float, float] = (0.05, 0.20, 0.50, 0.20, 0.05)
    prior_reports: float = 10.0  # 0.0 disables
    N_min, N_max = 1, 32
    prior_mean_N: float = (N_min + N_max) / 2.0

    # Theoretical per-pair stats
    mu_th, mu2_th, var_th = compute_pentanomial_moments(p5_true)
    print("=== Theoretical per-pair statistics (from p5_true, WL domain) ===")
    print(f"Mean (μ)              : {mu_th:.6f}")
    print(f"Variance (σ^2)        : {var_th:.6f}")
    print(f"Second moment (μ2)    : {mu2_th:.6f}")
    print()

    # Build external init aggregates once
    init_stats = compute_init_stats_from_prior(prior_p5, prior_reports, prior_mean_N)

    # Print suggested μ2 init and aggregates for spsa_handler
    mu_prior, mu2_prior, var_prior = compute_pentanomial_moments(prior_p5)
    print("=== Suggested μ2 init and aggregates for spsa_handler (from prior_p5) ===")
    print(f"Prior Mean (μ_prior)        : {mu_prior:.6f}")
    print(f"Prior Variance (σ^2_prior)  : {var_prior:.6f}")
    print(f"Prior Second moment (μ2_prior = E[x^2]) : {mu2_prior:.6f}")
    print()
    print("Paste this block into your run['args']['spsa'] to seed μ2:")
    print("{")
    print(f'  "mu2_init": {mu2_prior:.12f},')
    print(f'  "mu2_reports": {init_stats.reports:.12f},')
    print(f'  "mu2_sum_N": {init_stats.sum_N:.12f},')
    print(f'  "mu2_sum_s": {init_stats.sum_s:.12f},')
    print(f'  "mu2_sum_s2_over_N": {init_stats.sum_s2_over_N:.12f}')
    print("}")
    print()

    # Simulate reports
    seed = 42
    n_reports = 1000
    rng = random.Random(seed)

    stats = OnlineReportStats()
    stats.apply_init_stats(init_stats)

    for _ in range(n_reports):
        N = rng.randint(N_min, N_max)
        outs = gen_pentanomial_outcomes(rng.randint(0, 10**9), N, p5_true)
        s = float(sum(outs))
        stats.update(s, N)

    # Exact block-averaged estimates
    mu_exact = stats.mean()
    var_exact = stats.variance_block_avg()
    mu2_exact = stats.second_moment_block_avg()

    print("=== Online estimated per-pair statistics (exact block-avg) ===")
    print(f"Mean (μ̂)             : {mu_exact:.6f}")
    print(f"Variance (σ̂^2)       : {var_exact:.6f}")
    print(f"Second moment (μ̂2)   : {mu2_exact:.6f}")


if __name__ == "__main__":
    main()
