# SPSA in Fishtest (lean, phi-normalized)

This document explains SPSA in Fishtest using Elo‑normalized coordinates `phi` and how the single learning rate `r` maps to the classic `theta`‑space schedule `a = r * c**2`. Equations use simple Python-style expressions. The code is authoritative; when in doubt, defer to `server/fishtest/spsa_handler.py`.

### At a glance
- Workers play symmetric probes around current parameters: `theta ± c * flip`.
- Each report applies one SPSA update using the total `result = wins − losses` from that report (raw result; not divided by `N`).

## Chapter 1 — Textbook SPSA and ScheduleFree optimizers (short recap)

### SPSA
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
  - `c_k[i] = c_i / (k+1)**gamma`    # Fishtest evaluates an arriving report with k = K+1 to avoid k=0

### Noise/SNR quick facts
- Finite‑difference signal grows linearly with `c` for small gaps; over `N` pairs: `E[result] ∝ N * c`.
- Expected step (first order): `(a_k / c_k) * E[result] ∝ a_k`.
- Step noise std: `(a_k / c_k) * sqrt(N)`; hence step SNR: `SNR ∝ c_k / sqrt(N)`.

### Schedule‑free optimizers (textbook)

We minimize a differentiable objective f: R^d → R. At iteration t, let g_t be a stochastic gradient with E[g_t | θ_t] = ∇f(θ_t). Schedule‑free means: constant step size (no decay), stability from Polyak averaging of the fast iterate (not from shrinking the learning rate).

Notation
- θ_t: parameter at which the gradient is computed (current iterate); by default `θ_t = (1 - ρ) * z_t + ρ * x_t`
- z_t: fast iterate (primary optimizer state)
- x_t: Polyak (running) average of z_t
- η > 0: constant learning rate
- ρ ∈ [0, 1]: export blend between z and x (ρ = 0 → export z; ρ = 1 → export x)
- t starts at 0; define α_t = 1/(t+1)

#### Schedule‑free SGD
Updates (minimize):
- Gradients:
  `g_t ≈ ∇f(θ_t)`
- Fast iterate:
  `z_{t+1} = z_t - eta * g_t`
- Polyak average (arithmetic mean of visited z’s):
  `x_{t+1} = (1 - alpha_t) * x_t + alpha_t * z_{t+1}`, where `alpha_t = 1/(t+1)`
- Export (optional smoothing):
  `theta_{t+1} = (1 - rho) * z_{t+1} + rho * x_{t+1}`

Notes
- With α_t = 1/(t+1), x_t is exactly the running average of z_0, z_1, …, z_t.
- ρ is a presentation choice; it doesn’t affect the internal dynamics of z.

#### Schedule‑free Adam
We use only Adam’s second moment (RMS) for normalization (AdamW‑style), a constant `eta`, and schedule‑free smoothing via Polyak averaging. There is no first‑moment EMA: `m_t` is not computed. In this section `beta1` denotes the export blend with the Polyak average (i.e., the weight on `x_t`).

Hyperparameters: beta1 ∈ [0,1] (Polyak/export blend), beta2 ∈ [0,1), eps > 0, eta > 0.

State: z_t (fast iterate), x_t (Polyak average), v_t (second moment).

Updates (minimize):
- Gradients:
  `g_t ≈ ∇f(θ_t)`
- Second moment (with bias correction):
  `v_{t+1} = beta2 * v_t + (1 - beta2) * (g_t * g_t)`
  `v_hat = v_{t+1} / (1 - beta2**(t+1))`
- Normalized step and fast iterate:
  `d_{t+1} = g_t / (sqrt(v_hat) + eps)`
  `z_{t+1} = z_t - eta * d_{t+1}`
- Polyak average and export:
  `x_{t+1} = (1 - alpha_t) * x_t + alpha_t * z_{t+1}`, where `alpha_t = 1/(t+1)`
  `theta_{t+1} = (1 - beta1) * z_{t+1} + beta1 * x_{t+1}`

Defaults (common, not prescriptive)
- SGD: choose `eta` per problem; `rho ∈ {0, 0.9}` if you use `rho` as the export blend there.
- Adam (schedule‑free): `beta1 = 0.9` (Polyak/export blend), `beta2 = 0.999`, `eps = 1e‑8`, constant `eta`.

### Practical knobs and defaults (SPSA/SGD/Adam)

- c_end (per axis): choose so `theta[i] ± c_end` yields a small, measurable Elo gap (a few Elo). This sets `phi`’s unit scale.
- r_end (or `sf_lr` in schedule‑free): one scalar for all parameters in `phi`; tune to avoid frequent clipping and keep steady progress.
- alpha, gamma, A: `gamma` small (slow `c` decay), `alpha` moderate (stability late), `A` optional warm‑up (0–20% of total pairs).
- Bounds: keep `[min, max]` wide enough to avoid constant clipping; still clamp every `theta` update.
- Defaults (good starting points):
  - Schedule‑free SGD: `sf_beta1 = 0.9` to enable Polyak filtering of the fast iterate; set `sf_beta1 = 0` to match classic SPSA behavior.
  - AdamW: `beta1 = 0.9`, `beta2 = 0.999`, `sf_eps = 1e-8`.

