"""Shared template helpers for Mako and Jinja2 renderers."""

from __future__ import annotations

import binascii
import datetime
import urllib.parse
from urllib.parse import quote_plus

from fishtest.stats import LLRcalc, stat_util
from fishtest.stats import sprt as sprt_module
from fishtest.util import (
    diff_url,
    display_residual,
    format_bounds,
    format_date,
    format_group,
    format_results,
    format_time_ago,
    get_cookie,
    is_active_sprt_ltc,
    tests_repo,
    worker_name,
)


def urlencode(value: object) -> str:
    """URL-encode a value for use in templates."""
    return quote_plus(str(value))


def run_tables_prefix(username: object | None) -> str:
    """Compute the toggle prefix used by run_tables templates."""
    try:
        if not username:
            return ""
        text = str(username)
    except (TypeError, ValueError):
        return ""
    token = binascii.hexlify(text.encode()).decode()
    return f"user{token}_"


def build_contributors_summary(users: list[dict]) -> dict:
    """Build summary counts for contributors."""
    summary = {
        "testers": 0,
        "developers": 0,
        "active_testers": 0,
        "cpu_hours": 0,
        "games": 0,
        "tests": 0,
    }
    last_updated_min = datetime.datetime.min.replace(tzinfo=datetime.UTC)
    for user in users:
        if user.get("last_updated") != last_updated_min:
            summary["testers"] += 1
        if user.get("tests", 0) > 0:
            summary["developers"] += 1
        if user.get("games_per_hour", 0) > 0:
            summary["active_testers"] += 1
        summary["cpu_hours"] += int(user.get("cpu_hours", 0))
        summary["games"] += int(user.get("games", 0))
        summary["tests"] += int(user.get("tests", 0))
    return summary


def build_contributors_rows(
    users: list[dict],
    *,
    is_approver: bool,
) -> list[dict]:
    """Build template-ready contributor rows."""
    rows = []
    for user in users:
        username = user.get("username", "")
        last_updated = user.get("last_updated")
        last_updated_label = format_time_ago(last_updated) if last_updated else ""
        last_updated_sort = (
            -last_updated.timestamp() if getattr(last_updated, "timestamp", None) else 0
        )
        rows.append(
            {
                "username": username,
                "user_url": f"/user/{username}" if is_approver and username else "",
                "last_updated_label": last_updated_label,
                "last_updated_sort": last_updated_sort,
                "games_per_hour": int(user.get("games_per_hour", 0)),
                "cpu_hours": int(user.get("cpu_hours", 0)),
                "games": int(user.get("games", 0)),
                "tests": int(user.get("tests", 0)),
                "tests_repo_url": user.get("tests_repo", ""),
                "tests_user_url": f"/tests/user/{urllib.parse.quote(username)}"
                if username
                else "",
            }
        )
    return rows


def clip_long(text: str, max_length: int = 20) -> str:
    """Clip long strings and add an ellipsis."""
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text


def pdf_to_string(
    pdf: list[tuple[float, float]],
    decimals: tuple[int, int] = (2, 5),
) -> str:
    """Format a PDF list as a compact string."""
    return (
        "{"
        + ", ".join(
            f"{value:.{decimals[0]}f}: {prob:.{decimals[1]}f}" for value, prob in pdf
        )
        + "}"
    )


def list_to_string(values: list[float], decimals: int = 6) -> str:
    """Format a list of floats as a compact string."""
    return "[" + ", ".join(f"{value:.{decimals}f}" for value in values) + "]"


def t_conf(
    avg: float,
    var: float,
    skewness: float,
    exkurt: float,
) -> tuple[float, float]:
    """Compute t-confidence and its variance term."""
    t = (avg - 0.5) / var**0.5
    # limit for rounding error
    var_t = max(1 - t * skewness + 0.25 * t**2 * (exkurt + 2), 0)
    return t, var_t


def is_elo_pentanomial_run(run: dict) -> bool:
    """Return whether the run uses Elo pentanomial results."""
    args = run.get("args", {})
    results = run.get("results", {})
    return "sprt" not in args and "spsa" not in args and "pentanomial" in results


