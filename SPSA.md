# SPSA in Fishtest (lean, phi-normalized)

This document explains SPSA in Fishtest using Elo‑normalized coordinates `phi`, shows how the single learning rate `r` maps to the classic `theta`‑space schedule `a = r * c**2`, and summarizes multi‑worker arrival behavior and the arrival‑anchored fix. Equations use simple Python-style expressions.

### At a glance
- Workers play symmetric probes around current parameters: `theta ± c * Delta`.
- Each report applies one SPSA update using the total `result = wins − losses` from that report.

## Chapter 0 — Textbook SPSA (short recap)

- Notation
  - Vectors are Python-style arrays; “*” is elementwise; “@” is matrix multiply (only used when written explicitly).
  - `F(theta)` is the objective proxy (e.g., Elo/log-odds estimated from game outcomes).
  - We maximize `F` (move along `+gradient`).

- One SPSA iteration `k`
  - Draw `Delta` with independent Rademacher entries: `Delta[i] in {-1, +1}`.
  - Pick per‑axis perturbations `c_k[i]` (schedule defined below).

- Symmetric evaluations (elementwise, same scalar `deltaY`)
  - `y_plus  = F(theta_k + c_k * Delta)`      # theta_k[i] + c_k[i] * Delta[i]
  - `y_minus = F(theta_k - c_k * Delta)`
  - `deltaY = y_plus - y_minus`

- Two‑sided estimator (per `i`, first‑order, unbiased under Rademacher)
  - `g_hat[i] = (deltaY / (2 * c_k[i])) * Delta[i]`

- Update (maximize) with schedule `a_k`
  - `theta_{k+1}[i] = theta_k[i] + a_k * g_hat[i]`

- Canonical schedules (classic)
  - `a_k = a / (A + k)**alpha`
  - `c_k[i] = c_i / (k+1)**gamma`    # Fishtest evaluates the arriving report with k = K+1 to avoid k=0

### Textbook parameter tips (for reference)

- A (stability offset): `A ~= 0.1 * num_iter` (about 10% of planned pairs) to temper very early steps.
- alpha (decay of `a_k`): choose in `[0.5, 0.8]` for a moderate taper; larger `alpha` decays faster.
- gamma (decay of `c_k`): around `0.1` is common; keep `gamma` small so the finite-difference signal persists.
- Batching at fixed k: replicate pairs at the same `k` and average the two‑sided differences before updating (variance reduction).
- Binding in Fishtest: you don’t set `a` directly; the server binds `a_end = r_end * c_end**2`. These tips help interpret the classic schedules.

### Noise/SNR quick facts
- Finite‑difference signal grows linearly with `c` for small gaps; over `N` pairs: `E[result] ∝ N * c`.
- Expected step (first order): `(a_k / c_k) * E[result] ∝ a_k`.
- Step noise std: `(a_k / c_k) * sqrt(N)`; hence step SNR: `SNR ∝ c_k / sqrt(N)`.
- Implications: larger `c_k` boosts SNR; larger `N` reduces relative noise (∝ 1/√N).

## Chapter 1 — Core math: θ‑space vs φ‑space (maximize Elo)

This chapter shows the same SPSA step in two coordinate systems and why working in `phi` (Elo‑normalized) is simpler and better conditioned than working in `theta`.

### Sign convention
- We maximize `F` (Elo). Updates use a plus sign (move along +gradient).

### Setup (snapshot at dispatch)
- Let `k0` be the dispatch snapshot for this report. Define `c_i = c_i(k0)` and keep it fixed within the report.
- Define normalized coordinates and the same objective in `phi`:
  - `phi[i] = theta[i] / c_i`            # elementwise, at the k0 snapshot
  - equivalently: `theta[i] = c_i * phi[i]`
  - `G(phi) = F(theta)` with `theta = C @ phi` (conceptual; `C = diag(c_i)`)