## Chapter 2 — Core math: θ‑space vs φ‑space (maximize Elo)

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

### 4) Exact θ ↔ φ equivalence (single equation)
- Relationships: `g_phi[i] = c_i * g_theta[i]` and `theta[i] = c_i * phi[i]`
- Map the φ‑update back to θ in one line:
  - `phi[i]   = phi[i]   + r_k * g_phi[i]`
  - `theta[i] = c_i*phi[i] + r_k * c_i * g_phi[i] = theta[i] + (r_k * c_i**2) * g_theta[i]`
  - Identify the classic schedule: `a_k = r_k * c_i**2`   # exact at the same snapshot `k0`

### 5) Why φ is the better working space
- One scalar learning rate:
  - A single `r_k` works for all parameters in φ. In θ this becomes per‑axis `a_{k,i} = r_k * c_i**2` automatically.
- One c, one place:
  - The same `c_i` sets both the probe separation (`theta ± c_i * Delta_i`) and the θ step via `(r_k * c_i)`.

### Notes (units and invariants)
- Units check: `phi` is unitless, `c_i` has θ‑units, `r_k` has inverse “result” units; θ‑step has θ‑units: `delta_theta_i = (r_k * c_i) * result * Delta[i]`.
- Symbols: This chapter uses `Delta` for conceptual flips; Chapter 3 uses `flip` for the packed/transported bits—same object, different names to match context.

## Chapter 3 — Inputs, schedules, and the θ ↔ φ transform

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

### The θ ↔ φ transform, step by step

1) Dispatch snapshot (save `k0 = K`, and define `iter_local = K+1`)
- Compute the perturbation used inside the sub‑batch:
  - `c_i_k0 = param.c / (iter_local**gamma)`
- Conceptual normalized coordinates at dispatch:
  - `phi[i] = theta[i] / c_i_k0`
- What the worker plays:
  - `theta_white[i] = clip(theta[i] + c_i_k0 * flip[i])`
  - `theta_black[i] = clip(theta[i] - c_i_k0 * flip[i])`
  - In φ: this is exactly `phi ± flip` (unit steps), because `theta = c_i_k0 * phi` elementwise.
- Implementation note: `k0` and the packed flips are stored in the task and sent back with the report.

2) Arrival update (classic schedule form)
- Reconstruct the same `c_i_k0` using the saved `k0`; compute:
  - `a_i_k0 = param.a / (A + iter_local)**alpha`
- Apply the θ update per parameter (maximize):
  - `step_i  = (a_i_k0 / c_i_k0) * result * flip[i]`
  - `theta[i] = clip(theta[i] + step_i)`

3) Reading the same update through φ (single `r` at the same snapshot)
- Define:
  - `r_k0 = a_i_k0 / (c_i_k0**2)`
- Then the θ step is the φ‑update mapped back:
  - `delta_theta_i = r_k0 * c_i_k0 * result * flip[i]`   # identical to step_i above

### Summary

- Normalize at dispatch: `phi = theta / c(k0)`; probes are `phi ± flip`.
- Update at arrival: `theta += (a/c) * result * flip = (r * c) * result * flip` with `a = r * c**2`.

## Chapter 4 — Server ↔ worker protocol

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

## Chapter 5 — Lean schedule‑free SGD SPSA

This chapter documents the code path in `server/fishtest/spsa_handler.py` for the lean schedule‑free SGD branch (`sf-sgd`), implemented per‑parameter in helper `_schedule_free_sgd_param_update`. It is authoritative for tests and audits.

### 5.R Requirements (authoritative)
- Constant learning rate `sf_lr`; no decay, no warmup.
- Raw `result = wins - losses`; never divided by `N` or otherwise normalized for the step amplitude.
- A report with `N` pairs produces the same fast‑iterate total delta as `N` sequential single‑pair arrivals (under the constant per‑report signal convention):
  `Δz = sf_lr * c * result * flip`.
- Persist only: `z` (unclamped fast iterate) and `theta` (exported & clamped). The Polyak surrogate `x` is reconstructed per update if `sf_beta1 > 0`.
- Clamp `x_new` (if used) and always clamp `theta_new`. Never clamp `z`.
- Global counters: `iter += N`; `sf_weight_sum += report_weight`, with `weight = sf_lr` and `report_weight = weight * N`.
- Legacy fallback: if a parameter lacks `"z"`, apply classic SPSA: `theta += R * c * result * flip` then clamp.

### Very short recap with N=1
- Gradient proxy (φ-space) evaluated at theta_new: `g_phi_mean[i] ≈ (wins - losses) * flip[i]`.
- Fast iterate in θ-space (N‑invariant total signal):
  `delta_total_step = sf_lr * c * (wins - losses) * flip`
  `z_new = z_prev + delta_total_step`