def _nelo_pentanomial_details(results5: list[int]) -> dict:
    nelo_coeff = LLRcalc.nelo_divided_by_nt / (2**0.5)
    z975 = stat_util.Phi_inv(0.975)
    n5, pdf5 = LLRcalc.results_to_pdf(results5)
    avg5, var5, skewness5, exkurt5 = LLRcalc.stats_ex(pdf5)
    t5, var_t5 = t_conf(avg5, var5, skewness5, exkurt5)
    nelo5 = nelo_coeff * t5
    nelo5_delta = nelo_coeff * z975 * (var_t5 / n5) ** 0.5
    return {
        "n5": n5,
        "pdf5": pdf5,
        "avg5": avg5,
        "var5": var5,
        "skewness5": skewness5,
        "exkurt5": exkurt5,
        "t5": t5,
        "var_t5": var_t5,
        "nelo5": nelo5,
        "nelo5_delta": nelo5_delta,
    }


def nelo_pentanomial_summary(run: dict) -> str | None:
    """Build a summary line for Elo pentanomial results."""
    if not is_elo_pentanomial_run(run):
        return None

    results5 = run["results"]["pentanomial"]
    details = _nelo_pentanomial_details(results5)
    nelo5 = details["nelo5"]
    nelo5_delta = details["nelo5_delta"]

    if any(results5[0:2]):
        pairs_ratio = sum(results5[3:]) / sum(results5[0:2])
    elif any(results5[3:]):
        pairs_ratio = float("inf")
    else:
        pairs_ratio = float("nan")

    return (
        f"nElo: {nelo5:.2f} &plusmn; {nelo5_delta:.1f} (95%) "
        f"PairsRatio: {pairs_ratio:.2f}"
    )


