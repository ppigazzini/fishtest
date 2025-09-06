import random
import zlib

import numpy as np


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

    if iter is None:
        iter = spsa["iter"]

    # Generate a set of tuning parameters
    iter_local = iter + 1  # start from 1 to avoid division by zero
    for param in spsa["params"]:
        c = param["c"] / iter_local ** spsa["gamma"]
        flip = random.choice((-1, 1))
        result["w_params"].append(
            {
                "name": param["name"],
                "value": _param_clip(param, c * flip),
                "R": param["a"] / (spsa["A"] + iter_local) ** spsa["alpha"] / c**2,
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


def _add_to_history(spsa, num_games, w_params):
    # Compute the update frequency so that the required storage does not depend
    # on the the number of parameters. We have to recompute this every time since
    # the user may have modified the run.
    n_params = len(spsa["params"])
    samples = 100 if n_params < 100 else 10000 / n_params if n_params < 1000 else 1
    period = num_games / 2 / samples

    if "param_history" not in spsa:
        spsa["param_history"] = []
    if len(spsa["param_history"]) + 1 <= spsa["iter"] / period:
        # Schedule-free: display the long averaged iterate x (reconstructed), not the fast z.
        # Relation: theta = (1 - beta1) * z + beta1 * x  =>  x = (theta - (1 - beta1)*z)/beta1
        beta1 = spsa.get("sf_beta1", 0.9)
        summary = []
        for w_param, spsa_param in zip(w_params, spsa["params"]):
            if "z" in spsa_param and beta1 > 0:
                x_val = (spsa_param["theta"] - (1 - beta1) * spsa_param["z"]) / beta1
                show_val = x_val
            else:
                show_val = spsa_param["theta"]
            summary.append({"theta": show_val, "R": w_param["R"], "c": w_param["c"]})
        spsa["param_history"].append(summary)


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
        # Aggregate outcomes
        result = spsa_results["wins"] - spsa_results["losses"]
        N = spsa_results["num_games"] // 2
        if N <= 0:
            print(
                f"update_spsa_data: N=0 for {run_id}/{task_id}, skipping.", flush=True
            )
            return

        # Advance total consumed pair counter (still used for UI)
        spsa["iter"] += N

        # ------------------ Schedule-free SGD (Enhanced: mean gradient + lr-weighted mass) ------------------
        #
        # MODEL SUMMARY:
        #   We treat each arriving report (num_pairs = N) as ONE aggregated stochastic sample whose
        #   gradient in φ-space is the MEAN over its pairs: g_phi = (result / N) * flip.
        #   The fast iterate z updates with a constant per-pair learning rate (with optional warmup).
        #   A long Polyak-style average (implicit x) is maintained via a cumulative weight mass:
        #       weight_sum += lr_eff * N
        #       a_k = (lr_eff * N) / weight_sum
        #   The evaluation iterate theta blends z and x with beta1.
        #
        #   Using lr_eff * N (instead of just N) discounts very early, partially warmed steps
        #   so they do not dominate the average—mirrors the AdamW schedule-free variant fairness.
        #
        # LEGACY DIFFERENCES (old sf-sgd):
        #   - Previously: step used raw result (∝ N) and averaging weight w = N / total_pairs.
        #   - Now: step uses mean result/N and averaging mass is lr_eff * N (fair & scale-free).
        #   - Behavior matches old code after warmup up to a multiplicative reparameterization,
        #     but removes bias favoring large-N workers and early oversized (unwarmed) steps.
        #
        # OPTIONAL VARIANCE CLAMP:
        #   If 'sf_var_clamp' = λ > 0, clamp result to ± λ * N BEFORE dividing by N.
        #
        # STATE REQUIREMENTS:
        #   Per param: theta, z.
        #   Global: iter (total pairs), sf_weight_sum (cumulative lr_eff * N). Lazily initialized.
        #
        # SENTINEL (do not remove this block without preserving its rationale).

        lr = spsa.get("sf_lr", 0.0025)
        beta1 = spsa.get("sf_beta1", 0.9)
        clamp_lambda = spsa.get("sf_var_clamp", 0.0)

        # Warmup (linear ramp of learning rate over first ~10% of planned pairs unless overridden)
        total_pairs_planned = max(1, run["args"]["num_games"] // 2)
        warmup_pairs_default = max(1, int(0.1 * total_pairs_planned))
        warmup_pairs = spsa.get("sf_warmup_pairs", warmup_pairs_default)
        current_pairs = spsa["iter"]
        warmup_scale = (
            current_pairs / warmup_pairs if current_pairs < warmup_pairs else 1.0
        )
        lr_eff = lr * warmup_scale

        # Optional variance clamp (before mean)
        if clamp_lambda and clamp_lambda > 0.0:
            limit = clamp_lambda * N
            result_clamped = max(min(result, limit), -limit)
        else:
            result_clamped = result

        # Mean gradient scalar (per pair) in φ-space (scale-free across heterogeneous N)
        g_scalar = result_clamped / N

        # Learning-rate & pair-weighted averaging mass (lazy init)
        if "sf_weight_sum" not in spsa:
            spsa["sf_weight_sum"] = 0.0
        spsa["sf_weight_sum"] += lr_eff * N
        weight_sum = spsa["sf_weight_sum"]
        a_k = (lr_eff * N) / weight_sum if weight_sum > 0.0 else 1.0

        for idx, param in enumerate(spsa["params"]):
            c = w_params[idx]["c"]
            flip = w_params[idx]["flip"]

            # Classic fallback for legacy parameters without schedule-free state
            if "z" not in param:
                R = w_params[idx]["R"]
                classic_step = R * c * result * flip
                param["theta"] = _param_clip(param, classic_step)
                continue

            z_old = param["z"]

            # φ-gradient component
            g_phi = g_scalar * flip

            # Plain SGD fast iterate step (no RMS / no weight decay)
            # θ-space delta: delta_z = lr_eff * c * g_phi = lr_eff * c * (result/N) * flip
            delta_z = lr_eff * c * g_phi
            z_new = z_old + delta_z

            if beta1 == 0.0:
                theta_new = z_new
            else:
                # Closed-form θ update eliminating explicit x:
                # theta_new = (1 - a_k)*theta_old + (1 - beta1 + beta1 * a_k)*z_new - (1 - a_k)*(1 - beta1)*z_old
                theta_new = (
                    (1.0 - a_k) * param["theta"]
                    + (1.0 - beta1 + beta1 * a_k) * z_new
                    - (1.0 - a_k) * (1.0 - beta1) * z_old
                )

            if theta_new < param["min"]:
                theta_new = param["min"]
            elif theta_new > param["max"]:
                theta_new = param["max"]

            param["theta"] = theta_new
            param["z"] = z_new

        _add_to_history(spsa, run["args"]["num_games"], w_params)
        self.buffer(run)

    def get_spsa_data(self, run_id):
        run = self.get_run(run_id)
        return run["args"].get("spsa", {})
