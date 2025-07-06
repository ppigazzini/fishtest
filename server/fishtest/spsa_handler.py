import random
import zlib
from typing import Any

import numpy as np  # type: ignore

# ---------------- Shared helpers (branch-agnostic) ----------------


def _sf_weighting(spsa, N, lr):
    """
    Update and return weighted mass accumulator components.
    Returns (weight_sum_prev, weight_sum_curr, a_k).
    """
    base_weight = lr
    report_weight = base_weight * N
    weight_sum_prev = spsa["sf_weight_sum"]
    weight_sum_curr = weight_sum_prev + report_weight
    spsa["sf_weight_sum"] = weight_sum_curr
    a_k = (report_weight / weight_sum_curr) if weight_sum_curr > 0 else 1.0
    return weight_sum_prev, weight_sum_curr, a_k


def _reconstruct_x_prev_clamped(theta_prev, z_prev, beta1, pmin, pmax):
    if not beta1 or beta1 == 0.0:
        return None
    x_prev = (theta_prev - (1.0 - beta1) * z_prev) / beta1
    if x_prev < pmin:
        return pmin
    if x_prev > pmax:
        return pmax
    return x_prev


def _blend_theta_clamped(z_new, x_new, beta1, pmin, pmax):
    theta_unclamped = z_new if beta1 == 0.0 else (1.0 - beta1) * z_new + beta1 * x_new
    if theta_unclamped < pmin:
        return pmin
    if theta_unclamped > pmax:
        return pmax
    return theta_unclamped


def _history_show_val(beta1, x_new, theta_new):
    return x_new if beta1 and beta1 > 0.0 else theta_new


# ---------------- Adam-specific helpers ----------------


def _adam_update_v_and_denom(v, beta2, N, g_sq_mean, micro_steps, eps):
    """
    Closed-form EMA update of v over N steps using a constant mean of squared signals (g^2).
    This consumes g_sq_mean directly (no squaring inside) and applies bias correction.
    Returns (v_new, denom) where denom = sqrt(v_hat) + eps.
    """
    if beta2 < 1.0:
        beta2_pow_N = beta2**N
        v_new = beta2_pow_N * v + (1.0 - beta2_pow_N) * g_sq_mean
        bc_denom = 1.0 - (beta2**micro_steps)
        v_hat = v_new / bc_denom if bc_denom > 1e-16 else v_new
    else:
        v_new = v
        v_hat = v_new
    denom = (v_hat**0.5) + eps
    return v_new, denom


def _adam_delta_total_step(lr, c, result, flip, denom, N, beta2):
    """
    Directional step in phi normalized by denom, optional k(N,beta2) damping,
    then mapped to theta-space by multiplying with c.
    Returns delta_total_step in theta-space.
    """
    step_phi = (lr * result * flip) / denom
    if N > 1 and 0.0 < beta2 < 1.0:
        sqrt_b2 = beta2**0.5
        tiny = 1e-12
        if abs(1.0 - sqrt_b2) > tiny:
            num = 1.0 - (beta2 ** (0.5 * N))
            den = N * (1.0 - sqrt_b2)
            k = num / den if den != 0.0 else 1.0
        else:
            # Series expansion near beta2 -> 1
            k = 1.0 - ((N - 1) * 0.25) * (1.0 - beta2)
        if not (0.0 < k <= 1.0):
            k = 1.0 if k > 1.0 else 1e-6
        step_phi *= k
    return step_phi * c


def _adam_x_new_clamped(a_k, x_prev, z_new, pmin, pmax):
    """
    Mass-weighted Polyak average for Adam path (no triangular surrogate).
    """
    x_new = (1.0 - a_k) * x_prev + a_k * z_new
    if x_new < pmin:
        x_new = pmin
    elif x_new > pmax:
        x_new = pmax
    return x_new


# ---------------- Online μ2 estimator (report-level only) ----------------


def _mu2_hat(spsa):
    """
    Block-averaged μ2 using only per-report (N, s):
      σ̂² = E[s²/N] - μ̂² · E[N],  μ̂2 = μ̂² + σ̂²
    Falls back to mu2_init if no reports yet. Clamped to [1e-12, 4.0].
    """
    reports = spsa["mu2_reports"]
    if reports <= 0.0:
        return spsa["mu2_init"]

    sum_N = spsa["mu2_sum_N"]
    sum_s = spsa["mu2_sum_s"]
    sum_s2_over_N = spsa["mu2_sum_s2_over_N"]

    if sum_N <= 0.0:
        return spsa["mu2_init"]

    mu = sum_s / sum_N
    E_s2_over_N = sum_s2_over_N / reports
    E_N = sum_N / reports
    sigma2 = E_s2_over_N - (mu * mu) * E_N
    if sigma2 < 0.0:
        sigma2 = 0.0
    mu2 = mu * mu + sigma2
    if mu2 < 1e-12:
        mu2 = 1e-12
    elif mu2 > 4.0:
        mu2 = 4.0
    return mu2


