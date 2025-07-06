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

## Chapter 6 — Schedule‑free SPSA (full)

This chapter expands the schedule‑free (constant learning rate) variant: why we introduce a fast iterate `z`, an averaged iterate `x`, and the evaluation/blend iterate `theta` (a.k.a. `y` in textbook momentum/EMA literature), how we keep RAM neutral by not storing `x`, and how weighting by pairs (`N`) adapts fairly to heterogeneous workers.

### 6.1 Goals
1. Eliminate hand‑tuned power decays (`a_k`, `c_k`) for the learning rate part while keeping the existing per‑axis `c` for probe separation (still needed for finite differences / scale).
2. Use a single constant scalar step size in φ‑space: `lr` (alias: `sf_lr`).
3. Variance reduction and stability via two nested smoothers:
   - Long EMA (or cumulative average) `x` of the stochastic fast iterate.
   - Short blend of the current fast iterate and the smoothed iterate into the played/evaluated parameters `theta`.
4. Respect asynchronous heterogeneous batch sizes by weighting averages by the number of game pairs contributed.
5. Remain memory‑neutral: do not persist a full extra vector for `x` in history; reconstruct on demand.

### 6.2 The three sequences (textbook view)
We conceptually maintain for each parameter dimension `i`:

```
z_{t+1} = z_t + lr * (c_i * result_t * flip_t[i])          # fast step (φ gradient mapped back to θ via c_i)
x_{t+1} = (1 - w_t) * x_t + w_t * z_{t+1}                  # long average (Polyak / EMA)
theta_{t+1} = (1 - beta) * z_{t+1} + beta * x_{t+1}        # evaluation blend
```

Where:
* `lr` (`sf_lr`) is constant (no decay).
* `beta` (`sf_beta`) is a blend weight in `[0,1]` (typical: `0.9`). Larger `beta` shifts evaluation toward the averaged iterate, smoothing noise; smaller `beta` makes evaluation track the fast iterate (more responsive, noisier).
* `w_t` is the averaging weight for incorporating the new sample into `x`.

We play games (the SPSA probes) at `theta_t ± c_i * flip[i]`, i.e. evaluation iterate.

### 6.3 φ‑space interpretation
Recall: a θ‑step of `delta_theta_i = lr * c_i * result * flip[i]` corresponds to a φ‑step of `delta_phi_i = lr * result * flip[i]` (since `theta_i = c_i * phi_i` at the same snapshot). Thus schedule‑free SPSA is literally constant‑step SGD in φ with a two‑level smoother before evaluation. No power schedules remain; only the per‑axis scale `c_i` (probe radius) persists.

### 6.4 Choice of averaging weight `w_t`
We want fair contribution proportional to the number of *pairs* (`N`) in each asynchronous report so large workers do not under‑ or over‑influence relative to their data volume. Let `pairs_total_before` be the cumulative number of pairs incorporated into `x` so far. For a new report with `N` pairs:

```
w_t = N / (pairs_total_before + N)
```

Properties:
* This is exactly the incremental formula for a cumulative average of all past `z` samples when each sample is replicated `N` times.
* If every report had `N = 1`, it degenerates to the standard running average (`1/(t+1)`).
* No tuning knob: purely data‑proportional.

Alternative (EMA) form: if one preferred an exponential average with fixed half‑life, you could set `w_t = 1 - exp(-N / tau_pairs)`, but we currently use the unbiased cumulative version (no extra hyperparameter) for transparency.

### 6.5 RAM‑neutral reconstruction of `x`
We do not store `x` in persistent state or in the per‑step history vector (saves one vector per history point). Instead we store only:
* `theta_t` (actually we overwrite this slot with `x_t` for schedule‑free runs — see §6.9 for rationale).
* `z_t` as part of the internal SPSA state.
* Cumulative pair count (implicit in `spsa["iter"]`).

Reconstruction identity (derive from the blend equation):

```
theta_t = (1 - beta) * z_t + beta * x_t
=> x_t = (theta_t - (1 - beta) * z_t) / beta          (beta > 0)
```

Thus when we need to display the *averaged* trajectory (what users care about for progress / convergence), we reconstruct `x_t` on the fly from the stored `theta_t` (which actually is `x_t` after substitution; see §6.9) and the current `z_t` if needed for intermediate steps. This keeps history semantics identical to classic runs (frontend still consumes `param_history[].theta`).

Edge when `beta = 0`: the evaluation iterate equals the fast iterate; then `x` is unused and reconstruction is undefined (division by 0). Implementation guards: if `beta == 0` we skip reconstruction and treat `theta` as the displayed fast iterate.

### 6.6 Putting it together (one report)
For a report spanning `N` pairs with aggregated `result = wins - losses` (sum over those games) and stored snapshot index `k0` for `c_i`:

1. Compute the fast step per axis:
   `z[i] = z[i] + lr * c_i_k0 * result * flip[i]`