### 1) Symmetric probes (same evaluations, two views)
- In θ‑space:
  - `theta_plus[i]  = theta[i] + c_i * Delta[i]`
  - `theta_minus[i] = theta[i] - c_i * Delta[i]`
  - `deltaY = F(theta_plus) - F(theta_minus)`
- In φ‑space (using `theta = C @ phi`):
  - `phi_plus[i]  = phi[i] + Delta[i]`
  - `phi_minus[i] = phi[i] - Delta[i]`
  - `deltaY = G(phi_plus) - G(phi_minus)`   # same scalar as above

### 2) Two‑sided gradient estimators (unbiased, first‑order)
- θ‑space (per `i`):
  - `g_theta[i] = (deltaY / (2 * c_i)) * Delta[i]`
  - `E[g_theta[i]] = dF/dtheta_i`
  - Update: `theta[i] = theta[i] + a_k * g_theta[i]`
- φ‑space (per `i`):
  - `g_phi[i] = (deltaY / 2) * Delta[i]`
  - `E[g_phi[i]] = dG/dphi_i`
  - Update: `phi[i] = phi[i] + r_k * g_phi[i]`

### 3) Practical estimators in Fishtest (use result directly)
- We use `result = wins − losses` from the sub‑batch as the finite‑difference signal.
- For small Elo gaps between probes, `result` is linearly proportional to the Elo difference (constant absorbed by schedules), so you can plug it in directly:
  - `g_theta[i] ≈ (result / (2 * c_i)) * Delta[i]`
  - `g_phi[i]   ≈ (result / 2) * Delta[i]`
- Why this works (small‑gap justification):
  - Let `d = F(theta_plus) − F(theta_minus)` be the Elo gap. Per‑game expected score:
    - `E[score] = 1 / (1 + 10**(-d/400))`
  - Define per‑game `y ∈ {+1, 0, -1}` (wins − losses). Then:
    - `E[y] = 2*E[score] − 1 = tanh((ln(10)/800) * d)`
  - For small `|d|`, `tanh(x) ≈ x`:
    - Per game: `E[y] ≈ (ln(10)/800) * d  ≈ 0.002878 * d`
    - Over `N` games: `E[result] ≈ (ln(10)/800) * N * d`
  - Draws don’t break this: `E[y] = P(win) − P(loss) = 2*(E[score] − 0.5)` exactly.
  - Conclusion: use raw `result`; the constant factor is absorbed by `a_k` (θ) or `r_k` (φ).

### 4) Exact θ ↔ φ equivalence (single equation)
- Relationships: `g_phi[i] = c_i * g_theta[i]` and `theta[i] = c_i * phi[i]`
- Map the φ‑update back to θ in one line:
  - `phi[i]   = phi[i]   + r_k * g_phi[i]`
  - `theta[i] = c_i*phi[i] + r_k * c_i * g_phi[i] = theta[i] + (r_k * c_i**2) * g_theta[i]`
  - Identify the classic schedule: `a_k = r_k * c_i**2`   # exact at the same snapshot `k0`

### 5) Why φ is the better working space
- One scalar learning rate:
  - A single `r_k` works for all parameters in φ. In θ this becomes per‑axis `a_{k,i} = r_k * c_i**2` automatically (diagonal, unit‑aware scaling).
- One c, one place:
  - The same `c_i` sets both the probe separation (`theta ± c_i * Delta_i`) and the θ step via `(r_k * c_i)`. There’s no separate “step‑c” knob.
- Better conditioning, clearer knobs:
  - φ removes unit/scale differences. You tune one `r`; per‑axis θ steps emerge as `r * c_i`.
- Clean end‑of‑run binding:
  - With user knobs (`c_end, r_end`) the server builds `a_k` so `a_end = r_end * c_end**2`. The applied θ step per report remains `r * c * result * Delta`.

### Notes (units and invariants)
- Units check: `phi` is unitless, `c_i` has θ‑units, `r_k` has inverse “result” units; θ‑step has θ‑units: `delta_theta_i = (r_k * c_i) * result * Delta[i]`.
- Symbols: This chapter uses `Delta` for conceptual flips; Chapter 3 uses `flip` for the packed/transported bits—same object, different names to match context.