- Polyak surrogate x is the running arithmetic mean of z (θ-space, per‑pair mass).
  ```
  report_weight = 1
  weight_sum_curr = weight_sum_prev + report_weight

  x_new = (
      weight_sum_prev * x_prev
      + report_weight * z_new
  ) / weight_sum_curr
  ```
- Gradient evaluation point:
  `theta_new = (1 - sf_beta1) * z_new + sf_beta1 * x_new`
- Clamp rules and counters: clamp `x_new` and `theta_new`, never `z_new`; `iter += 1`, `sf_weight_sum += sf_lr * 1`.

### Very short recap with random N
- Gradient proxy (φ-space) evaluated at theta_new: `g_phi_mean[i] ≈ (wins - losses) / N * flip[i]`.
- Fast iterate in θ-space (N‑invariant total signal):
  `delta_total_step = sf_lr * c * (wins - losses) * flip`
  `z_new = z_prev + delta_total_step`
- Polyak surrogate x is the running arithmetic mean of z (θ-space, per‑pair mass). Closed form inside one report:
  ```
  weight = sf_lr
  report_weight = weight * N
  weight_sum_curr = weight_sum_prev + report_weight
  tri_factor = (N + 1) / 2

  x_new = (
      weight_sum_prev * x_prev
      + report_weight * z_prev
      + weight * delta_total_step * tri_factor
  ) / weight_sum_curr
  ```
- Gradient evaluation point:
  `theta_new = (1 - sf_beta1) * z_new + sf_beta1 * x_new`
- Clamp rules and counters: clamp `x_new` and `theta_new`, never `z_new`; `iter += N`, `sf_weight_sum += sf_lr * N`.


### 5.0 Snapshot (per report arrival)
```
result = wins - losses
N = num_games // 2
if N <= 0: abort

# Advance counters
iter += N
weight = sf_lr
report_weight = weight * N
weight_sum_prev = sf_weight_sum
weight_sum_curr = weight_sum_prev + report_weight
sf_weight_sum = weight_sum_curr
tri_factor = (N + 1) / 2
```

### 5.1 State structures
Global (`spsa` dict):
```
iter             # cumulative pairs (raw pair count)
sf_lr            # constant learning rate
sf_beta1         # blend coefficient
sf_weight_sum    # accumulated weighted mass (Σ report_weight), currently lr * total_pairs
```
Per schedule‑free parameter:
```
theta  # exported, always clamped
z      # fast iterate, unclamped
min, max, c, ...
```
Legacy parameter (classic):
```
theta, min, max, c, R, ...
```

### 5.2 Fast iterate aggregation (z-path, θ-space)
```
delta_total_step = sf_lr * c * result * flip   # θ-step (no division by N)
z_new = z_prev + delta_total_step              # z lives in θ-space (unclamped)
```

### 5.3 Surrogate averaging and blending (x/θ; clamp rules, θ-space)

Space recap (what lives where)
- θ-space: z_t, z_prev, z_new; x_prev, x_new (Polyak surrogate); θ, θ_new; s = delta_total_step / N; tri_factor contribution.
- φ-space (not used in SGD path): n/a here.

Batch-size randomness and gradient scale (why we use result, not result/N)
- Workers return a random number of pairs `N` per report (capacity varies).
- The per‑pair gradient proxy in φ is `g_phi_mean = (result / N) * flip`. Over `N` pairs, the sum of identical micro‑gradients is `N * g_phi_mean = result * flip`.
- To make the fast iterate `z` (θ-space) invariant to `N`, we update with the total signal `result` (not `result/N`):
  ```
  delta_total_step = sf_lr * c * result * flip   # θ-space
  z_new = z_prev + delta_total_step              # θ-space
  ```

Polyak filtering: what x is (plain words and one formula, θ-space)
- x is the Polyak (running) arithmetic mean of θ-states z over time. Think “keep the arithmetic mean of the z’s you visit,” but with a constant per‑micro‑step weight.
- Implementation model (conceptual): every micro‑step contributes equally with weight `weight = sf_lr`.
  - Running numerator: `num = Σ (weight * z_t)` over all processed micro‑steps t since the run started.
  - Running denominator (mass): `den = Σ weight = sf_lr * (total_pairs_so_far)`.
  - Running average: `x = num / den`.
- In code we don’t loop micro‑steps; we add the whole report’s contribution in closed form, then divide by the new total mass.

Reconstruct Polyak surrogate (θ-space, used only if `beta1 > 0`)
```
x_prev = (theta_prev - (1 - beta1) * z_prev) / beta1   # θ-space
x_prev = clamp(x_prev)    # clamp before use
```

Triangular surrogate: closed‑form, no loops (why `tri_factor = (N + 1) / 2`, θ-space)
- All quantities below are θ-space within this report.
- Goal: compute the arithmetic mean of the z “right endpoints” you would see inside this report if you expanded it into N unit micro‑steps.
- Within one report of `N` pairs, the micro‑step size is:
  ```
  s = delta_total_step / N   # θ-space micro-step
  ```
- Right‑endpoint model for the fast iterate after `t` micro‑steps (`t = 0..N`):
  ```
  z_t = z_prev + t * s       # θ-space
  ```