def build_tests_stats_context(run: dict) -> dict:
    """Build a template-friendly stats payload for tests_stats."""
    args = run.get("args", {})
    results = run.get("results", {})
    has_sprt = "sprt" in args
    has_pentanomial = "pentanomial" in results
    has_spsa = "spsa" in args

    z975 = stat_util.Phi_inv(0.975)
    nelo_divided_by_nt = LLRcalc.nelo_divided_by_nt

    results3 = [results["losses"], results["draws"], results["wins"]]
    results3_ = LLRcalc.regularize(results3)
    draw_ratio = results3_[1] / sum(results3_)
    n3, pdf3 = LLRcalc.results_to_pdf(results3)
    games3 = n3
    avg3, var3, skewness3, exkurt3 = LLRcalc.stats_ex(pdf3)
    stdev3 = var3**0.5
    pdf3_s = pdf_to_string(pdf3)
    avg3_l = avg3 - z975 * (var3 / n3) ** 0.5
    avg3_u = avg3 + z975 * (var3 / n3) ** 0.5
    var3_l = var3 * (1 - z975 * ((exkurt3 + 2) / n3) ** 0.5)
    var3_u = var3 * (1 + z975 * ((exkurt3 + 2) / n3) ** 0.5)
    stdev3_l = var3_l**0.5 if var3_l >= 0 else 0.0
    stdev3_u = var3_u**0.5
    t3, var_t3 = t_conf(avg3, var3, skewness3, exkurt3)
    t3_l = t3 - z975 * (var_t3 / n3) ** 0.5
    t3_u = t3 + z975 * (var_t3 / n3) ** 0.5
    nelo3 = nelo_divided_by_nt * t3
    nelo3_l = nelo_divided_by_nt * t3_l
    nelo3_u = nelo_divided_by_nt * t3_u

    pent = {}
    results5 = None
    results5_ = None
    n5 = None
    pdf5 = None
    if has_pentanomial:
        results5 = results["pentanomial"]
        results5_ = LLRcalc.regularize(results5)
        pentanomial_draw_ratio = results5_[2] / sum(results5_)
        details5 = _nelo_pentanomial_details(results5)
        n5 = details5["n5"]
        pdf5 = details5["pdf5"]
        avg5 = details5["avg5"]
        var5 = details5["var5"]
        skewness5 = details5["skewness5"]
        exkurt5 = details5["exkurt5"]
        t5 = details5["t5"]
        var_t5 = details5["var_t5"]

        games5 = 2 * n5
        var5_per_game = 2 * var5
        stdev5_per_game = var5_per_game**0.5
        pdf5_s = pdf_to_string(pdf5)
        avg5_l = avg5 - z975 * (var5 / n5) ** 0.5
        avg5_u = avg5 + z975 * (var5 / n5) ** 0.5
        var5_per_game_l = var5_per_game * (1 - z975 * ((exkurt5 + 2) / n5) ** 0.5)
        var5_per_game_u = var5_per_game * (1 + z975 * ((exkurt5 + 2) / n5) ** 0.5)
        stdev5_per_game_l = var5_per_game_l**0.5 if var5_per_game_l >= 0 else 0.0
        stdev5_per_game_u = var5_per_game_u**0.5
        t5_l = t5 - z975 * (var_t5 / n5) ** 0.5
        t5_u = t5 + z975 * (var_t5 / n5) ** 0.5
        sqrt2 = 2**0.5
        nt5 = t5 / sqrt2
        nt5_l = t5_l / sqrt2
        nt5_u = t5_u / sqrt2
        nelo5 = nelo_divided_by_nt * nt5
        nelo5_l = nelo_divided_by_nt * nt5_l
        nelo5_u = nelo_divided_by_nt * nt5_u
        results5_DD_prob = draw_ratio - (results5_[1] + results5_[3]) / (2 * n5)
        results5_WL_prob = results5_[2] / n5 - results5_DD_prob
        ratio = var5_per_game / var3
        var_diff = var3 - var5_per_game
        rms_bias = var_diff**0.5 if var_diff >= 0 else 0
        rms_bias_elo = stat_util.elo(0.5 + rms_bias)

        pent = {
            "results5": results5,
            "draw_ratio": pentanomial_draw_ratio,
            "games5": games5,
            "n5": n5,
            "pdf5": pdf5,
            "pdf5_s": pdf5_s,
            "avg5": avg5,
            "avg5_l": avg5_l,
            "avg5_u": avg5_u,
            "var5": var5,
            "skewness5": skewness5,
            "exkurt5": exkurt5,
            "var5_per_game": var5_per_game,
            "var5_per_game_l": var5_per_game_l,
            "var5_per_game_u": var5_per_game_u,
            "stdev5_per_game": stdev5_per_game,
            "stdev5_per_game_l": stdev5_per_game_l,
            "stdev5_per_game_u": stdev5_per_game_u,
            "nelo5": nelo5,
            "nelo5_l": nelo5_l,
            "nelo5_u": nelo5_u,
            "results5_DD_prob": results5_DD_prob,
            "results5_WL_prob": results5_WL_prob,
            "ratio": ratio,
            "var_diff": var_diff,
            "rms_bias": rms_bias,
            "rms_bias_elo": rms_bias_elo,
        }

    drawelo = stat_util.draw_elo_calc(results3_)

    sigma = stdev5_per_game if has_pentanomial else stdev3

    sprt = {}
    if has_sprt:
        sprt_args = args["sprt"]
        elo_model = sprt_args.get("elo_model", "BayesElo")
        alpha = sprt_args["alpha"]
        beta = sprt_args["beta"]
        elo0 = sprt_args["elo0"]
        elo1 = sprt_args["elo1"]
        batch_size_units = sprt_args.get("batch_size", 1)
        batch_size_games = 2 * batch_size_units if has_pentanomial else 1
        overshoot = sprt_args.get("overshoot", None)

        belo0 = None
        belo1 = None
        if elo_model == "BayesElo":
            belo0 = elo0
            belo1 = elo1
            elo0_ = stat_util.bayeselo_to_elo(belo0, drawelo)
            elo1_ = stat_util.bayeselo_to_elo(belo1, drawelo)
            elo_model_ = "logistic"
        else:
            elo0_ = elo0
            elo1_ = elo1
            elo_model_ = elo_model

        if elo_model_ == "logistic":
            lelo0 = elo0_
            lelo1 = elo1_
            lelo03 = lelo0
            lelo13 = lelo1
            score0 = stat_util.L(lelo0)
            score1 = stat_util.L(lelo1)
            score03 = score0
            score13 = score1
            nelo0 = nelo_divided_by_nt * (score0 - 0.5) / sigma
            nelo1 = nelo_divided_by_nt * (score1 - 0.5) / sigma
            nelo03 = nelo_divided_by_nt * (score03 - 0.5) / stdev3
            nelo13 = nelo_divided_by_nt * (score13 - 0.5) / stdev3
        else:
            nelo0 = elo0_
            nelo1 = elo1_
            nelo03 = nelo0
            nelo13 = nelo1
            score0 = nelo0 / nelo_divided_by_nt * sigma + 0.5
            score1 = nelo1 / nelo_divided_by_nt * sigma + 0.5
            score03 = score0
            score13 = score1
            lelo0 = stat_util.elo(score0)
            lelo1 = stat_util.elo(score1)
            lelo03 = lelo0
            lelo13 = lelo1

        if belo0 is None:
            belo0 = stat_util.elo_to_bayeselo(lelo03, draw_ratio)[0]
            belo1 = stat_util.elo_to_bayeselo(lelo13, draw_ratio)[0]

        llrjumps3 = list_to_string(
            [item[0] for item in LLRcalc.LLRjumps(pdf3, score0, score1)]
        )
        sp = sprt_module.sprt(alpha=alpha, beta=beta, elo0=lelo0, elo1=lelo1)
        sp.set_state(results3_)
        a3 = sp.analytics()
        llr3_l = a3["a"]
        llr3_u = a3["b"]
        if elo_model_ == "logistic":
            llr3 = LLRcalc.LLR_logistic(lelo03, lelo13, results3_)
        else:
            llr3 = LLRcalc.LLR_normalized(nelo03, nelo13, results3_)

        elo3_l = a3["ci"][0]
        elo3_u = a3["ci"][1]
        elo3 = a3["elo"]
        los3 = a3["LOS"]
        llr3_exact = n3 * LLRcalc.LLR(pdf3, score03, score13)
        llr3_alt = n3 * LLRcalc.LLR_alt(pdf3, score03, score13)
        llr3_alt2 = n3 * LLRcalc.LLR_alt2(pdf3, score03, score13)
        llr3_normalized = LLRcalc.LLR_normalized(nelo03, nelo13, results3_)
        llr3_normalized_alt = LLRcalc.LLR_normalized_alt(nelo03, nelo13, results3_)
        llr3_be = stat_util.LLRlegacy(belo0, belo1, results3_)

        sprt = {
            "elo_model": elo_model,
            "alpha": alpha,
            "beta": beta,
            "elo0": elo0,
            "elo1": elo1,
            "batch_size_games": batch_size_games,
            "lelo0": lelo0,
            "lelo1": lelo1,
            "nelo0": nelo0,
            "nelo1": nelo1,
            "belo0": belo0,
            "belo1": belo1,
            "score0": score0,
            "score1": score1,
            "llr3": llr3,
            "llr3_l": llr3_l,
            "llr3_u": llr3_u,
            "elo3": elo3,
            "elo3_l": elo3_l,
            "elo3_u": elo3_u,
            "los3": los3,
            "llr3_exact": llr3_exact,
            "llr3_alt": llr3_alt,
            "llr3_alt2": llr3_alt2,
            "llr3_normalized": llr3_normalized,
            "llr3_normalized_alt": llr3_normalized_alt,
            "llr3_be": llr3_be,
            "llrjumps3": llrjumps3,
        }

        if has_pentanomial and results5_ is not None and pdf5 is not None:
            llrjumps5 = list_to_string(
                [item[0] for item in LLRcalc.LLRjumps(pdf5, score0, score1)]
            )
            sp = sprt_module.sprt(alpha=alpha, beta=beta, elo0=lelo0, elo1=lelo1)
            sp.set_state(results5_)
            a5 = sp.analytics()
            llr5_l = a5["a"]
            llr5_u = a5["b"]
            if elo_model_ == "logistic":
                llr5 = LLRcalc.LLR_logistic(lelo0, lelo1, results5_)
            else:
                llr5 = LLRcalc.LLR_normalized(nelo0, nelo1, results5_)

            o0 = 0
            o1 = 0
            if overshoot is not None:
                o0 = (
                    -overshoot["sq0"] / overshoot["m0"] / 2
                    if overshoot["m0"] != 0
                    else 0
                )
                o1 = (
                    overshoot["sq1"] / overshoot["m1"] / 2
                    if overshoot["m1"] != 0
                    else 0
                )

            elo5_l = a5["ci"][0]
            elo5_u = a5["ci"][1]
            elo5 = a5["elo"]
            los5 = a5["LOS"]
            llr5_exact = n5 * LLRcalc.LLR(pdf5, score0, score1)
            llr5_alt = n5 * LLRcalc.LLR_alt(pdf5, score0, score1)
            llr5_alt2 = n5 * LLRcalc.LLR_alt2(pdf5, score0, score1)
            llr5_normalized = LLRcalc.LLR_normalized(nelo0, nelo1, results5_)
            llr5_normalized_alt = LLRcalc.LLR_normalized_alt(nelo0, nelo1, results5_)

            sprt.update(
                {
                    "llr5": llr5,
                    "llr5_l": llr5_l,
                    "llr5_u": llr5_u,
                    "elo5": elo5,
                    "elo5_l": elo5_l,
                    "elo5_u": elo5_u,
                    "los5": los5,
                    "llr5_exact": llr5_exact,
                    "llr5_alt": llr5_alt,
                    "llr5_alt2": llr5_alt2,
                    "llr5_normalized": llr5_normalized,
                    "llr5_normalized_alt": llr5_normalized_alt,
                    "llrjumps5": llrjumps5,
                    "overshoot0": o0,
                    "overshoot1": o1,
                }
            )
    else:
        elo3, elo95_3, los3 = stat_util.get_elo(results3_)
        sprt = {
            "elo3": elo3,
            "elo3_l": elo3 - elo95_3,
            "elo3_u": elo3 + elo95_3,
            "los3": los3,
        }
        if has_pentanomial and results5_ is not None:
            elo5, elo95_5, los5 = stat_util.get_elo(results5_)
            sprt.update(
                {
                    "elo5": elo5,
                    "elo5_l": elo5 - elo95_5,
                    "elo5_u": elo5 + elo95_5,
                    "los5": los5,
                }
            )

    context_rows = [
        ("Base TC", args.get("tc", "?")),
        ("Test TC", args.get("new_tc", args.get("tc", "?"))),
        ("Book", args.get("book", "?")),
        ("Threads", args.get("threads", "?")),
        ("Base options", args.get("base_options", "?")),
        ("New options", args.get("new_options", "?")),
    ]

    sprt_rows = []
    sprt_bounds_rows = []
    if has_sprt:
        sprt_rows = [
            ("Alpha", sprt["alpha"]),
            ("Beta", sprt["beta"]),
            (f"Elo0 ({sprt['elo_model']})", sprt["elo0"]),
            (f"Elo1 ({sprt['elo_model']})", sprt["elo1"]),
            ("Batch size (games)", sprt["batch_size_games"]),
        ]
        sprt_bounds_rows = [
            {
                "label": "H0",
                "logistic": f"{sprt['lelo0']:.3f}",
                "normalized": f"{sprt['nelo0']:.3f}",
                "bayes": f"{sprt['belo0']:.3f}",
                "score": f"{sprt['score0']:.5f}",
            },
            {
                "label": "H1",
                "logistic": f"{sprt['lelo1']:.3f}",
                "normalized": f"{sprt['nelo1']:.3f}",
                "bayes": f"{sprt['belo1']:.3f}",
                "score": f"{sprt['score1']:.5f}",
            },
        ]

    draw_rows = [("Draw ratio", f"{draw_ratio:.5f}")]
    if has_pentanomial:
        draw_rows.append(("Pentanomial draw ratio", f"{pent['draw_ratio']:.5f}"))
    draw_rows.append(("DrawElo (BayesElo)", f"{drawelo:.2f}"))

    pent_rows = {}
    if has_pentanomial:
        pent_rows = {
            "basic_rows": [
                (
                    "Elo",
                    f"{sprt['elo5']:.4f} [{sprt['elo5_l']:.4f}, {sprt['elo5_u']:.4f}]",
                ),
                ("LOS(1-p)", f"{sprt['los5']:.5f}"),
            ],
            "aux_rows": [
                ("Games", f"{int(pent['games5'])}"),
                ("Results [0-2]", str(pent["results5"])),
                ("Distribution", pent["pdf5_s"]),
                (
                    "(DD,WL) split",
                    f"({pent['results5_DD_prob']:.5f}, {pent['results5_WL_prob']:.5f})",
                ),
                ("Expected value", f"{pent['avg5']:.5f}"),
                ("Variance", f"{pent['var5']:.5f}"),
                ("Skewness", f"{pent['skewness5']:.5f}"),
                ("Excess kurtosis", f"{pent['exkurt5']:.5f}"),
            ],
        }
        if has_sprt:
            pent_rows["basic_rows"].append(
                (
                    "LLR",
                    f"{sprt['llr5']:.4f} [{sprt['llr5_l']:.4f}, {sprt['llr5_u']:.4f}]",
                )
            )
            pent_rows["llr_rows"] = [
                ("Logistic (exact)", f"{sprt['llr5_exact']:.5f}"),
                ("Logistic (alt)", f"{sprt['llr5_alt']:.5f}"),
                ("Logistic (alt2)", f"{sprt['llr5_alt2']:.5f}"),
                ("Normalized (exact)", f"{sprt['llr5_normalized']:.5f}"),
                ("Normalized (alt)", f"{sprt['llr5_normalized_alt']:.5f}"),
            ]
            pent_rows["aux_rows"].append(("Score", f"{pent['avg5']:.5f}"))
        else:
            pent_rows["aux_rows"].append(
                (
                    "Score",
                    f"{pent['avg5']:.5f} [{pent['avg5_l']:.5f}, {pent['avg5_u']:.5f}]",
                )
            )

        pent_rows["aux_rows"].extend(
            [
                (
                    "Variance/game",
                    f"{pent['var5_per_game']:.5f} [{pent['var5_per_game_l']:.5f}, {pent['var5_per_game_u']:.5f}]",
                ),
                (
                    "Stdev/game",
                    f"{pent['stdev5_per_game']:.5f} [{pent['stdev5_per_game_l']:.5f}, {pent['stdev5_per_game_u']:.5f}]",
                ),
            ]
        )

        if has_sprt:
            pent_rows["aux_rows"].append(("Normalized Elo", f"{pent['nelo5']:.2f}"))
            pent_rows["aux_rows"].append(("LLR jumps [0-2]", sprt["llrjumps5"]))
            pent_rows["aux_rows"].append(
                (
                    "Expected overshoot [H0,H1]",
                    f"[{sprt['overshoot0']:.5f}, {sprt['overshoot1']:.5f}]",
                )
            )
        else:
            pent_rows["aux_rows"].append(
                (
                    "Normalized Elo",
                    f"{pent['nelo5']:.2f} [{pent['nelo5_l']:.2f}, {pent['nelo5_u']:.2f}]",
                )
            )

        pent_rows["comparison_rows"] = [
            ("Variance ratio (pentanomial/trinomial)", f"{pent['ratio']:.5f}"),
            ("Variance difference (trinomial-pentanomial)", f"{pent['var_diff']:.5f}"),
            ("RMS bias", f"{pent['rms_bias']:.5f}"),
            ("RMS bias (Elo)", f"{pent['rms_bias_elo']:.3f}"),
        ]

    tri_rows = {
        "basic_rows": [
            (
                "Elo",
                f"{sprt['elo3']:.4f} [{sprt['elo3_l']:.4f}, {sprt['elo3_u']:.4f}]",
            ),
            ("LOS(1-p)", f"{sprt['los3']:.5f}"),
        ],
        "aux_rows": [
            ("Games", f"{int(games3)}"),
            ("Results [losses, draws, wins]", str(results3)),
            ("Distribution {loss ratio, draw ratio, win ratio}", pdf3_s),
            ("Expected value", f"{avg3:.5f}"),
            ("Variance", f"{var3:.5f}"),
            ("Skewness", f"{skewness3:.5f}"),
            ("Excess kurtosis", f"{exkurt3:.5f}"),
        ],
    }

    if has_sprt:
        tri_rows["basic_rows"].append(
            (
                "LLR",
                f"{sprt['llr3']:.4f} [{sprt['llr3_l']:.4f}, {sprt['llr3_u']:.4f}]",
            )
        )
        tri_rows["llr_rows"] = [
            ("Logistic (exact)", f"{sprt['llr3_exact']:.5f}"),
            ("Logistic (alt)", f"{sprt['llr3_alt']:.5f}"),
            ("Logistic (alt2)", f"{sprt['llr3_alt2']:.5f}"),
            ("Normalized (exact)", f"{sprt['llr3_normalized']:.5f}"),
            ("Normalized (alt)", f"{sprt['llr3_normalized_alt']:.5f}"),
            ("BayesElo", f"{sprt['llr3_be']:.5f}"),
        ]
        tri_rows["aux_rows"].append(("Score", f"{avg3:.5f}"))
        tri_rows["aux_rows"].append(("Normalized Elo", f"{nelo3:.2f}"))
        tri_rows["aux_rows"].append(("LLR jumps [loss, draw, win]", sprt["llrjumps3"]))
    else:
        tri_rows["aux_rows"].append(
            ("Score", f"{avg3:.5f} [{avg3_l:.5f}, {avg3_u:.5f}]")
        )
        tri_rows["aux_rows"].append(
            (
                "Normalized Elo",
                f"{nelo3:.2f} [{nelo3_l:.2f}, {nelo3_u:.2f}]",
            )
        )

    tri_rows["aux_rows"].extend(
        [
            ("Variance/game", f"{var3:.5f} [{var3_l:.5f}, {var3_u:.5f}]"),
            ("Stdev/game", f"{stdev3:.5f} [{stdev3_l:.5f}, {stdev3_u:.5f}]"),
        ]
    )

    return {
        "run_id": str(run.get("_id", "")),
        "has_sprt": has_sprt,
        "has_pentanomial": has_pentanomial,
        "has_spsa": has_spsa,
        "context_rows": context_rows,
        "sprt_rows": sprt_rows,
        "draw_rows": draw_rows,
        "sprt_bounds_rows": sprt_bounds_rows,
        "sprt_note": (
            "Note: normalized Elo is inversely proportional to the square root of the number "
            "of games it takes on average to detect a given strength difference with a given "
            "level of significance. It is given by logistic_elo/(2*standard_deviation_per_game). "
            "In other words if the draw ratio is zero and Elo differences are small then normalized "
            "Elo and logistic Elo coincide."
        ),
        "pentanomial": pent_rows,
        "trinomial": tri_rows,
        "tri_note": (
            "Note: The following quantities are computed using the incorrect trinomial model and so "
            "they should be taken with a grain of salt. The trinomial quantities are listed because "
            "they serve as a sanity check for the correct pentanomial quantities and moreover it is "
            "possible to extract some genuinely interesting information from the comparison between the two."
        ),
        "llr_note": (
            "Note: The quantities labeled alt and alt2 are various approximations for the exact quantities. "
            "Simulations indicate that the exact quantities perform better under extreme conditions."
        ),
        "bayes_note": (
            "Note: BayesElo is the LLR as computed using the BayesElo model. It is not clear how to "
            "generalize it to the pentanomial case."
        ),
    }