## Chapter 2 — Inputs, schedules, and the θ ↔ φ transform

This chapter shows how user inputs become schedules on the server and how θ and φ relate at dispatch and arrival.

### User inputs
- Per parameter row: `name, start, min, max, c_end, r_end`
- Global: `A, alpha, gamma, num_games`  (`num_iter = num_games // 2`)

### Server‑derived schedules (per axis)

- c schedule (choose `c` so the last step hits `c_end` exactly):
  - `c = c_end * (num_iter**gamma)`
  - `c_k = c / k**gamma`  (server evaluates an arriving report with `k = K+1`)
- a schedule (tied to `r` via `a_end = r_end * c_end**2`):
  - `a_end = r_end * (c_end**2)`
  - `a = a_end * (A + num_iter)**alpha`
  - `a_k = a / (A + k)**alpha`

- Convenience variable used by the handler and history:
  - `R_k = a_k / (c_k**2)`

Note: With these bindings, near the end (`k ≈ num_iter`) you read `r_k ≈ r_end` because `a_k / c_k**2 → a_end / c_end**2`.

### The θ ↔ φ transform, step by step

1) Dispatch snapshot (save `k0 = K`, and define `iter_local = K+1`)
- Compute the perturbation used inside the sub‑batch:
  - `c_i_k0 = param.c / (iter_local**gamma)`
- Conceptual normalized coordinates at dispatch:
  - `phi[i] = theta[i] / c_i_k0`
- What the worker plays:
  - `theta_white[i] = clip(theta[i] + c_i_k0 * Delta[i])`
  - `theta_black[i] = clip(theta[i] - c_i_k0 * Delta[i])`
  - In φ: this is exactly `phi ± Delta` (unit steps), because `theta = c_i_k0 * phi` elementwise.
- Implementation note: `k0` and the packed flips are stored in the task and sent back with the report.

2) Arrival update (classic schedule form)
- Reconstruct the same `c_i_k0` using the saved `k0`; compute:
  - `a_i_k0 = param.a / (A + iter_local)**alpha`
- Apply the θ update per parameter (maximize):
  - `step_i  = (a_i_k0 / c_i_k0) * result * Delta[i]`
  - `theta[i] = clip(theta[i] + step_i)`

3) Reading the same update through φ (single `r` at the same snapshot)
- Define:
  - `r_k0 = a_i_k0 / (c_i_k0**2)`
- Then the θ step is the φ‑update mapped back:
  - `step_i = (r_k0 * c_i_k0) * result * Delta[i]`
  - `delta_theta_i = r_k0 * c_i_k0 * result * Delta[i]`   # identical to step_i above

4) End‑of‑run knobs and φ reading
- Near the end (`k ≈ num_iter`): `r_k ≈ r_end` because `a_k / c_k**2 → a_end / c_end**2`.
- This is why we bind `a_end = r_end * c_end**2`: one scalar `r_end` in φ implies per‑axis `a_end,i` in θ.

Implementation notes
- Indexing: for arrival‑anchored averaging over an arrival span, use `j = 1..N` so the first pair aligns with `iter_local = K + 1`.

5) Optional arrival‑anchored replacement (see Chapter 4)
- To conserve schedule “mass” across overlapping reports, replace `a_i_k0` by the average `a_i` over the arrival span `(K+1 .. K+N)` before dividing by `c_i_k0`. Geometry is unchanged because `c_i_k0` is the same one engines used.

### Summary

- Normalize at dispatch: `phi = theta / c(k0)`; probes are `phi ± Delta`.
- Update at arrival: `theta += (a/c) * result * Delta = (r * c) * result * Delta` with `a = r * c**2`.
- A single `r` governs all axes in φ; in θ this expresses as per‑axis `a = r * c**2` automatically.

## Chapter 3 — Server ↔ worker protocol

