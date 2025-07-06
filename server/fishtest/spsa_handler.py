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

    iter_local = iter + 1  # start from 1 to avoid division by zero
    is_classic = all(key in spsa for key in ("A", "alpha", "gamma"))

    for param in spsa["params"]:
        c = param["c"] / iter_local ** spsa["gamma"] if is_classic else param["c"]
        flip = random.choice((-1, 1))
        w_param = {
            "name": param["name"],
            "value": _param_clip(param, c * flip),
            "c": c,
            "flip": flip,
        }
        if is_classic:
            w_param["R"] = param["a"] / (spsa["A"] + iter_local) ** spsa["alpha"] / c**2
        result["w_params"].append(w_param)
        result["b_params"].append(
            {
                "name": param["name"],
                "value": _param_clip(param, -c * flip),
            }
        )

    return result


def _add_to_history(spsa, num_games, w_params, show_vals):
    n_params = len(spsa["params"])
    samples = 100 if n_params < 100 else 10000 / n_params if n_params < 1000 else 1
    period = num_games / 2 / samples

    if "param_history" not in spsa:
        spsa["param_history"] = []

    if len(spsa["param_history"]) + 1 <= spsa["iter"] / period:
        summary = []
        for param, w_param, show_val in zip(spsa["params"], w_params, show_vals):
            row = {"theta": show_val, "c": w_param["c"]}
            if "R" in w_param:
                row["R"] = w_param["R"]
            if "z" in param:
                row["z"] = param["z"]
            if "v" in param:
                row["v"] = param["v"]
            summary.append(row)
        spsa["param_history"].append(summary)


def _classic_param_update(param, w_param, result):
    param["theta"] = _param_clip(param, w_param["R"] * w_param["c"] * result * w_param["flip"])
    return param["theta"]


def _sf_weighting(spsa, N, lr):
    report_weight = lr * N
    weight_sum_prev = spsa["sf_weight_sum"]
    weight_sum_curr = weight_sum_prev + report_weight
    spsa["sf_weight_sum"] = weight_sum_curr
    return weight_sum_prev, weight_sum_curr


def _reconstruct_x_prev_clamped(theta_prev, z_prev, beta, pmin, pmax):
    if beta == 0.0:
        return None
    x_prev = (theta_prev - (1.0 - beta) * z_prev) / beta
    return min(max(x_prev, pmin), pmax)


def _blend_theta_clamped(z_new, x_new, beta, pmin, pmax):
    theta_unclamped = z_new if beta == 0.0 else (1.0 - beta) * z_new + beta * x_new
    return min(max(theta_unclamped, pmin), pmax)


def _history_show_val(beta, x_new, theta_new):
    return x_new if beta > 0.0 else theta_new


def _sgd_delta_total_step(lr, c, result, flip):
    return lr * c * result * flip


def _sgd_x_new(
    weight_sum_prev,
    weight_sum_curr,
    x_prev,
    z_prev,
    delta_total_step,
    report_weight,
    lr,
    N,
):
    tri_factor = (N + 1) / 2.0
    return (
        weight_sum_prev * x_prev
        + report_weight * z_prev
        + lr * delta_total_step * tri_factor
    ) / weight_sum_curr


def _schedule_free_sgd_param_update(
    param, w_param, result, N, lr, beta, weight_sum_prev, weight_sum_curr
):
    c = w_param["c"]
    flip = w_param["flip"]
    z_prev = param["z"]

    x_prev = _reconstruct_x_prev_clamped(
        param["theta"], z_prev, beta, param["min"], param["max"]
    )
    delta_total_step = _sgd_delta_total_step(lr, c, result, flip)
    z_new = z_prev + delta_total_step
    report_weight = lr * N

    if beta == 0.0:
        theta_new = _blend_theta_clamped(z_new, z_new, beta, param["min"], param["max"])
        param["theta"] = theta_new
        param["z"] = z_new
        param.setdefault("v", 0.0)
        return _history_show_val(beta, None, theta_new)

    x_new = _sgd_x_new(
        weight_sum_prev,
        weight_sum_curr,
        x_prev,
        z_prev,
        delta_total_step,
        report_weight,
        lr,
        N,
    )
    x_new = min(max(x_new, param["min"]), param["max"])
    theta_new = _blend_theta_clamped(z_new, x_new, beta, param["min"], param["max"])

    param["theta"] = theta_new
    param["z"] = z_new
    param.setdefault("v", 0.0)
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

        if not task["active"]:
            info = "request_spsa_data: task {}/{} is not active".format(run_id, task_id)
            print(info, flush=True)
            return {"task_alive": False, "info": info}

        result = _generate_data(spsa)
        packed_flips = _pack_flips([w_param["flip"] for w_param in result["w_params"]])
        task["spsa_params"] = {"iter": spsa["iter"], "packed_flips": packed_flips}
        self.buffer(run)
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

        w_params = _generate_data(spsa, iter=task_spsa_params["iter"])["w_params"]
        flips = _unpack_flips(task_spsa_params["packed_flips"], length=len(w_params))
        for idx, w_param in enumerate(w_params):
            w_param["flip"] = flips[idx]
            del w_param["value"]

        result = spsa_results["wins"] - spsa_results["losses"]
        N = spsa_results["num_games"] // 2
        if N <= 0:
            print(f"update_spsa_data: N=0 for {run_id}/{task_id}, skipping.", flush=True)
            return

        spsa["iter"] += N

        if "sf_lr" in spsa:
            lr = spsa["sf_lr"]
            beta = spsa["sf_beta"]
            weight_sum_prev, weight_sum_curr = _sf_weighting(spsa, N, lr)
            show_vals = []
            for param, w_param in zip(spsa["params"], w_params):
                if "z" not in param:
                    param["z"] = param["theta"]
                    param["v"] = 0.0
                show_vals.append(
                    _schedule_free_sgd_param_update(
                        param,
                        w_param,
                        result,
                        N,
                        lr,
                        beta,
                        weight_sum_prev,
                        weight_sum_curr,
                    )
                )
        else:
            show_vals = []
            for param, w_param in zip(spsa["params"], w_params):
                show_vals.append(_classic_param_update(param, w_param, result))

        _add_to_history(spsa, run["args"]["num_games"], w_params, show_vals)
        self.buffer(run)

    def get_spsa_data(self, run_id):
        run = self.get_run(run_id)
        return run["args"].get("spsa", {})
