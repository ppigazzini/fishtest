import random
import zlib

import numpy as np

# ---------------- Shared helpers (branch-agnostic) ----------------


def _sf_weighting(spsa, N, lr):
    """
    Update and return weighted mass accumulator components.
    Returns (weight_sum_prev, weight_sum_curr, a_k).
    Behavior:
      base weight    = lr
      report_weight  = base weight * N
      weight_sum_prev = spsa["sf_weight_sum"]
      weight_sum_curr = weight_sum_prev + report_weight
      spsa["sf_weight_sum"] = weight_sum_curr
      a_k = report_weight / weight_sum_curr
    """
    base_weight = lr
    report_weight = base_weight * N
    weight_sum_prev = spsa["sf_weight_sum"]
    weight_sum_curr = weight_sum_prev + report_weight
    spsa["sf_weight_sum"] = weight_sum_curr
    a_k = (report_weight / weight_sum_curr) if weight_sum_curr > 0 else 1.0
    return weight_sum_prev, weight_sum_curr, a_k


def _reconstruct_x_prev_clamped(theta_prev, z_prev, beta, pmin, pmax):
    """
    Reconstruct Polyak surrogate x_prev and clamp it into [pmin, pmax].
    Returns x_prev or None if beta == 0.
    """
    if not beta or beta == 0.0:
        return None
    x_prev = (theta_prev - (1.0 - beta) * z_prev) / beta
    if x_prev < pmin:
        return pmin
    if x_prev > pmax:
        return pmax
    return x_prev


def _blend_theta_clamped(z_new, x_new, beta, pmin, pmax):
    """
    Blend z/x into theta and clamp into [pmin, pmax].
    If beta == 0, x_new is ignored and theta = z_new.
    """
    theta_unclamped = z_new if beta == 0.0 else (1.0 - beta) * z_new + beta * x_new
    if theta_unclamped < pmin:
        return pmin
    if theta_unclamped > pmax:
        return pmax
    return theta_unclamped


def _history_show_val(beta, x_new, theta_new):
    """
    History value: x_new when beta > 0, else theta_new.
    """
    return x_new if beta and beta > 0.0 else theta_new


# ---------------- SGD-specific helpers ----------------


def _sgd_delta_total_step(lr, c, result, flip):
    """
    Fast iterate increment (no division by N), θ-space after multiplying by c.
    """
    return lr * c * result * flip


