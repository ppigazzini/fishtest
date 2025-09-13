# Collapsing N micro-steps into one macro update: what changes, and why

Inside a report there are N per-pair signals signal_j and per-pair gains gain_j that can vary across the block.

Definitions:
- sum_signal = sum over j of signal_j (block sum)
- mean_signal = sum_signal / N (block mean)
- mean_gain = (1/N) * sum over j of gain_j (mean gain in the block)

Replacing the N sequential micro-steps with one macro update creates two separate effects. Keeping them distinct shows what can be fixed (recoverable) and what cannot.

## Two distinct effects

1) Using the first gain for the whole block (aggregation bias)
- Shortcut: apply the block-start gain start_gain = gain(k_start) to the whole sum sum_signal.
- True accumulation uses every gain_j.
- Discrepancy:
  agg_bias = sum_j (start_gain - gain_j) * signal_j
- Recoverable: replace start_gain by mean_gain. In linear updates this fixes the aggregation bias with only block-level info.

2) Using a mean sequence instead of the real one (stochastic mismatch)
- Even after fixing gains with mean_gain, replacing the realized sequence {signal_j} by its mean mean_signal removes within-block randomness and ordering.
- Zero-mean deviation:
  seq_mismatch = sum_j (gain_j - mean_gain) * (signal_j - mean_signal)
  E[seq_mismatch] = 0
  Var(seq_mismatch) is proportional to var_signal * sum_j (gain_j - mean_gain)^2
- This is variance, not bias. Without per-step signal_j (or flatter gain_j), the sample-path difference cannot be removed using only sum_signal or mean_signal.

## What is recoverable vs not (in general)

Recoverable with block-level stats:
- Replace start_gain with mean_gain to remove the “use-first-gain” shortcut.
- This reproduces the aggregation for the constant-mean surrogate (signal_j = mean_signal) and matches a micro path built from that surrogate.
- It does not reconstruct the aggregation of the real per-step sequence sum_j gain_j * signal_j.

Not recoverable from block sums/means:
- With only sum_signal (and even with a histogram), you cannot recover sum_j gain_j * signal_j when gains vary within the block; different sequences with the same sum_signal produce different weighted sums.
- The gap to the real micro path is the zero-mean term
  seq_mismatch = sum_j (gain_j - mean_gain) * (signal_j - mean_signal),
  with variance proportional to var_signal * sum_j (gain_j - mean_gain)^2.
- For nonlinear/stateful algorithms (like Adam), curvature and temporal-order coupling add further irreducible differences beyond sequence weighting; matching means is not enough.

---

## SPSA (weighted sum with time-varying gains)

- Nature: the block update is a weighted sum, sum_j gain_j * signal_j, where gain_j = a_k / c_k depends on the within-block position k. Because gains vary across the block, the update is not determined by sum_signal alone.

Recoverable (aggregation):
- Using mean_gain per block removes the “use the first gain” shortcut bias, and reproduces the constant-mean surrogate exactly (mean-gain macro == constant-mean micro). See simul/bias-spsa.py.

Not recoverable (sequence dependence):
- Without the per-step sequence, you cannot in general reconstruct sum_j gain_j * signal_j. Two sequences with the same sum_signal (even the same histogram) give different results when gains vary within the block.

What the charts show (and why it improves over time):
- The deviation to the real micro is the zero-mean seq_mismatch term. Because gains shrink with k (textbook schedules), later contributions are weakly weighted, so order noise dampens as more pairs accrue. The corrected macro (or constant-mean micro) tracks the real micro closely and becomes more robust to shuffling in the long run.

---

## Schedule-free SGD (linear dynamics for z and x)

- States: z updates linearly in signal_j; x is a linear time-varying average of z; theta = (1 - beta1) * z + beta1 * x.

Recoverable (aggregation):
- The macro closed form equals a micro run with signal_j = mean_signal for z, x, and theta (see simul/bias-sf-sgd.py). Macro == micro(const-mean) by construction.

Why the charts show near-coincidence and robustness:
- z at report boundaries depends only on sum_signal, so it is sequence-invariant.
- x uses decaying schedule-free weights a_t ≈ 1/t; the within-block ordering noise is averaged with total “blend mass” ~ sum_{j=1..N} a_{t+j} ≈ ln((t+N)/t) ≈ N/t. As t grows, this vanishes.
- Result: macro/micro(const-mean) are very close to the real micro, and the difference shrinks over time; shuffling has negligible long-run effect.

---

## Schedule-free Adam (with online μ2 from block summaries)

Core mechanics inside a block:
- Per-step variance state: v_j = β₂ · v_{j−1} + (1 − β₂) · (signal_j)²
- Per-step scale: step_scale_j = 1 / sqrt(v̂_j + ε), with bias correction in v̂
- Per-step update (schematically): Δ_j ≈ lr · signal_j · step_scale_j

What we model (block-level only, no per-outcome access):
- Macro (fishtest-style):
  - Inputs per report: {N, s}, where s = Σ_j signal_j and N is the count.
  - Online second moment per pair is estimated before the block using exact block averages:
    - μ̂ = (Σ s_i) / (Σ N_i)
    - E_N = (Σ N_i) / K, E_s2_over_N = (Σ s_i² / N_i) / K, with K = number of reports
    - σ̂² = E_s2_over_N − μ̂² · E_N
    - μ̂2 = μ̂² + σ̂²
  - Use μ̂2 as the constant g² level for the block’s closed-form Adam v update, apply bias correction and the intra-block damping k(N, β₂), then take one step with the block sum s.
- Micro (const-mean):
  - N steps with constant numerator mean_signal = s/N and constant g² = μ̂2 (the same pre-block estimate). By construction, this path coincides with the macro.
- Micro (real):
  - N steps with the realized per-outcome signal_j and g²_j = (signal_j)² at each step.

Guarantees and behavior:
- Macro == Micro(const-mean) exactly, for any β₁, β₂, lr, and N.
- Using μ̂2 (mean of squares) aligns the normalization level with the real micro path, removing the large drift that would arise from using (mean_signal)².
- A small residual difference remains vs the real micro due to within-block sequence and convexity effects (order of |signal| interacting with the EMA and 1/√·). This term is zero-mean and typically small; it shrinks as total pairs grow and is only mildly affected by shuffling.

Key formulas (block-averaged, report-level only):
- μ̂ = Σ s_i / Σ N_i
- E_N = (Σ N_i) / K
- E_s2_over_N = (Σ s_i² / N_i) / K
- σ̂² = E_s2_over_N − μ̂² · E_N
- μ̂2 = μ̂² + σ̂²

Code anchors:
- Estimator and updates: mu2_hat, update_mu2_stats in simul/bias-sf-adam.py
- Adam closed form and intra-block damping: adam_v_closed_form, adam_k in simul/bias-sf-adam.py
- Paths: macro_update and build_const_mean_online_sequences in simul/bias-sf-adam.py

---