- Where the sum comes from (explicit breakdown)
  - The N right endpoints are: `z_prev + 1*s, z_prev + 2*s, ..., z_prev + N*s`.
  - Arithmetic mean of those N values:
  ```
  avg_right_end = (1/N) * sum(z_prev + t * s for t in range(1, N+1))
  # separate the constant and the ramp terms
  avg_right_end = (1/N) * (N * z_prev) + (1/N) * s * sum(t for t in range(1, N+1))
  # sum(range(1, N+1)) = N * (N + 1) / 2
  avg_right_end = z_prev + s * (N + 1) / 2
  # replace s = delta_total_step / N
  avg_right_end = z_prev + delta_total_step * ((N + 1) / (2 * N))
  ```
- Tiny sanity example (`N = 3`): right endpoints are `z_prev + s, z_prev + 2*s, z_prev + 3*s`; average is `z_prev + (1+2+3)*s/3 = z_prev + 2*s`. With `s = delta_total_step/3`, this is `z_prev + delta_total_step * (2/3)`.
- Report‑mass contribution to the surrogate numerator (`weight = sf_lr`, `report_weight = weight * N`):
  ```
  # Add this report’s micro‑steps to the running numerator num = Σ weight * z_t
  # Each of the N right endpoints contributes weight * z_t. Sum them in closed form:
  #   Σ (weight * z_t) = weight * Σ (z_prev + t*s)
  #                    = weight * (N * z_prev) + weight * s * Σ t
  # Use s = delta_total_step / N and Σ t = N*(N+1)/2
  contrib = report_weight * z_prev + weight * delta_total_step * ((N + 1) / 2)
  tri_factor = (N + 1) / 2   # average t over 1..N; exact; midpoint intuition is N/2
  ```

Weighted‑mass Polyak surrogate and clamp (θ-space)
```
x_new = (
    weight_sum_prev * x_prev
    + report_weight * z_prev
    + weight * delta_total_step * tri_factor
) / weight_sum_curr
x_new = clamp(x_new)
```

Why this formula matches “x is the running average of z” (θ-space)
- Before this report: `num_prev = weight_sum_prev * x_prev`, `den_prev = weight_sum_prev`.
- This report adds: `num_add = contrib`, `den_add = report_weight`.
- After this report: `num_curr = num_prev + num_add`, `den_curr = den_prev + den_add = weight_sum_curr`.
- Running average is `x_new = num_curr / den_curr`, which is exactly the code above.

Blend the new gradient evaluation and persist (θ-space)
```
if beta1 == 0:
    theta_new = z_new
else:
    theta_new = (1 - beta1) * z_new + beta1 * x_new
theta_new = clamp(theta_new)

# Persist z_new (θ-space, unclamped) and theta_new (θ-space, clamped)
```

Sanity checks
- `N = 1` ⇒ `tri_factor = (1 + 1) / 2 = 1` (the single right endpoint).
- If `beta1 == 0` and no clamp: `theta_new - theta_prev == delta_total_step` (exact).
- `z` is never clamped; `x_new` and `theta_new` are clamped to `[min, max]`.

Note on bias: The right‑endpoint average induces a mild bias versus an exact micro‑state integral; the implementation uses this simple closed form for speed and consistency across random `N`.

### 5.4 History and telemetry (as implemented)
History is recorded via `_add_to_history` at a sampling cadence derived from the run-level `num_games`:
- Sampling parameters:
  ```
  n_params = len(params)
  samples = 100 if n_params < 100 else 10000 / n_params if n_params < 1000 else 1
  period = run["args"]["num_games"] / 2 / samples   # note: uses run-level num_games
  ```
- A snapshot is appended only when:
  ```
  len(param_history) + 1 > iter / period
  ```
- Stored per-parameter fields in each snapshot entry:
  - "theta": θ-space show value
    - schedule‑free: `x_new` (clamped, θ) if `sf_beta1 > 0`, else `theta_new` (clamped, θ)
    - classic: `theta` (clamped, θ) after update
  - "R": φ-rate used for the classic θ-update at dispatch (maps via `a = R * c**2`)
  - "c": per-axis probe scale at dispatch (θ-units)
- Note: `x` is not persisted in state; it is reconstructed per update when recorded.

### 5.5 Invariants & edge cases
- `iter` increases by exactly `N`.
- `sf_weight_sum` increases by `sf_lr * N`.
- If `sf_beta1 == 0` and no clamp: `theta_new - theta_prev == delta_total_step`.
- Bounds: `min ≤ x_new ≤ max` (when used), `min ≤ theta_new ≤ max`; `z_new` is unconstrained.
- Update is aborted if signature mismatch or `N <= 0`.

## Chapter 6 — Schedule‑free AdamW SPSA