Dispatch (request), using global pairs counter `K`
- `iter_local = K + 1`
- For each parameter `i`:
  - `c_i_k0 = param.c / (iter_local**gamma)`
  - `flip[i] = choice([-1, +1])`
- Return to worker:
  - `theta_white[i] = clip(theta[i] + c_i_k0 * flip[i])`
  - `theta_black[i] = clip(theta[i] - c_i_k0 * flip[i])`
- Store in task:
  - `task.spsa_params = { "iter": K, "packed_flips": pack_bits(flip) }`

Update (arrival), for a report with `num_games = 2*N`
- Reconstruct flips and `c_i_k0` using saved `k0 = task.spsa_params["iter"]`
- `result = wins - losses`
- Apply `theta` update per parameter `i` (master schedule form):
  - `a_i_k0 = param.a / (A + (k0+1))**alpha`
  - `step_i = (a_i_k0 / c_i_k0) * result * flip[i]`
  - `theta[i] = clip(theta[i] + step_i)`
- `spsa["iter"] += N`

Notes
- Multiple workers can share the same `k0`; all use the same `(a_k0, c_k0)` captured at dispatch.
- Only arrival advances `K` by `N`.

## Chapter 4 — Async realities and a simple fix

Problems in practice (brief)
- Dispatch‑time scaling: many in‑flight batches use early, larger `a(k0)/c(k0)` regardless of arrival.
- Overlap of `a(·)` “mass” across overlapping reports.
- Heterogeneous batch sizes: `abs(result) ∝ N`, so large workers dominate.
- Out‑of‑order arrivals and startup bursts at `k0 = 0`.

Arrival‑anchored, mass‑conserving factor (server‑only)
- At arrival with current `K` and `N` pairs, replace `(a_i(k0)/c_i(k0))` by the average `a_i` over the arrival span, divided by the `c_i` used at dispatch:
  - `avg_a_i = (1.0 / N) * sum(param.a / (A + (K + j))**alpha for j in range(1, N+1))`
  - `factor_i = avg_a_i / c_i_k0`
  - `theta[i] = clip(theta[i] + result * flip[i] * factor_i)`
- Properties: conserves total `a` mass; robust to reordering; uses the same `c_i` the engines played.

Optional variance guard
- Clamp per report: `result = clamp(result, -lambda_ * N, +lambda_ * N)` where `lambda_ in [3, 5]`.

Telemetry and history fidelity (optional)
- When storing history for plotting, also store the applied `K` and `N` (or the averaged `a` value) per report so plots reflect the actual scale used if you adopt the arrival‑anchored factor.

Operational tweak (optional)
- If feasible, reduce sub‑batch size dependence on core count so big workers report more frequently with smaller `N`, lowering variance without server changes.

## Chapter 5 — Choosing knobs (short)

- `c_end` (per axis): choose so `theta[i] ± c_end` yields a small, measurable Elo gap (a few Elo). This sets `phi`’s unit scale.
- `r_end` (or `lr` in schedule‑free): one scalar for all parameters in `phi`; tune to avoid frequent clipping and keep steady progress.
- `alpha, gamma, A`: `gamma` small (slow `c` decay), `alpha` moderate (stability late), `A` optional warm‑up (0–20% of total pairs).
- Bounds: keep `[min, max]` wide enough to avoid constant clipping; still clip every `theta` update.

## Chapter 6 — Schedule‑free SGD (mean‑gradient, weighted averaging)

This chapter documents the lean schedule‑free SGD variant used alongside the AdamW form (Chapter 7). It removes all power decays and second‑moment state while retaining: (a) constant φ‑space learning rate with optional warmup, (b) mean gradient scaling (`result / N`) for fairness across heterogeneous report sizes, (c) pair & learning‑rate weighted Polyak averaging mass (`lr_eff * N`), and (d) a short evaluation blend controlled by `sf_beta1` (first‑moment style smoothing). Weight decay, RMS normalization, variance clamp, and β₂ logic are NOT part of this branch.