2. Update cumulative pairs: `pairs_total += N`.
3. Compute weight: `w = N / pairs_total`.
4. Update running average (concept): `x[i] = (1 - w) * x[i] + w * z[i]`.
5. Evaluation blend: `theta[i] = (1 - beta) * z[i] + beta * x[i]`.
6. Clip `theta[i]` into `[min_i, max_i]`.
7. Advance global `iter` by `N`.

Implementation detail: we do not materialize step (4) permanently; instead we either (a) store `x` in the historical record directly (so the plotting field is the smoothed path) or (b) reconstruct as needed (historical choice here is to store `x` in the `theta` slot for clarity and leave `z` only in state).

### 6.7 Relation to classic decayed schedule
If in the classic formulation you froze `a_k/c_k` to a constant value `lr * c_i` and disabled both the `a` and `c` power decays, then the raw θ‑update sequence is identical to the schedule‑free fast iterate `z`. The schedule‑free method simply *adds* the long average `x` and blend `beta` for variance reduction, offering smoother progress without decays. Thus classic late‑run behavior (where `a_k/c_k` stabilizes) mirrors schedule‑free steady state.

### 6.8 Hyperparameters (`sf_lr`, `sf_beta`)
* `sf_lr` (`lr`): primary knob; too large -> frequent clipping & noisy plateau; too small -> slow drift.
* `sf_beta` (`beta`): smoothing depth. Higher yields slower but cleaner trajectory. Practical window:
  - `0.8` light smoothing.
  - `0.9` default (balance).
  - `0.95+` heavy smoothing (may lag improvements in highly dynamic early phases).
No separate decay exists; adapt by manual adjustments or future adaptive schemes (not yet implemented).

### 6.9 History semantics and user display
Users expect the plotted curve to represent *progress* (denoised). We therefore store the *averaged* iterate for schedule‑free runs in the existing history field `theta` (so downstream code and UI remain unchanged). Internally the true evaluation iterate used for that report is `(1 - beta) * z + beta * x`; because immediately after blending we overwrite the export slot with `x`, the visible path is the smoothed one. Legacy (classic) runs still store the post‑update θ. This conditional meaning is documented here to avoid confusion.

Consistency checks:
* When `beta -> 0`: exported path matches fast iterate; behavior reduces to constant‑lr SPSA without smoothing.
* When `beta -> 1`: exported path is the cumulative average of all past fast iterates (Polyak average), and evaluation iterate is almost identical (`theta ≈ x`).
* In all cases, instantaneous raw step magnitude (before smoothing) is `lr * c_i_k0 * |result|`.

### 6.10 Edge cases & safeguards
* Division by zero: guard when `pairs_total_before = 0` -> first weight `w = 1` (makes `x = z`).
* Large result spikes: optional clamp (`|result| <= lambda_ * N`) from Chapter 4 can still be applied; variance reduction layers are orthogonal.
* Bounds interaction: clipping occurs *after* blending; extreme clips can bias the average—monitor clip frequency to choose `lr`.

### 6.11 Minimal pseudocode (arrival path)
```
def handle_report(result, N, flips, c_vec, state):
    # state: {theta[], z[], iter_pairs, sf_lr, sf_beta, ...}
    lr = state.sf_lr
    beta = state.sf_beta
    # 1. Fast iterate update
    for i in range(len(z)):
        z[i] += lr * c_vec[i] * result * flips[i]
    # 2. Weight
    pairs_before = state.iter_pairs
    pairs_after = pairs_before + N
    w = N / pairs_after
    # 3. (Conceptual) average x  -- not stored separately
    # x_new = (1 - w) * x_old + w * z   (x_old reconstructed if needed)
    # 4. Blend to evaluation iterate
    if beta == 0:
        theta = z[:]  # direct
    else:
        # Reconstruct x_old from stored theta_old and z_old if needed:
        # x_old = (theta_old - (1 - beta) * z_old) / beta
        # x_new = (1 - w) * x_old + w * z
        # theta_new = (1 - beta) * z + beta * x_new
        # Implementation shortcut: compute theta_new directly without persisting x.
        # (Actual code does algebra inline.)
        theta = blend_and_average(theta, z, w, beta)
    clip(theta)
    state.iter_pairs = pairs_after
    store_history(theta_as_display(theta))
```

### 6.12 Summary
Schedule‑free SPSA is constant‑step φ‑space SGD plus Polyak-style averaging and a short blend, implemented so that: (a) only one new scalar learning rate (`sf_lr`) and one smoothing scalar (`sf_beta`) are exposed; (b) heterogeneous asynchronous reports contribute proportionally via `w = N / cumulative_pairs_after`; (c) no extra per‑history vector is stored; (d) the user sees the stabilized `x` trajectory under the familiar `theta` key.

Practical tuning: start from the classic run’s late effective `r_end` as `sf_lr`; pick `sf_beta = 0.9`; watch clipping & improvement slope—adjust `sf_lr` by ~×1.25 or ÷1.25 as needed.

Arrival‑anchored mass adjustment (§4) is unnecessary here because there is no decaying `a_k`; each report’s contribution is already normalized by *pairs* in the averaging weight.


## Chapter 7 — Quick reference

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