def _mu2_update(spsa, N, s):
    """Update μ2 accumulators after the current report."""
    spsa["mu2_reports"] += 1.0
    spsa["mu2_sum_N"] += N
    spsa["mu2_sum_s"] += s
    spsa["mu2_sum_s2_over_N"] += (s * s) / N


# ---------------- Existing code continues ----------------


def _pack_flips(flips):
    """
    This transforms a list of +-1 into a sequence of bytes
    with the meaning of the individual bits being 1:1, 0:-1.
    """
    return np.packbits(np.array(flips, dtype=np.int8) == 1).tobytes() if flips else b""


def _unpack_flips(packed_flips, length=None):
    """
    The inverse function.
    """
    if not packed_flips:
        return []
    bits = np.unpackbits(np.frombuffer(packed_flips, dtype=np.uint8))
    flips = np.where(bits, 1, -1)
    return flips.tolist() if length is None else flips[:length].tolist()


def _param_clip(param, increment):
    return min(max(param["theta"] + increment, param["min"]), param["max"])


def _generate_data(spsa, iter=None):
    # Explicit Any-typed dict so later additions (sig, task_alive, etc.) are type-safe.
    result: dict[str, Any] = {"w_params": [], "b_params": []}

    if iter is None:
        iter = spsa["iter"]

    # Generate a set of tuning parameters
    iter_local = iter + 1  # start from 1 to avoid division by zero
    for param in spsa["params"]:
        if "gamma" in spsa:
            c = param["c"] / iter_local ** spsa["gamma"]
        else:
            c = param["c"]

        flip = random.choice((-1, 1))

        if "alpha" in spsa and "A" in spsa:
            R = param["a"] / (spsa["A"] + iter_local) ** spsa["alpha"] / c**2
        else:
            R = 0.0

        result["w_params"].append(
            {
                "name": param["name"],
                "value": _param_clip(param, c * flip),
                "R": R,
                "c": c,
                "flip": flip,
            }
        )
        # These are only used by the worker
        result["b_params"].append(
            {
                "name": param["name"],
                "value": _param_clip(param, -c * flip),
            }
        )

    return result

    def _add_to_history(spsa, num_games, w_params, show_vals):
        """
        Simple, uniform-per-pair sampling (matches master):
        - fixed target samples based on number of params
        - period derived from run-level num_games
        - stores show_val under the 'theta' key for UI compatibility:
            - schedule-free: x_new if beta1 > 0, else theta_new
            - classic: theta after classic update
        """
        n_params = len(spsa["params"])
        samples = 100 if n_params < 100 else 10000 / n_params if n_params < 1000 else 1
        period = num_games / 2 / samples

        if "param_history" not in spsa:
            spsa["param_history"] = []

        if len(spsa["param_history"]) + 1 <= spsa["iter"] / period:
            summary = [
                {
                    "theta": show_val,
                    "c": w_param["c"],
                    "z": param.get("z", show_val),
                    "v": param.get("v", 0.0),
                }
                for param, w_param, show_val in zip(spsa["params"], w_params, show_vals)
            ]
            spsa["param_history"].append(summary)


# ---------------- Per-parameter helpers ----------------


def _classic_param_update(param, w_param, result):
    """
    Classic SPSA per-parameter update (legacy path).
    Mutates param["theta"] (clamped) and returns the display value (theta).
    """
    c = w_param["c"]
    flip = w_param["flip"]
    R = w_param["R"]
    param["theta"] = min(
        max(param["theta"] + R * c * result * flip, param["min"]), param["max"]
    )
    return param["theta"]


def _schedule_free_adam_param_update(
    param,
    w_param,
    *,
    result,
    N,
    lr,
    beta1,
    beta2,
    eps,
    micro_steps,
    a_k,
    g2_mean,
):
    c = w_param["c"]
    flip = w_param["flip"]
    z_prev = param["z"]

    # v and denom using μ̂2 (mean of squares), not (mean)^2
    v_new, denom = _adam_update_v_and_denom(
        param["v"], beta2, N, g2_mean, micro_steps, eps
    )

    # Fast iterate step (theta-space)
    delta_total_step = _adam_delta_total_step(lr, c, result, flip, denom, N, beta2)
    z_new = z_prev + delta_total_step

    # Surrogate reconstruction and averaging
    if beta1 == 0.0:
        x_new = z_new  # unused for blending; only for show when beta1==0
        theta_new = _blend_theta_clamped(
            z_new, x_new, beta1, param["min"], param["max"]
        )
    else:
        x_prev = _reconstruct_x_prev_clamped(
            param["theta"], z_prev, beta1, param["min"], param["max"]
        )
        x_new = _adam_x_new_clamped(a_k, x_prev, z_new, param["min"], param["max"])
        theta_new = _blend_theta_clamped(
            z_new, x_new, beta1, param["min"], param["max"]
        )

    # Persist
    param["z"] = z_new
    param["theta"] = theta_new
    param["v"] = v_new

    return _history_show_val(beta1, x_new, theta_new)