### 6.1 Goals
1. Remove `a_k`, `c_k` learning rate decay complexity; keep per‑axis `c` only as probe radius.
2. Use a single constant scalar `sf_lr` (with optional warmup) shared by all parameters in φ.
3. Normalize gradient scale by dividing by `N` (pairs) so large reports are lower variance, not larger magnitude.
4. Use averaging mass `lr_eff * N` (not just `N`) so warmup scaling proportionally reduces early influence and the effective step size and averaging weight stay coupled.
5. Provide a light smoother (`sf_beta1`) via a blend with the (implicit) Polyak average; memory‑neutral implementation (no stored `x`).

### 6.2 Fast iterate, average, evaluation blend
Conceptually three sequences exist (per axis `i`): fast iterate `z`, long average `x`, evaluation iterate `theta_eval` (what games are played at). For a report with mean gradient scalar `g_scalar = (result / N)` and signing flips `flip[i]` captured at dispatch with scale `c_i`:

```
g_phi[i]    = g_scalar * flip[i]
delta_z[i]  = lr_eff * g_phi[i] * c_i              # map φ step to θ via c_i
z_new[i]    = z_old[i] + delta_z[i]
# Averaging mass update (scalar): weight_sum += lr_eff * N
w_mass      = (lr_eff * N) / weight_sum_after      # a_k in AdamW notation
x_new[i]    = (1 - w_mass) * x_old[i] + w_mass * z_new[i]
theta_eval[i] = (1 - sf_beta1) * z_new[i] + sf_beta1 * x_new[i]
```

Implementation eliminates `x` using algebra (see §6.5) and directly updates the exported/smoothed trajectory without storing an extra vector per history entry.

### 6.3 Mean gradient and fairness
Using `g_scalar = result / N` keeps expected gradient magnitude invariant to report size; variance shrinks ~1/N. The averaging mass includes `N` so total influence (step + weighting) still scales with contributed data volume. Splitting one big report into smaller ones with the same total pairs yields comparable net effect (up to stochastic noise).

### 6.4 Learning rate and warmup
`lr_eff = sf_lr * warmup_scale`, with an optional linear warmup over the first `sf_warmup_pairs` total pairs (`warmup_scale = min(1, iter_pairs / sf_warmup_pairs)`). Because averaging mass uses `lr_eff * N`, early steps (smaller `lr_eff`) also contribute proportionally less to the long average, mirroring the AdamW variant’s coupling.

### 6.5 Eliminating the explicit average
Let `a_k = (lr_eff * N) / weight_sum_after` be the incremental averaging weight. Define `beta = sf_beta1` for brevity. From the conceptual equations:

```
x_new = (1 - a_k) * x_old + a_k * z_new
theta_eval_new = (1 - beta) * z_new + beta * x_new
```

We store a single exported trajectory `theta_export` that we choose to be the *smoothed* path (analogous to `x` in the limit `beta→1`). Eliminate `x` and `theta_eval` algebraically using the previous exported value (`theta_old`) and both `z_old`, `z_new`:

```
theta_new = (1 - a_k) * theta_old + (1 - beta + beta * a_k) * z_new - (1 - a_k)*(1 - beta) * z_old
```

Edge cases:
* `beta = 0`: simplifies to `theta_new = z_new` (pure constant‑lr SPSA without smoothing).
* First update: define `weight_sum_before = 0` ⇒ `a_k = 1`; formula yields `theta_new = z_new` (expected: average equals fast iterate initially).

### 6.6 State summary (SGD branch)
Global scalar fields: `iter` (pairs), `sf_lr`, `sf_beta1`, optional `sf_warmup_pairs`, running `sf_weight_sum` (initial 0).

Per parameter: `theta` (exported smoothed path), `z` (fast iterate), bounds & `c` from classic SPSA setup.

No: `v` (second moment), `sf_beta2`, `sf_eps`, `sf_wd`, `sf_updates`, `sf_var_clamp` — those belong only to the AdamW branch.