def _sgd_x_new(
    weight_sum_prev,
    weight_sum_curr,
    x_prev,
    z_prev,
    delta_total_step,
    report_weight,
    weight,
    N,
):
    """
    Triangular surrogate Polyak numerator with weighted mass, then divide by weight_sum_curr.
    Caller clamps x_new after.
    """
    tri_factor = (N + 1) / 2.0
    return (
        weight_sum_prev * x_prev
        + report_weight * z_prev
        + weight * delta_total_step * tri_factor
    ) / weight_sum_curr


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
    result = {"w_params": [], "b_params": []}

    # iter argument is ignored as c is constant

    # Generate a set of tuning parameters
    for param in spsa["params"]:
        c = param["c"]
        flip = random.choice((-1, 1))
        result["w_params"].append(
            {
                "name": param["name"],
                "value": _param_clip(param, c * flip),
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
        - schedule-free: x_new if beta > 0, else theta_new
    """
    n_params = len(spsa["params"])
    samples = 100 if n_params < 100 else 10000 / n_params if n_params < 1000 else 1
    period = num_games / 2 / samples

    if "param_history" not in spsa:
        spsa["param_history"] = []

    if len(spsa["param_history"]) + 1 <= spsa["iter"] / period:
        summary = [
            {"theta": show_val, "c": w_param["c"]}
            for w_param, show_val in zip(w_params, show_vals)
        ]
        spsa["param_history"].append(summary)


def _schedule_free_sgd_param_update(
    param, w_param, result, N, lr, beta, weight_sum_prev, weight_sum_curr
):
    """
    Schedule-free lean update with per-report weighting.

    Generalized weighted surrogate average:
        weight = lr
        report_weight = weight * N
        weight_sum_prev = previous cumulative mass
        weight_sum_curr = weight_sum_prev + report_weight

    Triangular surrogate (kept as current biased form):
        tri_factor = (N + 1) / 2

    Surrogate long average:
        x_new = (weight_sum_prev * x_prev
                 + report_weight * z_prev
                 + weight * delta_total_step * tri_factor) / weight_sum_curr

    Where:
        delta_total_step = lr * c * result * flip   (no division by N)
    """
    c = w_param["c"]
    flip = w_param["flip"]
    z_prev = param["z"]

    # Reconstruct x_prev if needed (clamped)
    x_prev = _reconstruct_x_prev_clamped(
        param["theta"], z_prev, beta, param["min"], param["max"]
    )

    # Aggregated fast iterate increment (no division by N)
    delta_total_step = _sgd_delta_total_step(lr, c, result, flip)
    z_new = z_prev + delta_total_step

    # Report weighting
    weight = lr
    report_weight = weight * N

    if beta == 0.0:
        # No surrogate path; export fast iterate
        theta_new = _blend_theta_clamped(z_new, z_new, beta, param["min"], param["max"])
        param["theta"] = theta_new
        param["z"] = z_new
        return _history_show_val(beta, None, theta_new)

    # Weighted surrogate average (then clamp)
    x_new = _sgd_x_new(
        weight_sum_prev,
        weight_sum_curr,
        x_prev,
        z_prev,
        delta_total_step,
        report_weight,
        weight,
        N,
    )
    if x_new < param["min"]:
        x_new = param["min"]
    elif x_new > param["max"]:
        x_new = param["max"]

    theta_new = _blend_theta_clamped(z_new, x_new, beta, param["min"], param["max"])

    param["theta"] = theta_new
    param["z"] = z_new
    return _history_show_val(beta, x_new, theta_new)


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

        # Catch some issues which may occur after a server crash
        if "spsa_params" not in task:
            print(
                f"update_spsa_data: spsa_params not found for {run_id}/{task_id}. Skipping update...",
                flush=True,
            )
            return
        task_spsa_params = task["spsa_params"]
        # Make sure we cannot call update_spsa_data again with these data
        del task["spsa_params"]

        sig = spsa_results.get("sig", 0)
        if sig != zlib.crc32(task_spsa_params["packed_flips"]):
            print(
                f"update_spsa_data: spsa_params for {run_id}/{task_id}",
                "do not match the signature sent by the worker.",
                "Skipping update...",
                flush=True,
            )
            return

        # Reconstruct spsa data from the task data
        w_params = _generate_data(spsa, iter=task_spsa_params["iter"])["w_params"]
        flips = _unpack_flips(task_spsa_params["packed_flips"], length=len(w_params))
        for idx, w_param in enumerate(w_params):
            w_param["flip"] = flips[idx]
            del w_param["value"]  # for safety!

        # Update the current theta based on the results from the worker
        result = spsa_results["wins"] - spsa_results["losses"]
        N = spsa_results["num_games"] // 2

        # Advance total consumed pair counter
        spsa["iter"] += N

        # Schedule-free globals
        lr = spsa["sf_lr"]
        beta = spsa["sf_beta"]

        # Unified weighted mass update
        weight_sum_prev, weight_sum_curr, _ = _sf_weighting(spsa, N, lr)

        # Apply per-parameter updates and collect show values for history
        show_vals = []
        for param, w_param in zip(spsa["params"], w_params):
            show_val = _schedule_free_sgd_param_update(
                param,
                w_param,
                result,
                N,
                lr,
                beta,
                weight_sum_prev,
                weight_sum_curr,
            )
            show_vals.append(show_val)

        _add_to_history(spsa, run["args"]["num_games"], w_params, show_vals)
        self.buffer(run)

    def get_spsa_data(self, run_id):
        run = self.get_run(run_id)
        return run["args"].get("spsa", {})
