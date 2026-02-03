"""Shared template helpers for Mako and Jinja2 renderers."""

from __future__ import annotations

import binascii
from urllib.parse import quote_plus

from fishtest.stats import LLRcalc, stat_util
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


def nelo_pentanomial_summary(run: dict) -> str | None:
    """Build a summary line for Elo pentanomial results."""
    if not is_elo_pentanomial_run(run):
        return None

    results5 = run["results"]["pentanomial"]
    nelo_coeff = LLRcalc.nelo_divided_by_nt / (2**0.5)
    z975 = stat_util.Phi_inv(0.975)

    n5, pdf5 = LLRcalc.results_to_pdf(results5)
    avg5, var5, skewness5, exkurt5 = LLRcalc.stats_ex(pdf5)
    t5, var_t5 = t_conf(avg5, var5, skewness5, exkurt5)
    nelo5 = nelo_coeff * t5
    nelo5_delta = nelo_coeff * z975 * (var_t5 / n5) ** 0.5

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


def diff_url_for_run(run: dict, *, allow_github_api_calls: bool) -> str:
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


__all__ = [
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
