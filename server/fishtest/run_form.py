"""Run-creation helpers shared by UI frontends (Pyramid-free).

Why this exists
--------------

Historically, the "create run" logic lived close to Pyramid view code.
During the Pyramid -> FastAPI switch, the goal was to keep the *domain and
validation* logic unchanged while replacing only the HTTP layer.

This module therefore contains the heavy lifting for creating a run from the
UI form:

- validate and normalize POSTed fields
- resolve branches to SHAs via GitHub
- extract NNUE nets from Stockfish sources
- compute SPRT/SPSA derived parameters
- provide helper utilities used after run creation (message formatting, net metadata)

Request contract
----------------

The functions here operate on a minimal, duck-typed "request" object.
FastAPI handlers supply a small adapter that provides the attributes used here:

- request.POST: mapping of form fields
- request.authenticated_userid: username
- request.session.flash(message, queue): flash messages
- request.userdb: UserDb-like object
- request.rundb: RunDb-like object
- request.host_url: base URL for user-facing errors
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime

import fishtest.github_api as gh
import fishtest.stats.stat_util
import regex
from fishtest.schemas import tc as tc_schema
from fishtest.util import (
    format_bounds,
    get_hash,
    get_tc_ratio,
    tests_repo,
)
from vtjson import validate


def get_master_info(
    user: str = "official-stockfish",
    repo: str = "Stockfish",
    ignore_rate_limit: bool = False,
):
    try:
        commits = gh.get_commits(
            user=user,
            repo=repo,
            ignore_rate_limit=ignore_rate_limit,
        )
    except Exception as e:
        print(f"Exception getting commits:\n{e}")
        return None

    bench_search = re.compile(r"(^|\s)[Bb]ench[ :]+([1-9]\d{5,7})(?!\d)")
    latest_bench_match = None

    message = commits[0]["commit"]["message"].strip().split("\n")[0].strip()
    date_str = commits[0]["commit"]["committer"]["date"]
    date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")

    for commit in commits:
        message_lines = commit["commit"]["message"].strip().split("\n")
        for line in reversed(message_lines):
            bench = bench_search.search(line.strip())
            if bench:
                latest_bench_match = {
                    "bench": bench.group(2),
                    "message": message,
                    "date": date.strftime("%b %d"),
                }
                break
        if latest_bench_match:
            break

    return latest_bench_match


def _get_sha(branch: str, repo_url: str):
    """Resolves the git branch to sha commit."""
    user, repo = gh.parse_repo(repo_url)
    try:
        commit = gh.get_commit(user=user, repo=repo, branch=branch)
    except Exception as e:
        raise Exception(f"Unable to access developer repository {repo_url}: {e!s}")
    if "sha" in commit:
        return commit["sha"], commit["commit"]["message"].split("\n")[0]
    return "", ""


def _get_nets(commit_sha: str, repo_url: str):
    """Get the nets from evaluate.h or ucioption.cpp in the repo."""
    try:
        nets: list[str] = []
        pattern = re.compile("nn-[a-f0-9]{12}.nnue")

        user, repo = gh.parse_repo(repo_url)
        options = gh.download_from_github(
            "/src/evaluate.h",
            user=user,
            repo=repo,
            branch=commit_sha,
            method="raw",
        ).decode()
        for line in options.splitlines():
            if "EvalFileDefaultName" in line and "define" in line:
                m = pattern.search(line)
                if m:
                    nets.append(m.group(0))

        if nets:
            return nets

        options = gh.download_from_github(
            "/src/ucioption.cpp",
            user=user,
            repo=repo,
            branch=commit_sha,
            method="raw",
        ).decode()
        for line in options.splitlines():
            if "EvalFile" in line and "Option" in line:
                m = pattern.search(line)
                if m:
                    nets.append(m.group(0))
        return nets
    except Exception as e:
        raise Exception(f"Unable to access developer repository {repo_url}: {e!s}")


def _parse_spsa_params(spsa: dict):
    raw = spsa["raw_params"]
    params = []
    for line in raw.split("\n"):
        chunks = line.strip().split(",")
        if len(chunks) == 1 and chunks[0] == "":  # blank line
            continue
        if len(chunks) != 6:
            raise Exception(f"the line {chunks} does not have 6 entries")
        param = {
            "name": chunks[0],
            "start": float(chunks[1]),
            "min": float(chunks[2]),
            "max": float(chunks[3]),
            "c_end": float(chunks[4]),
            "r_end": float(chunks[5]),
        }
        param["c"] = param["c_end"] * spsa["num_iter"] ** spsa["gamma"]
        param["a_end"] = param["r_end"] * param["c_end"] ** 2
        param["a"] = param["a_end"] * (spsa["A"] + spsa["num_iter"]) ** spsa["alpha"]
        param["theta"] = param["start"]
        params.append(param)
    return params


def _sanitize_options(options: str) -> str:
    try:
        options.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("Options must contain only ASCII characters")

    tokens = options.split()
    token_regex = re.compile(r"^[^\s=]+=[^\s=]+$", flags=re.ASCII)
    for token in tokens:
        if not token_regex.fullmatch(token):
            raise ValueError(
                "Each option must be a 'key=value' pair with no extra spaces and exactly one '='",
            )
    return " ".join(tokens)


def validate_form(request):
    data = {
        "base_tag": request.POST["base-branch"],
        "new_tag": request.POST["test-branch"],
        "tc": request.POST["tc"],
        "new_tc": request.POST["new_tc"],
        "book": request.POST["book"],
        "book_depth": request.POST["book-depth"],
        "base_signature": request.POST["base-signature"],
        "new_signature": request.POST["test-signature"],
        "base_options": _sanitize_options(request.POST["base-options"]),
        "new_options": _sanitize_options(request.POST["new-options"]),
        "username": request.authenticated_userid,
        "tests_repo": request.POST["tests-repo"],
        "info": request.POST["run-info"],
        "arch_filter": request.POST["arch-filter"],
    }
    try:
        data["tests_repo"] = gh.normalize_repo(data["tests_repo"])
    except Exception as e:
        raise Exception(
            f"Unable to access developer repository {data['tests_repo']}: {e!s}",
        ) from e

    user, repo = gh.parse_repo(data["tests_repo"])
    username = request.authenticated_userid
    u = request.userdb.get_user(username)

    official_repo = "https://github.com/official-stockfish/Stockfish"
    master_repo = official_repo
    try:
        master_repo = gh.get_master_repo(user, repo, ignore_rate_limit=True)
    except Exception as e:
        print(
            f"Unable to determine master repo for {data['tests_repo']}: {e!s}",
            flush=True,
        )
    else:
        if master_repo != official_repo:
            data["master_repo"] = master_repo
            message = (
                f"It seems that your repo {data['tests_repo']} has been forked from "
                f"{master_repo} and not from {official_repo} "
                "as recommended in the wiki. As such, some functionality may be broken. "
            )
            suffix_soft = (
                "Please consider replacing your repo with one forked from the official "
                "Stockfish repo!"
            )
            suffix_hard = (
                "Please replace your repo with one forked from the official "
                "Stockfish repo!"
            )
            if u["registration_time"] >= datetime(2025, 7, 1, tzinfo=UTC):
                raise Exception(message + " " + suffix_hard)
            request.session.flash(
                message + " " + suffix_soft,
                "warning",
            )

    odds = request.POST.get("odds", "off")
    if odds == "off":
        data["new_tc"] = data["tc"]

    checkbox_arch_filter = request.POST.get("checkbox-arch-filter", "off")
    if checkbox_arch_filter == "off":
        data["arch_filter"] = ""

    try:
        regex.compile(data["arch_filter"])
    except regex.error as e:
        raise Exception(f"Invalid arch filter: {e}") from e

    validate(tc_schema, data["tc"], "data['tc']")
    validate(tc_schema, data["new_tc"], "data['new_tc']")

    if request.POST.get("rescheduled_from"):
        data["rescheduled_from"] = request.POST["rescheduled_from"]

    def strip_message(m: str) -> str:
        lines = m.strip().split("\n")
        bench_search = re.compile(r"(^|\s)[Bb]ench[ :]+([1-9]\d{5,7})(?!\d)")
        for i, line in enumerate(reversed(lines)):
            new_line, n = bench_search.subn("", line)
            if n:
                lines[-i - 1] = new_line
                break
        s = "\n".join(lines)
        s = re.sub(r"[ \t]+", " ", s)
        s = re.sub(r"\n+", r"\n", s)
        return s.rstrip()

    if len(data["new_signature"]) == 0 or len(data["info"]) == 0:
        try:
            c = gh.get_commit(
                user=user,
                repo=repo,
                branch=data["new_tag"],
                ignore_rate_limit=True,
            )
        except Exception as e:
            raise Exception(
                f"Unable to access developer repository {data['tests_repo']}: {e!s}",
            ) from e
        if "commit" not in c:
            raise Exception(
                f"Cannot find branch {data['new_tag']} in developer repository",
            )
        if len(data["new_signature"]) == 0:
            bench_search = re.compile(r"(^|\s)[Bb]ench[ :]+([1-9]\d{5,7})(?!\d)")
            lines = c["commit"]["message"].split("\n")
            for line in reversed(lines):
                m = bench_search.search(line)
                if m:
                    data["new_signature"] = m.group(2)
                    break
            else:
                raise Exception(
                    "This commit has no signature: please supply it manually.",
                )
        if len(data["info"]) == 0:
            data["info"] = strip_message(c["commit"]["message"])

    if request.POST["stop_rule"] == "spsa":
        data["base_signature"] = data["new_signature"]

    for k, v in data.items():
        if len(v) == 0 and k != "arch_filter":
            raise Exception(f"Missing required option: {k}")

    data["auto_purge"] = request.POST.get("auto-purge") is not None
    data["adjudication"] = request.POST.get("adjudication") is None

    if "resolved_base" in request.POST:
        data["resolved_base"] = request.POST["resolved_base"]
        data["resolved_new"] = request.POST["resolved_new"]
        data["msg_base"] = request.POST["msg_base"]
        data["msg_new"] = request.POST["msg_new"]
    else:
        data["resolved_base"], data["msg_base"] = _get_sha(
            data["base_tag"],
            data["tests_repo"],
        )
        data["resolved_new"], data["msg_new"] = _get_sha(
            data["new_tag"],
            data["tests_repo"],
        )
        u = request.userdb.get_user(data["username"])
        if u.get("tests_repo", "") != data["tests_repo"]:
            u["tests_repo"] = data["tests_repo"]
            request.userdb.save_user(u)

    if len(data["resolved_base"]) == 0 or len(data["resolved_new"]) == 0:
        raise Exception("Unable to find branch!")

    if data["base_tag"] == "master":
        master_info = get_master_info(user=user, repo=repo, ignore_rate_limit=True)
        if master_info is None or master_info["bench"] != data["base_signature"]:
            raise Exception(
                "Bench signature of Base master does not match, "
                'please "git pull upstream master" !',
            )

    stop_rule = request.POST["stop_rule"]

    data["base_nets"] = _get_nets(data["resolved_base"], data["tests_repo"])
    data["new_nets"] = _get_nets(data["resolved_new"], data["tests_repo"])

    missing_nets = []
    for net_name in set(data["base_nets"]) | set(data["new_nets"]):
        net = request.rundb.get_nn(net_name)
        if net is None:
            missing_nets.append(net_name)
    if missing_nets:
        raise Exception(
            "Missing net(s). Please upload to: {} the following net(s): {}".format(
                request.host_url,
                ", ".join(missing_nets),
            ),
        )

    data["threads"] = int(request.POST["threads"])
    data["priority"] = int(request.POST["priority"])
    data["throughput"] = int(request.POST["throughput"])

    if data["threads"] <= 0:
        raise Exception("Threads must be >= 1")

    if stop_rule == "sprt":
        sprt_batch_size_games = 2 * max(
            1,
            int(0.5 + 16 / get_tc_ratio(data["tc"], data["threads"])),
        )
        sprt_batch_size_games = 8
        assert sprt_batch_size_games % 2 == 0
        elo_model = request.POST["elo_model"]
        if elo_model not in ["BayesElo", "logistic", "normalized"]:
            raise Exception("Unknown Elo model")
        data["sprt"] = fishtest.stats.stat_util.SPRT(
            alpha=0.05,
            beta=0.05,
            elo0=float(request.POST["sprt_elo0"]),
            elo1=float(request.POST["sprt_elo1"]),
            elo_model=elo_model,
            batch_size=sprt_batch_size_games // 2,
        )
        data["num_games"] = 800000
    elif stop_rule == "spsa":
        data["num_games"] = int(request.POST["num-games"])
        if data["num_games"] <= 0:
            raise Exception("Number of games must be >= 0")

        data["spsa"] = {
            "A": int(float(request.POST["spsa_A"]) * data["num_games"] / 2),
            "alpha": float(request.POST["spsa_alpha"]),
            "gamma": float(request.POST["spsa_gamma"]),
            "raw_params": request.POST["spsa_raw_params"],
            "iter": 0,
            "num_iter": int(data["num_games"] / 2),
        }
        data["spsa"]["params"] = _parse_spsa_params(data["spsa"])
        if len(data["spsa"]["params"]) == 0:
            raise Exception("Number of params must be > 0")
    else:
        data["num_games"] = int(request.POST["num-games"])
        if data["num_games"] <= 0:
            raise Exception("Number of games must be >= 0")

    max_games = 3200000
    if data["num_games"] > max_games:
        raise Exception("Number of games must be <= " + str(max_games))

    return data


def update_nets(request, run: dict):
    run_id = str(run["_id"])
    data = run["args"]
    base_nets, new_nets, missing_nets = [], [], []
    for net_name in set(data["base_nets"]) | set(data["new_nets"]):
        net = request.rundb.get_nn(net_name)
        if net is None:
            missing_nets.append(net_name)
        else:
            if net_name in data["base_nets"]:
                base_nets.append(net)
            if net_name in data["new_nets"]:
                new_nets.append(net)
    if missing_nets:
        raise Exception(
            "Missing net(s). Please upload to {} the following net(s): {}".format(
                request.host_url,
                ", ".join(missing_nets),
            ),
        )

    tests_repo_ = tests_repo(run)
    user, repo = gh.parse_repo(tests_repo_)
    try:
        if gh.is_master(run["args"]["resolved_base"]):
            for net in base_nets:
                if "is_master" not in net:
                    net["is_master"] = True
                    request.rundb.update_nn(net)
    except Exception as e:
        print(f"Unable to evaluate is_master({run['args']['resolved_base']}): {e!s}")

    for net in new_nets:
        if "first_test" not in net:
            net["first_test"] = {"id": run_id, "date": datetime.now(UTC)}
        net["last_test"] = {"id": run_id, "date": datetime.now(UTC)}
        request.rundb.update_nn(net)


def new_run_message(request, run: dict) -> str:
    if "sprt" in run["args"]:
        sprt = run["args"]["sprt"]
        elo_model = sprt.get("elo_model")
        ret = f"SPRT{format_bounds(elo_model, sprt['elo0'], sprt['elo1'])}"
    elif "spsa" in run["args"]:
        ret = f"SPSA[{run['args']['num_games']}]"
    else:
        ret = f"NumGames[{run['args']['num_games']}]"
        if run["args"]["resolved_base"] == request.rundb.pt_info["pt_branch"]:
            ret += f"(PT:{request.rundb.pt_info['pt_version']})"
    ret += f" TC:{run['args']['tc']}"
    ret += (
        f"[{run['args']['new_tc']}]"
        if run["args"]["new_tc"] != run["args"]["tc"]
        else ""
    )
    ret += "(LTC)" if run["tc_base"] >= request.rundb.ltc_lower_bound else ""
    ret += f" Book:{run['args']['book']}"
    ret += f" Threads:{run['args']['threads']}"
    ret += "(SMP)" if run["args"]["threads"] > 1 else ""
    ret += f" Hash:{get_hash(run['args']['base_options'])}/{get_hash(run['args']['new_options'])}"
    return ret


def del_tasks(run: dict):
    run = copy.copy(run)
    run.pop("tasks", None)
    return copy.deepcopy(run)