def results_pre_attrs(results_info: dict, run: dict) -> str:
    """Build pre tag attributes for results styling."""
    ret = ""
    style = results_info.get("style", "")
    if style:
        ret = f'style="background-color: {style};"'

    classes = "rounded elo-results results-pre"
    tc = run["args"]["tc"]
    new_tc = run["args"].get("new_tc", tc)
    if tc != new_tc:
        classes += " time-odds"
    ret += f' class="{classes}"'

    return ret


def diff_url_for_run(run: dict, allow_github_api_calls: bool) -> str:  # noqa: FBT001
    """Build a diff URL for a run with optional GitHub API calls."""
    return diff_url(run, master_check=allow_github_api_calls)


def tests_run_setup(
    args: dict,
    master_info: dict,
    pt_info: dict,
    test_book: str,
) -> dict:
    """Assemble template-ready test setup values."""
    base_branch = args.get("base_tag", "master")
    latest_bench = args.get("base_signature", master_info["bench"])

    pt_version = pt_info["pt_version"]
    pt_branch = pt_info["pt_branch"]
    pt_signature = pt_info["pt_bench"]

    tc = args.get("tc", "10+0.1")
    new_tc = args.get("new_tc", tc)

    default_book = args.get("book", test_book)

    is_odds = new_tc != tc

    arch_filter = args.get("arch_filter", "")
    compiler = args.get("compiler", "")

    return {
        "base_branch": base_branch,
        "latest_bench": latest_bench,
        "pt_version": pt_version,
        "pt_branch": pt_branch,
        "pt_signature": pt_signature,
        "tc": tc,
        "new_tc": new_tc,
        "default_book": default_book,
        "is_odds": is_odds,
        "arch_filter": arch_filter,
        "compiler": compiler,
    }