# ---------------- Handler ----------------


class SPSAHandler:
    def __init__(self, rundb):
        self.get_run = rundb.get_run
        if rundb.is_primary_instance():
            self.buffer = rundb.buffer
        self.active_run_lock = rundb.active_run_lock

    def request_spsa_data(self, run_id, task_id):
        with self.active_run_lock(run_id):
            return self.__request_spsa_data(run_id, task_id)

    def __request_spsa_data(self, run_id, task_id):
        run = self.get_run(run_id)
        task = run["tasks"][task_id]
        spsa = run["args"]["spsa"]

        # Check if the worker is still working on this task.
        if not task["active"]:
            info = "request_spsa_data: task {}/{} is not active".format(run_id, task_id)
            print(info, flush=True)
            return {"task_alive": False, "info": info}

        result = _generate_data(spsa)
        packed_flips = _pack_flips([w_param["flip"] for w_param in result["w_params"]])
        task["spsa_params"] = {}
        task["spsa_params"]["iter"] = spsa["iter"]
        task["spsa_params"]["packed_flips"] = packed_flips
        self.buffer(run)
        # The signature defends against server crashes and worker bugs
        sig = zlib.crc32(packed_flips)
        result["sig"] = sig
        result["task_alive"] = True
        return result

    def update_spsa_data(self, run_id, task_id, spsa_results):
        with self.active_run_lock(run_id):
            return self.__update_spsa_data(run_id, task_id, spsa_results)

    def __update_spsa_data(self, run_id, task_id, spsa_results):
        run = self.get_run(run_id)
        task = run["tasks"][task_id]
        spsa = run["args"]["spsa"]

        if "spsa_params" not in task:
            print(
                f"update_spsa_data: spsa_params not found for {run_id}/{task_id}. Skipping update...",
                flush=True,
            )
            return
        task_spsa_params = task["spsa_params"]
        del task["spsa_params"]

        sig = spsa_results.get("sig", 0)
        if sig != zlib.crc32(task_spsa_params["packed_flips"]):
            print(
                f"update_spsa_data: spsa_params for {run_id}/{task_id} do not match signature. Skipping update...",
                flush=True,
            )
            return

        # Regenerate perturbation metadata (c, R, flip) deterministically from stored iter
        w_params = _generate_data(spsa, iter=task_spsa_params["iter"])["w_params"]
        flips = _unpack_flips(task_spsa_params["packed_flips"], length=len(w_params))
        for idx, w_param in enumerate(w_params):
            w_param["flip"] = flips[idx]
            del w_param["value"]  # Never trust back worker-side values

        # Aggregate outcomes
        result = spsa_results["wins"] - spsa_results["losses"]
        N = spsa_results["num_games"] // 2  # symmetric SPSA pairs
        if N <= 0:
            print(
                f"update_spsa_data: N=0 for {run_id}/{task_id}, skipping.", flush=True
            )
            return

        # Advance total consumed pair counter
        spsa["iter"] += N

        # Schedule-free Adam parameters
        lr = spsa["sf_lr"]
        beta1 = spsa["sf_beta1"]
        beta2 = spsa["sf_beta2"]
        eps = spsa["sf_eps"]
        micro_steps = spsa["iter"]

        # Unified weighted mass update (only a_k used in Adam path)
        _, _, a_k = _sf_weighting(spsa, N, lr)

        # μ̂2 (mean of squares) pre-block for Adam normalization
        g2_mean = _mu2_hat(spsa)

        show_vals = []
        for param, w_param in zip(spsa["params"], w_params):
            if "z" not in param:
                show_val = _classic_param_update(param, w_param, result)
            else:
                show_val = _schedule_free_adam_param_update(
                    param,
                    w_param,
                    result=result,
                    N=N,
                    lr=lr,
                    beta1=beta1,
                    beta2=beta2,
                    eps=eps,
                    micro_steps=micro_steps,
                    a_k=a_k,
                    g2_mean=g2_mean,
                )
            show_vals.append(show_val)

        # Update μ2 accumulators post-block
        _mu2_update(spsa, N, result)

        _add_to_history(spsa, run["args"]["num_games"], w_params, show_vals)
        self.buffer(run)

    def get_spsa_data(self, run_id):
        run = self.get_run(run_id)
        return run["args"].get("spsa", {})