### 6.7 One report arrival (pseudocode)
```
g_scalar = result / N
lr_eff = sf_lr * warmup_scale(iter_pairs)
sf_weight_sum += lr_eff * N
a_k = (lr_eff * N) / sf_weight_sum
for each param i:
  delta_z = lr_eff * g_scalar * flip[i] * c_i
  z_new = z_old + delta_z
  if sf_beta1 == 0:
    theta_new = z_new
  else:
    theta_new = (1 - a_k) * theta_old \
           + (1 - sf_beta1 + sf_beta1 * a_k) * z_new \
           - (1 - a_k) * (1 - sf_beta1) * z_old
  clip(theta_new)
  store(theta = theta_new, z = z_new)
iter += N
```

### 6.8 Hyperparameters
* `sf_lr`: constant φ learning rate; tune by monitoring clip frequency & convergence slope.
* `sf_beta1`: blend toward the long average. Typical 0.9 (0.8 faster, 0.95 smoother, 0 disables smoothing).
* `sf_warmup_pairs` (optional): linear ramp length for `sf_lr` and averaging mass coupling (default: 10% of planned pairs if omitted, or disabled if set to 0).

### 6.9 History semantics
Exported `theta` is already the smoothed trajectory (`theta_new` above). No reconstruction needed. If `sf_beta1 = 0`, the path is the raw fast iterate. Plots therefore remain directly comparable to classic runs (representing a denoised improving parameter trajectory).

### 6.10 Safeguards & notes
* Division by zero: first report handled by `sf_weight_sum` update (becomes `lr_eff * N` > 0) ⇒ `a_k = 1`.
* Large outliers: any variance clamp is deliberately excluded here to stay minimal; enabling it would mirror Chapter 7 step 1 (can be added later if needed).
* Clipping after blend is essential; excessive clipping suggests lowering `sf_lr`.

### 6.11 Summary
Schedule‑free SGD = constant‑lr φ‑space SPSA with mean gradient, lr‑scaled pair weighting, and a light blend controlled by `sf_beta1`. It is the minimal fair variant for heterogeneous workers: no RMS, no weight decay, no β₂ counter—just the pieces required for scale invariance, smoothing, and warmup‑consistent averaging mass.

Practical start: set `sf_lr` near the late classic `r_end`, `sf_beta1 = 0.9`; adjust `sf_lr` by ×1.25 / ÷1.25 based on observed stability & speed.

## Chapter 7 — Schedule‑free AdamW SPSA (Fishtest implementation)

Chapter 6 described a minimal schedule‑free SPSA using a simple cumulative (pair‑weighted) average plus a short blend. The production implementation adds (a) an RMS (second‑moment) normalization like Adam/AdamW, (b) decoupled weight decay, (c) warmup scaling, and (d) an arrival (report) based interpretation ("Model A: aggregated update"). This chapter documents the exact math used in `spsa_handler.py`.

### 7.1 High‑level differences vs Chapter 6
| Aspect | Chapter 6 (SGD) | Chapter 7 (AdamW variant) |
|--------|-----------------|---------------------------|
| Variance normalization | None | RMS via exponential second moment (β₂) |
| Weight decay | None | Decoupled, first‑order approx over N pairs |
| Averaging weight mass | lr_eff * N | lr_eff * N (same coupling) |
| Bias correction | N/A | Adam-style on second moment using arrival count |
| Gradient scale | result / N | result / N (mean) |
| Heterogeneous workers | Pair & lr weighting | Pair & lr weighting |

### 7.2 State (naming mirrors code)
Global (`spsa` dict): `iter`, `sf_lr`, `sf_beta1`, `sf_beta2`, `sf_eps`, `sf_wd`, `sf_updates`, `sf_weight_sum`, optional `sf_warmup_pairs`, `sf_var_clamp`.

Per parameter: `theta`, `z` (fast iterate), `v` (second moment), plus classic fields (`min`, `max`, `c`, etc.) retained for fallback.