def build_tasks_rows(
    run: dict,
    *,
    show_task: int,
    chi2: float,
    is_approver: bool,
) -> tuple[list[dict], bool, bool]:
    """Build template-ready task rows for the tasks table."""
    show_pentanomial = "pentanomial" in run.get("results", {})
    show_residual = "spsa" not in run.get("args", {})
    tasks = []
    all_tasks = run.get("tasks", []) + run.get("bad_tasks", [])

    for idx, task in enumerate(all_tasks):
        if "bad" in task and idx < len(run.get("tasks", [])):
            continue
        if "stats" not in task:
            continue

        task_id = task.get("task_id", idx)
        stats = task.get("stats", {})
        total = stats.get("wins", 0) + stats.get("losses", 0) + stats.get("draws", 0)

        if task_id == show_task:
            row_class = "highlight"
        elif task.get("active"):
            row_class = "info"
        else:
            row_class = ""

        worker_info = task.get("worker_info")
        worker_label = "-"
        worker_url = ""
        if worker_info:
            worker_label = worker_name(worker_info)
            if is_approver and worker_info.get("username") != "Unknown_worker":
                worker_url = f"/workers/{worker_name(worker_info, short=True)}"

        info_label = "-"
        if worker_info:
            gcc_version = ".".join(str(m) for m in worker_info.get("gcc_version", []))
            compiler = worker_info.get("compiler", "g++")
            python_version = ".".join(
                str(m) for m in worker_info.get("python_version", [])
            )
            version = worker_info.get("version", "")
            arch = worker_info.get("ARCH", "")
            worker_arch = worker_info.get("worker_arch", "unknown")
            uname = worker_info.get("uname", "")
            max_memory = worker_info.get("max_memory", "")
            info_label = (
                f"os: {uname}; ram: {max_memory}MiB; compiler: {compiler} {gcc_version}; "
                f"python: {python_version}; worker: {version}; arch: {worker_arch}; "
                f"features: {arch}"
            )

        last_updated_label = str(task.get("last_updated", "-")).split(".")[0]
        played_label = f"{total:03d} / {task.get('num_games', 0):03d}"

        if show_pentanomial:
            p = stats.get("pentanomial", [0] * 5)
            results_cells = [f"[{p[0]}, {p[1]}, {p[2]}, {p[3]}, {p[4]}]"]
        else:
            results_cells = [
                stats.get("wins", "-"),
                stats.get("losses", "-"),
                stats.get("draws", "-"),
            ]

        crashes = stats.get("crashes", "-")
        time_losses = stats.get("time_losses", "-")
        residual_label = ""
        residual_bg = ""
        if show_residual:
            residual = display_residual(task, chi2)
            if residual["residual"] != float("inf"):
                residual_label = f"{residual['residual']:.3f}"
                residual_bg = residual["display_color"]
            else:
                residual_label = "-"

        tasks.append(
            {
                "task_id": task_id,
                "row_class": row_class,
                "pgn_url": f"/api/pgn/{run['_id']}-{task_id:d}.pgn",
                "worker_label": worker_label,
                "worker_url": worker_url,
                "info_label": info_label,
                "last_updated_label": last_updated_label,
                "played_label": played_label,
                "results_cells": results_cells,
                "crashes": crashes,
                "time_losses": time_losses,
                "residual_label": residual_label,
                "residual_bg": residual_bg,
            }
        )

    return tasks, show_pentanomial, show_residual