This chapter mirrors Chapter 5 for the schedule‑free AdamW path, explaining all math used in the sf-adam code:
- Space map at a glance.
- φ-space: g_phi_mean, v → v_hat → denom (normalization lives here).
- θ-space: z_prev → z_new, x_prev → x_new, theta; mapping via `θ-step = c * (φ-step)`.
- Batch-size invariance: step with the total `result`; use `result/N` only for the second moment `v`.
- Surrogate x: Polyak (arithmetic) mean of z via mass blend `a_k`; no triangular averaging for Adam (see “Why no triangular term here”).
- Micro-batch damping k(N, β2): rescales the macro step to keep N‑invariance under EMA smoothing; it is the geometric‑mean analogue of the SGD triangular factor in Chapter 5.3 (see the k(N, β2) subsection and the α(√β2, N) note).

### 6.R Requirements (authoritative)
- Constant global learning rate `sf_lr` (no decay, no warmup, no weight decay).
- Raw `result = wins - losses`; never divided by `N` for the step amplitude.
- Persist: `theta` (clamped export), `z` (fast iterate, unclamped), `v` (second moment). The Polyak surrogate `x` is reconstructed per update when `sf_beta1 > 0`.
- Reconstruction (if `sf_beta1 > 0`):
  ```
  x_prev = (theta_prev - (1 - beta1) * z_prev) / beta1
  x_prev = clamp(x_prev)
  ```
- Weighted mass accumulation:
  ```
  weight = sf_lr
  report_weight = weight * N
  sf_weight_sum += report_weight
  a_k = report_weight / sf_weight_sum   # after increment
  ```
- Second moment update per parameter uses closed-form aggregation over `N` identical mean micro gradients with bias correction exponent = total processed pairs after increment (φ-space):
  ```
  g_phi_mean = (result / N) * flip          # φ-space per-pair gradient proxy
  v = (beta2**N) * v + (1 - beta2**N) * (g_phi_mean**2)   # φ-space EMA over N
  v_hat = v / (1 - beta2**micro_steps)      # φ-space bias correction
  denom = sqrt(v_hat) + sf_eps              # φ-space normalization
  ```

  Why this equals N looped EMA updates (derivation)
  - Start from Adam’s second-moment EMA per micro-step `t` (φ-space), with constant per-pair gradient magnitude inside the report:
    ```
    v_{t+1} = beta2 * v_t + (1 - beta2) * g^2,   where g = g_phi_mean
    ```
  - Unroll N identical micro-steps (no change in `g` within the report):
    ```
    v_1 = beta2 * v_0 + (1 - beta2) * g^2
    v_2 = beta2 * v_1 + (1 - beta2) * g^2
        = beta2^2 * v_0 + (1 - beta2) * (1 + beta2) * g^2
    ...
    v_N = beta2^N * v_0 + (1 - beta2) * (1 + beta2 + ... + beta2^{N-1}) * g^2
        = beta2^N * v_0 + (1 - beta2) * ((1 - beta2^N) / (1 - beta2)) * g^2
        = beta2^N * v_0 + (1 - beta2^N) * g^2
    ```
    That is exactly the closed form:
    ```
    v ← (beta2**N) * v + (1 - beta2**N) * (g_phi_mean**2)
    ```
    Note: `flip ∈ {±1}` so `(g_phi_mean**2) = (result / N)**2` (since `flip**2 = 1`).

  - Bias correction exponent:
    Adam’s bias correction uses the total number of micro-steps processed so far, call it `t`. In our setting, one pair = one micro-step, so after consuming `N` new pairs we have:
    ```
    micro_steps = t_after = (previous_total_pairs) + N = iter
    v_hat = v / (1 - beta2**micro_steps)
    ```
    This matches standard Adam, where the correction exponent is the current step count after the update.

  - Denominator for normalization (φ-space):
    ```
    denom = sqrt(v_hat) + sf_eps
    ```
    This is exactly the RMS term used to normalize the φ-step.

  Practical consequence
  - The closed form replaces an explicit N-step loop without changing the result, because within a report the per-pair gradient proxy is constant (`g_phi_mean`). This keeps the implementation fast and numerically consistent with sequential micro-updates.

- Directional fast iterate step (φ → θ mapping; no triangular surrogate):
  ```
  step_phi = (sf_lr * result * flip) / denom   # φ-space step (batch-size invariant numerator)
  # Optional micro-batch damping (enabled in code when N>1 and 0<beta2<1):
  # k(N, beta2) = (1 - beta2**(N/2)) / (N * (1 - sqrt(beta2)))  in (0, 1]
  # Near beta2 -> 1: k ≈ 1 - ((N - 1)/4) * (1 - beta2)
  step_phi *= k(N, beta2)   # if applicable; clipped to (0, 1] in code
  z_new = z_prev + step_phi * c                 # map φ-step to θ via c
  ```
  The factor `k(N, beta2)` is bounded to `(0, 1]` in code for safety; if conditions don’t hold, `k = 1`.
- Polyak surrogate averaging and blend (if `beta1 > 0`):
  ```
  x_new = (1 - a_k) * x_prev + a_k * z_new
  x_new = clamp(x_new)
  theta_new = clamp((1 - beta1) * z_new + beta1 * x_new)
  ```
  else:
  ```
  theta_new = clamp(z_new)
  ```
- Never clamp `z`. Legacy fallback: parameters lacking `"z"` are updated via classic SPSA (as in Chapter 5).