### 7.3 One report update (Model A)
Given a report with `result = wins - losses` over `2N` games and flips `flip[i]` used with scale `c_i` captured at dispatch:

1. Optional clamp: `result <- clamp(result, ± λ N)` if `sf_var_clamp = λ > 0`.
2. Mean gradient scalar: `g_scalar = result / N`.
3. Per‑axis φ gradient: `g_phi[i] = g_scalar * flip[i]`.
4. Second moment: `v[i] = β₂ * v[i] + (1 - β₂) * g_phi[i]^2`.
5. Bias correction: `v_hat[i] = v[i] / (1 - β₂^{sf_updates})` after incrementing `sf_updates`.
6. Warmup: `lr_eff = sf_lr * min(1, iter_pairs / warmup_pairs)` (linear, pairs based).
7. RMS‑normalized φ step: `step_phi[i] = lr_eff * g_phi[i] / (sqrt(v_hat[i]) + sf_eps)`.
8. Map to θ fast iterate: `Δz[i] = step_phi[i] * c_i`.
9. Decoupled weight decay (first order N pairs): `z[i] = (1 - lr_eff * sf_wd * N)_+ * z[i] + Δz[i]`.
10. Averaging mass: `sf_weight_sum += lr_eff * N`, `a_k = (lr_eff * N) / sf_weight_sum`.
11. Blend (eliminating explicit x):
```
theta_new = (1 - a_k) * theta_old + (1 - β₁ + β₁ a_k) * z_new - (1 - a_k)(1 - β₁) * z_old
```
   If `β₁ = 0`, `theta_new = z_new`.
12. Clip `theta_new` into `[min, max]`.
13. Increment `iter += N`.

### 7.4 Why mean gradient (result / N)
Expected gradient scale independent of N; variance shrinks as 1/N; large workers contribute lower‑noise samples without inflated steps. Pair‑weighted averaging (`lr_eff * N`) still makes total influence proportional to data volume.

### 7.5 Warmup
Linear over first `sf_warmup_pairs` (or 10% of planned total pairs if unspecified). Applies to both step size and averaging mass (since mass uses `lr_eff * N`).

### 7.6 Variance clamp
If enabled (`sf_var_clamp = λ`), clamp raw `result` to `± λ N` before dividing by `N`. Protects RMS accumulator from rare outliers (e.g., crash cascades).

### 7.7 History reconstruction
We export a smoothed trajectory. Using `theta = (1 - β₁) z + β₁ x`, we can reconstruct `x = (theta - (1 - β₁) z)/β₁` for display if needed. For `β₁ = 0` this is skipped.

### 7.8 Fairness & equivalence
Splitting a large `N` report into multiple smaller reports summing to the same total pairs gives similar net effect (up to stochastic noise) because: gradient means are identical, cumulative averaging mass matches, weight decay approximation aggregates linearly, and each arrival applies exactly one β₂ decay.

### 7.9 Differences vs reference schedule‑free AdamW
| Feature | Fishtest | PyTorch / Optax |
|---------|----------|------------------|
| Averaging mass | lr_eff * N | polynomial / lr_max^p variants |
| Weight decay | (1 - lr_eff*wd*N)_+ | Decoupled (no N scaling needed for fixed batch) |
| Heterogeneous adjustment | Mean gradient + pair weighting | Fixed batch size |
| First moment | Omitted (RMS only) | Omitted in schedule‑free variant |

### 7.10 Hyperparameter quick guide
| Name | Meaning | Typical |
|------|---------|---------|
| sf_lr | Base φ learning rate | Start from late classic r_end |
| sf_beta1 | Eval blend β₁ | 0.9 (0.8 fast, 0.95 smoother) |
| sf_beta2 | RMS decay | 0.999 (0.995 faster response) |
| sf_eps | Numeric eps | 1e-8 |
| sf_wd | Decoupled weight decay | 0 (enable only if drift) |
| sf_var_clamp | Outlier λ | 0 (disabled), else 3–5 |