def build_run_table_rows(
    runs: list[dict],
    *,
    allow_github_api_calls: bool,
) -> list[dict]:
    """Build template-ready run rows for run tables."""
    rows = []
    for run in runs:
        args = run.get("args", {})
        run_id = str(run.get("_id", ""))
        start_time = run.get("start_time")
        start_date_label = (
            start_time.strftime("%y-%m-%d") if hasattr(start_time, "strftime") else ""
        )
        username = args.get("username", "")
        user_short = username[:3]
        user_url = f"/tests/user/{username}" if username else ""
        run_url = f"/tests/view/{run_id}" if run_id else ""
        new_tag = args.get("new_tag", "")
        new_tag_short = new_tag[:23]
        diff_link = diff_url_for_run(run, allow_github_api_calls)
        is_finished = bool(run.get("finished"))
        is_sprt = "sprt" in args
        live_label = "sprt" if is_sprt else str(args.get("num_games", ""))
        live_url = f"/tests/live_elo/{run_id}" if is_sprt else ""
        tc_label = args.get("tc", "")
        threads = args.get("threads", 1)
        cores = run.get("cores", "")
        workers = run.get("workers", "")
        cores_label = ""
        if not is_finished:
            cores_label = f"cores: {cores} ({workers})"
        info = args.get("info", "")
        info_html = info.replace("\n", "<br>") if info else ""

        rows.append(
            {
                "run": run,
                "run_id": run_id,
                "start_date_label": start_date_label,
                "user_short": user_short,
                "user_name": username,
                "user_url": user_url,
                "is_finished": is_finished,
                "is_sprt": is_sprt,
                "new_tag_short": new_tag_short,
                "run_url": run_url,
                "diff_url": diff_link,
                "live_label": live_label,
                "live_url": live_url,
                "tc_label": tc_label,
                "threads": threads,
                "cores_label": cores_label,
                "info_html": info_html,
            }
        )
    return rows


__all__ = [
    "build_contributors_rows",
    "build_contributors_summary",
    "build_tasks_rows",
    "build_run_table_rows",
    "clip_long",
    "diff_url",
    "diff_url_for_run",
    "display_residual",
    "format_bounds",
    "format_date",
    "format_group",
    "format_results",
    "format_time_ago",
    "get_cookie",
    "is_active_sprt_ltc",
    "is_elo_pentanomial_run",
    "list_to_string",
    "build_tests_stats_context",
    "nelo_pentanomial_summary",
    "pdf_to_string",
    "results_pre_attrs",
    "run_tables_prefix",
    "t_conf",
    "tests_repo",
    "tests_run_setup",
    "urlencode",
    "worker_name",
]