### 6.0 Snapshot (per report arrival)
```
result = wins - losses
N = num_games // 2
if N <= 0: abort

iter += N

weight = sf_lr
report_weight = weight * N
weight_sum_prev = sf_weight_sum
weight_sum_curr = weight_sum_prev + report_weight
sf_weight_sum = weight_sum_curr
a_k = report_weight / weight_sum_curr

g_mean = result / N        # ONLY for second-moment modeling
micro_steps = iter         # bias correction exponent for v
```

### 6.1 State structures
Global (`spsa` dict):
```
iter, sf_lr, sf_beta1, sf_beta2, sf_eps, sf_weight_sum
```
Per schedule‑free Adam parameter:
```
theta (clamped), z (unclamped), v (second moment), min, max, c, ...
```
Legacy (classic): same as Chapter 5.

### 6.2 Batch size, second moment, step, Polyak filtering, and N‑damping

Batch-size randomness and gradient scale (result vs result/N)
- Workers return a random number of pairs `N` per report.
- Per‑pair φ‑gradient proxy: `g_phi_mean = (result / N) * flip`. Over `N` pairs, the total signal is `result * flip`.
- Keep the step amplitude invariant to `N` by using the total `result` in the numerator of the step (φ-step, then map to θ via c):
  ```
  step_phi = (sf_lr * result * flip) / denom
  z_new = z_prev + step_phi * c
  ```
- Use `g_phi_mean` only for the second moment `v` (per‑pair modeling), not for the step amplitude.

Closed‑form second moment and denominator (φ-space, no loops)
```
v = (beta2**N) * v + (1 - beta2**N) * (g_phi_mean**2)
v_hat = v / (1 - beta2**micro_steps)   # micro_steps = total pairs after this report
denom = sqrt(v_hat) + sf_eps
```
- This aggregates the N identical micro‑gradients in one shot and applies bias correction.

Optional micro‑batch damping k(N, β2): what it fixes and where it comes from
- Why we need it: in Adam, the denominator (RMS) grows during the N identical micro‑steps because `v` is an EMA. If we compress those N micro‑steps into one macro update and use only the end‑of‑block denominator `denom_end`, we apply the largest denominator to the whole block. Earlier micro‑steps would have used smaller denominators, so the true sequential sum is larger than the one‑shot macro step. As N grows, the mismatch grows; the macro step shrinks with N.
- Back‑of‑the‑envelope model that matches practice:
  - With constant per‑pair magnitude `|g_phi_mean|` inside the block, denominators across micro‑steps scale roughly geometrically by `sqrt(beta2)`.
  - Let `d_j` be the denominator at micro‑step j (1..N) and `d_end` the denominator at the end of the block. Approximate:
    - `d_j ≈ d_end * beta2**((N - j)/2)`   # earlier steps see smaller denom
  - Sequential micro‑steps sum:
    - `S_seq ≈ Σ_{j=1..N} (sf_lr * g_phi_mean) / d_j`
    - `= (sf_lr * g_phi_mean / d_end) * Σ_{j=1..N} beta2**((j - N)/2)`
    - `= (sf_lr * g_phi_mean / d_end) * Σ_{i=0..N-1} beta2**(i/2)`
  - Compressed macro step uses numerator `sf_lr * (N * g_phi_mean)` and denominator `d_end`:
    - `S_macro = (sf_lr * N * g_phi_mean) / d_end`
  - Match the two by multiplying the macro step with the average geometric factor:
    - `k(N, beta2) = (1/N) * Σ_{i=0..N-1} beta2**(i/2) = (1 - beta2**(N/2)) / (N * (1 - sqrt(beta2)))`
- How it plugs into the step:
  ```
  step_phi = ((sf_lr * result * flip) / denom) * k(N, beta2)
  z_new = z_prev + step_phi * c
  ```
- Guards and numerics:
  - Apply only if `N > 1` and `0 < beta2 < 1`; otherwise use `k = 1` (no damping).
  - Clip to `(0, 1]` in code for safety (geometric mean ≤ 1).
  - Near `beta2 -> 1`, use the numerically stable series:
    - `k(N, beta2) ≈ 1 - ((N - 1)/4) * (1 - beta2)`
- Sanity checks and intuition:
  - `N = 1` ⇒ `k = 1` (no change); `beta2 = 0` ⇒ `k = 1` (no smoothing); `beta2 -> 1` ⇒ `k -> 1` with a small linear correction.
  - Example: `beta2 = 0.99`, `N = 16` ⇒ `k ≈ (1 - 0.99**8) / (16 * (1 - 0.995)) ≈ 0.97` (mild reduction).
  - Takeaway: `k` compensates for the fact that “one big step with the final denom” underestimates the sum of N smaller steps that would have used a ladder of smaller denoms along the way.

Polyak filtering: x is the running arithmetic mean of z
- Think “keep the arithmetic mean of the z’s you visit,” with constant per‑micro‑step weight `weight = sf_lr`.
- Running numerator/denominator across the whole run:
  - `num = Σ (weight * z_t)`, `den = Σ weight = sf_weight_sum`.
  - Running average: `x = num / den`.