### 7.11 Edge safeguards
- Clamp decay factor to `[0,1]`.
- Guard bias denom: if `1 - β₂^{t} < 1e-16`, skip divide.
- If `β₁ = 0`, bypass reconstruction.
- If `β₂ = 1`, RMS reduces to raw gradient magnitude.

### 7.12 Pseudocode
```
sf_updates += 1
lr_eff = sf_lr * min(1, iter_pairs / warmup_pairs)
if var_clamp>0: result = clamp(result, -λN, +λN)
g_scalar = result / N
sf_weight_sum += lr_eff * N
a_k = (lr_eff * N) / sf_weight_sum
for each param i:
  g_phi = g_scalar * flip[i]
  v = β₂ * v + (1-β₂) * g_phi^2
  v_hat = v / (1 - β₂^{sf_updates})
  step_phi = lr_eff * g_phi / (sqrt(v_hat) + eps)
  delta_z = step_phi * c_i
  if sf_wd>0:
    decay = max(0, 1 - lr_eff * sf_wd * N)
    z = z * decay + delta_z
  else:
    z = z + delta_z
  if β₁ == 0:
    theta = z
  else:
    theta = (1 - a_k) * theta + (1 - β₁ + β₁ a_k) * z - (1 - a_k)(1 - β₁) * z_old
  clip(theta)
iter += N
```

### 7.13 Summary
Schedule‑free AdamW SPSA in Fishtest = mean gradient over pairs + RMS normalization + pair & lr weighted averaging + optional warmup & variance clamp + decoupled N‑scaled weight decay, all in a memory‑neutral form with fair treatment of heterogeneous workers.



## Chapter 8 — Quick reference

Symbols
- `theta[i]`: parameter `i` in θ‑space
- `phi[i]`: `theta[i] / c_i(k0)` (Elo‑normalized at dispatch)
- `c_i(k)`: per‑axis perturbation schedule; `c_i(k0)` fixed for the report
- `a_i(k)`: θ‑space LR schedule
- `r_k`: φ‑space LR, `r_k = a_i(k) / (c_i(k)**2)`
- `Delta[i]`: flip in `{-1, +1}`
- `result`: `wins - losses` over the report
- `K`: global pairs count (server), `k0`: dispatch snapshot, `N`: pairs in the report

Core identities
- `phi[i] = theta[i] / c_i(k0)`
- `theta[i] += r_k * c_i(k0) * result * Delta[i]`
- `a_i(k) = r_k * (c_i(k)**2)`

Implementation pointers (code map)
 - Server: request/update and state
   - SPSA handler: `server/fishtest/spsa_handler.py`
     - `_generate_data(...)`: compute `c_i(k0)`, draw flips, return w/b params; store snapshot `k0` and packed flips.
     - `__update_spsa_data(...)`: verify signature, reconstruct `R`, `c`, flips at `k0`; compute `result` and `N`; apply `theta` update; advance `iter` by `N`.
     - Helpers: `_pack_flips`, `_unpack_flips`, `_param_clip`, `_add_to_history`.
   - View args and schedules: `server/fishtest/views.py`
     - Parse per‑parameter rows; set `param.c`, `param.a_end = r_end * c_end**2`, `param.a`, `theta`.
   - Run/DB glue: `server/fishtest/rundb.py`
     - `RunDb.sync_update_task(...)` triggers SPSA update when results arrive.
   - Schemas: `server/fishtest/schemas.py`
     - Shapes for args, tasks, and `spsa_results` (W/L/D/num_games/sig).
   - UI utils: `server/fishtest/util.py`
     - `strip_run(...)` exposes `param_history` and SPSA args for plotting.
 - Worker: sub‑batching and reporting
   - Orchestration: `worker/worker.py`
   - Games and SPSA sub‑batches: `worker/games.py`
     - `launch_fastchess(...)`: get w/b params + signature; play at `theta ± cΔ`; post results with signature.
