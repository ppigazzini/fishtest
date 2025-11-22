# simul experiments for schedule-free SPSA

This folder contains **experimental** scripts for studying macro vs micro behavior of SPSA and its schedule-free variants (SGD and AdamW backends). They are analysis tools for the `sf-sgd` / `sf-adam` branches, not part of the production fishtest server or worker.

## Prerequisites

- Python 3.10+ (same environment you use for the fishtest repo)
- `uv` installed and available on your PATH

Run all commands from the `simul/` folder, e.g.:

```bash
cd /path/to/fishtest/simul
```

## Running the bias experiments

The bias scripts generate trajectories and (optionally) plots comparing:
- sequential micro updates vs a single macro update per report,
- classic SPSA vs corrected macro,
- schedule-free SGD vs AdamW backends.

### Classic SPSA aggregation bias

```bash
uv run bias_spsa.py
```

This runs the classic SPSA macro vs micro experiments (corrected vs uncorrected; original vs shuffled). See `simul/BIAS.md` for interpretation of the charts.

### Schedule-free SGD backend

```bash
uv run bias_sf_sgd.py
```

This runs schedule-free SGD experiments, comparing:
- macro closed form,
- micro with constant-mean surrogate,
- micro with the real per-outcome sequence.

### Schedule-free AdamW backend

```bash
uv run bias_sf_adam.py
```

This runs schedule-free AdamW experiments for the SPSA setting, using the online μ₂ estimator and block-level damping `k(N, β₂)`.

## Notes

- These scripts are safe to run locally; they do **not** talk to the production fishtest database or worker processes.
- For more background on the math and the approximations, see:
  - `SPSA.md` (overall SPSA + schedule-free SPSA design)
  - `simul/BIAS.md` (macro vs micro aggregation and sequence effects)