- Report‑level closed form (no loops): in Adam we approximate the micro‑step average by the endpoint `z_new` (no triangular term):
  - Numerator addition: `num_add = report_weight * z_new`.
  - Denominator addition: `den_add = report_weight`.
- Therefore the updated surrogate is a mass‑weighted blend with `a_k = report_weight / sf_weight_sum`:
  ```
  x_new = (1 - a_k) * x_prev + a_k * z_new
  x_new = clamp(x_new)
  ```

Why no triangular term here (contrast with SGD)

What the exact surrogate would be under Adam’s smoothing
- In SGD, micro‑steps inside a report are equal, so the average of the N right endpoints is exactly the triangular factor `(N+1)/(2N)` times the total delta, giving the `tri_factor = (N+1)/2` in the numerator.
- In Adam, the denominator grows across the N micro‑steps because `v` is an EMA, so the per‑micro‑step sizes shrink over the block. If we model the denominator growth as geometric with ratio
  ```
  q = sqrt(beta2)  in (0, 1]
  ```
  then the micro‑step sizes are approximately a geometric sequence:
  ```
  s_j ∝ q^{j - N}     # j = 1..N, later steps are smaller only if q>1; with q<1 they are larger denominators and smaller steps earlier, larger later; the net effect is "end-heavy" change
  ```
- The exact arithmetic mean of the N right endpoints (the Polyak surrogate over micro‑steps) can be written without loops as
  ```
  z_avg = z_prev + α(q, N) * Δ
  ```
  where `Δ = Σ_{j=1..N} s_j` is the total fast‑iterate delta in this report, and the “Adam triangular” factor is
  ```
  α(q, N) = [1 - (N+1) q^N + N q^{N+1}] / [N (1 - q) (1 - q^N)]    # closed form
  ```
  Derivation sketch:
  - Average of right endpoints: (1/N) Σ_{t=1..N} z_t with z_t = z_prev + Σ_{j=1..t} s_j
  - Swap sums: (1/N) Σ_{j=1..N} (N - j + 1) s_j
  - With geometric steps s_j ∝ q^{j - N}, use the standard sums
    - Σ q^j = q (1 - q^N) / (1 - q)
    - Σ (N - j + 1) q^j = q [1 - (N+1) q^N + N q^{N+1}] / (1 - q)^2
  - Normalize by Δ = Σ s_j to get the α(q, N) above.

Key limits and intuition
- q = 1 (no smoothing change within the block) ⇒ α(1, N) = (N+1)/(2N)  (the SGD triangular average).
- 0 < q < 1 (Adam’s usual case) ⇒ α(q, N) strictly increases toward 1 as q decreases, i.e., the average lies closer to the endpoint z_new than the triangular midpoint because more of the change happens later in the block.
- As β2 → 1 (q → 1), α(q, N) → (N+1)/(2N) and the difference from the triangular average is O(1 − q).
  A short series expansion around q = 1 gives:
  ```
  α(q, N) ≈ (N+1)/(2N) + ((N-1)/12) * (1 - q) + O((1 - q)^2)
  ```

Why we approximate with z_new (and not α(q, N))
- Accuracy vs complexity: The exact α(q, N) depends on an effective ratio q for the denominators across the block. In practice the denominator also includes bias correction and sf_eps, and g varies slightly—so q is only approximate. Using α(q, N) adds complexity for a second‑order correction.
- Magnitude of the effect: The surrogate blend uses a_k = report_weight / sf_weight_sum, which decays over the run. The difference between z_avg and z_new impacts x_new by a factor a_k * (1 − α(q, N)) * |Δ|, typically small once sf_weight_sum grows.
- Consistency with step damping: We already restore N‑invariance of the macro step via k(N, β2) on the numerator. Given that, placing the surrogate at z_new (α = 1) is a simple, end‑heavy approximation that aligns with the fact that under smoothing more of the change accrues toward the end of the block.

Optional: exact surrogate if you want it
- If we ever choose to match the micro‑step average exactly under the geometric model, replace the report‑level surrogate contribution
  ```
  # current (endpoint):
  num_add = report_weight * z_new
  # exact (geometric):
  num_add = report_weight * (z_prev + α(q, N) * Δ)
  ```
  with `q = sqrt(beta2)` and `Δ = z_new - z_prev` (after applying k(N, β2) to the step).
- We’ve kept the endpoint form to stay simple, fast, and robust; the empirical difference is negligible in our settings (β2 close to 1, moderate N).

### 6.3 History and telemetry (as implemented)
History behavior is identical to Chapter 5 (same cadence and stored fields):
- Sampling cadence uses the run-level `num_games` and the same `samples` heuristic and `period`.
- Stored per-parameter fields per snapshot:
  - `"theta"` = show value (`x_new` if `beta1>0`, else `theta_new`)
  - `"R"`, `"c"` as provided in `w_params` for the update
- `x` is reconstructed transiently (not persisted).

### 6.4 Invariants & edge cases
- `iter` increases by exactly `N`; `sf_weight_sum` increases by `sf_lr * N`.
- If `beta1 == 0` and no clamp: `theta_new - z_prev == step_phi * c`.
- Bounds: `min ≤ theta_new ≤ max`; if `beta1>0`, `min ≤ x_new ≤ max`; `z_new` is unconstrained.
- Update is aborted if signature mismatch or `N <= 0`.

## Chapter 7 — Quick reference

Symbols and spaces
- `theta[i]`: parameter `i` in θ‑space
- `phi[i]`: `theta[i] / c_i(k0)` (Elo‑normalized at dispatch)
- `c_i(k)`: per‑axis perturbation schedule; `c_i(k0)` fixed for the report
- `r_k`: φ‑space LR; classic `a_k = r_k * (c_k**2)`
- `flip[i]`: Rademacher in `{-1, +1}`
- `result`: `wins - losses` over the report
- `K`: global pairs count (server), `k0`: dispatch snapshot, `N`: pairs in the report

Space map
- θ‑space: `theta`, `z`, `x`, `delta_theta`, `tri_factor` contribution, `c` has θ‑units.
- φ‑space: `phi`, `g_phi_mean`, `v`, `v_hat`, `denom`, `step_phi` (unitless after multiplying by result and sf_lr).
- Mapping: `theta = c * phi`; θ‑step = `c * (φ-step)`.

Units quick notes
- φ is unitless; c has θ‑units; sf_lr has inverse “result” units.
- φ‑step: `(sf_lr * result)` is unitless; θ‑step multiplies by c to get θ‑units.

Implementation pointers (code map)
- Server: request/update and state in `server/fishtest/spsa_handler.py`
  - `_generate_data(...)`: compute `c_i(k0)`, draw flips, return w/b params; store `k0` and packed flips
  - `__update_spsa_data(...)`: verify signature, reconstruct flips and `c(k0)`; compute `result`, `N`; apply update; advance `iter` by `N`
  - Helpers: `_pack_flips`, `_unpack_flips`, `_param_clip`, `_add_to_history`
- Worker orchestration: `worker/worker.py`; sub‑batches in `worker/games.py`

## External Reference Implementations

These codebases provide public implementations of schedule‑free optimizers used for cross‑checking semantics (fast iterate vs Polyak surrogate, weighting, second‑moment handling).

- PyTorch (facebookresearch/schedule_free)
  Repository: https://github.com/facebookresearch/schedule_free

- Optax (google-deepmind/optax) schedule_free contrib module
  Source file: https://github.com/google-deepmind/optax/blob/main/optax/contrib/_schedule_free.py

## Bibliography

[1] J. C. Spall. “Multivariate Stochastic Approximation Using a Simultaneous Perturbation Gradient Approximation.” IEEE Transactions on Automatic Control, 37(3), 1992. https://www.jhuapl.edu/spsa/PDF-SPSA/Spall_TAC92.pdf

[2] J. C. Spall. “Implementation of the Simultaneous Perturbation Algorithm for Stochastic Optimization.” IEEE Transactions on Aerospace and Electronic Systems, 34(3), 1998. https://www.jhuapl.edu/SPSA/PDF-SPSA/Spall_Implementation_of_the_Simultaneous.PDF

[3] D. P. Kingma, J. Ba. “Adam: A Method for Stochastic Optimization.” arXiv:1412.6980 (2014). https://arxiv.org/pdf/1412.6980

[4] I. Loshchilov, F. Hutter. “Decoupled Weight Decay Regularization.” arXiv:1711.05101 (2017). https://arxiv.org/pdf/1711.05101

[5] X. Wang, L. Aitchison. “Batch Size Invariant Adam.” arXiv:2402.18824 (February 2024). https://arxiv.org/pdf/2402.18824

[6] Z. Chen, N. He, T. Ma, S. Song, Z. Wang. “The Road Less Scheduled: Schedule‑Free Optimization in Deep Learning.” arXiv:2405.15682 (May 2024). https://arxiv.org/pdf/2405.15682

[7] Z. Chen, N. He, T. Ma, S. Song, Z. Wang. “General Framework for Online‑to‑Nonconvex Conversion: Schedule‑Free SGD Is Also Effective for Nonconvex Optimization.” arXiv:2411.07061 (November 2024). https://arxiv.org/pdf/2411.07061

[8] D. Morwani, H. Zhang, N. Vyas, S. Kakade. “Connections Between Schedule‑Free Optimizers, Ademamix, and Accelerated SGD Variant.” arXiv:2502.02431 (February 2025). https://arxiv.org/pdf/2502.02431

[9] M. Song, K. Ahn, B. Baek, C. Yun. “Through the River: Understanding the Benefit of Schedule‑Free Methods for Language Model Training.” arXiv:2507.09846 (July 2025). https://arxiv.org/pdf/2507.09846v1

[10] C. Brown. “Analysis of Schedule-Free Nonconvex Optimization.” arXiv:2508.06743 (August 2025). https://arxiv.org/pdf/2508.06743
