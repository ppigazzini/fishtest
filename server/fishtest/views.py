import copy
import gzip
import hashlib
import html
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import bson
import fishtest.github_api as gh
import fishtest.stats.stat_util
import requests
from fishtest.run_cache import Prio
from fishtest.schemas import (
    RUN_VERSION,
    github_repo,
    is_undecided,
    runs_schema,
    short_worker_name,
)
from fishtest.schemas import tc as tc_schema
from fishtest.util import (
    email_valid,
    format_bounds,
    format_date,
    get_chi2,
    get_hash,
    get_tc_ratio,
    is_sprt_ltc_data,
    password_strength,
    plural,
    reasonable_run_hashes,
    tests_repo,
)
from pyramid.httpexceptions import HTTPFound, HTTPNotFound
from pyramid.security import forget, remember
from pyramid.view import forbidden_view_config, notfound_view_config, view_config
from vtjson import ValidationError, union, validate

HTTP_TIMEOUT = 15.0


def pagination(page_idx, num, page_size, query_params):
    pages = [
        {
            "idx": "Prev",
            "url": "?page={}".format(page_idx),
            "state": "disabled" if page_idx == 0 else "",
        }
    ]
    last_idx = (num - 1) // page_size
    for idx, _ in enumerate(range(0, num, page_size)):
        if (
            idx < 1
            or (idx < 5 and page_idx < 4)
            or abs(idx - page_idx) < 2
            or (idx > last_idx - 5 and page_idx > last_idx - 4)
        ):
            pages.append(
                {
                    "idx": idx + 1,
                    "url": "?page={}".format(idx + 1) + query_params,
                    "state": "active" if page_idx == idx else "",
                }
            )
        elif pages[-1]["idx"] != "...":
            pages.append({"idx": "...", "url": "", "state": "disabled"})
    pages.append(
        {
            "idx": "Next",
            "url": "?page={}".format(page_idx + 2) + query_params,
            "state": "disabled" if page_idx >= (num - 1) // page_size else "",
        }
    )
    return pages


@notfound_view_config(renderer="notfound.mak")
def notfound_view(request):
    request.response.status = 404
    if request.exception:
        try:
            json_body = str(request.exception).replace("'", '"')
            # Check if json_body is a valid JSON
            parsed_json = json.loads(json_body)
            request.response.content_type = "application/json"
            request.response.body = json.dumps(parsed_json).encode("utf-8")
            return request.response
        except json.JSONDecodeError:
            pass
    return {}


@view_config(route_name="home")
def home(request):
    return HTTPFound(location=request.route_url("tests"))


def ensure_logged_in(request):
    userid = request.authenticated_userid
    if not userid:
        request.session.flash("Please login")
        raise HTTPFound(
            location=request.route_url("login", _query={"next": request.path_qs})
        )
    return userid


@view_config(
    route_name="login",
    renderer="login.mak",
    require_csrf=True,
    request_method=("GET", "POST"),
)
@forbidden_view_config(renderer="login.mak")
def login(request):
    userid = request.authenticated_userid
    if userid:
        return home(request)
    login_url = request.route_url("login")
    referrer = request.url
    if referrer == login_url:
        referrer = "/"  # never use the login form itself as came_from
    came_from = request.params.get("came_from", referrer)

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        token = request.userdb.authenticate(username, password)
        if "error" not in token:
            if request.POST.get("stay_logged_in"):
                # Session persists for a year after login
                headers = remember(request, username, max_age=60 * 60 * 24 * 365)
            else:
                # Session ends when the browser is closed
                headers = remember(request, username)
            next_page = request.params.get("next") or came_from
            return HTTPFound(location=next_page, headers=headers)
        message = token["error"]
        if "Account pending for user:" in message:
            message += (
                " . If you recently registered to fishtest, "
                "a person will now manually approve your new account, to avoid spam. "
                "This is usually quick, but sometimes takes a few hours. "
                "Thank you!"
            )
        request.session.flash(message, "error")
    return {}


# Note that the allowed length of mailto URLs on Chrome/Windows is severely
# limited.


def worker_email(worker_name, blocker_name, message, host_url, blocked):
    owner_name = worker_name.split("-")[0]
    body = f"""\
Dear {owner_name},

Thank you for contributing to the development of Stockfish. Unfortunately, it seems your Fishtest worker {worker_name} has some issue(s). More specifically the following has been reported:

{message}

You may possibly find more information about this in our event log at {host_url}/actions

Feel free to reply to this email if you require any help, or else contact the #fishtest-dev channel on the Stockfish Discord server: https://discord.com/invite/awnh2qZfTT

Enjoy your day,

{blocker_name} (Fishtest approver)

"""
    return body


def normalize_lf(m):
    m = m.replace("\r\n", "\n").replace("\r", "\n")
    return m.rstrip()


@view_config(route_name="workers", renderer="workers.mak", require_csrf=True)
def workers(request):
    is_approver = request.has_permission("approve_run")

    blocked_workers = request.rundb.workerdb.get_blocked_workers()
    blocker_name = request.authenticated_userid

    # If we are approver then we are logged in, so blocker_name is not None
    if is_approver:
        for w in blocked_workers:
            owner_name = w["worker_name"].split("-")[0]
            owner = request.userdb.get_user(owner_name)
            w["owner_email"] = owner["email"] if owner is not None else ""
            w["body"] = worker_email(
                w["worker_name"],
                blocker_name,
                w["message"],
                request.host_url,
                w["blocked"],
            )
            w["subject"] = f"Issue(s) with worker {w['worker_name']}"

    worker_name = request.matchdict.get("worker_name")
    try:
        validate(union(short_worker_name, "show"), worker_name, name="worker_name")
    except ValidationError as e:
        request.session.flash(str(e), "error")
        return {
            "show_admin": False,
            "show_email": is_approver,
            "blocked_workers": blocked_workers,
        }
    if len(worker_name.split("-")) != 3:
        return {
            "show_admin": False,
            "show_email": is_approver,
            "blocked_workers": blocked_workers,
        }
    ensure_logged_in(request)
    owner_name = worker_name.split("-")[0]
    if not is_approver and blocker_name != owner_name:
        request.session.flash("Only owners and approvers can block/unblock", "error")
        return {
            "show_admin": False,
            "show_email": is_approver,
            "blocked_workers": blocked_workers,
        }

    if request.method == "POST":
        button = request.POST.get("submit")
        if button == "Submit":
            blocked = request.POST.get("blocked") is not None
            message = request.POST.get("message")
            max_chars = 500
            if len(message) > max_chars:
                request.session.flash(
                    f"Warning: your description of the issue has been truncated to {max_chars} characters",
                    "error",
                )
                message = message[:max_chars]
            message = normalize_lf(message)
            was_blocked = request.workerdb.get_worker(worker_name)["blocked"]
            request.rundb.workerdb.update_worker(
                worker_name, blocked=blocked, message=message
            )
            if blocked != was_blocked:
                request.session.flash(
                    f"Worker {worker_name} {'blocked' if blocked else 'unblocked'}!",
                )
                request.actiondb.block_worker(
                    username=blocker_name,
                    worker=worker_name,
                    message="blocked" if blocked else "unblocked",
                )
        return HTTPFound(location=request.route_url("workers", worker_name="show"))

    w = request.rundb.workerdb.get_worker(worker_name)
    return {
        "show_admin": True,
        "worker_name": worker_name,
        "blocked": w["blocked"],
        "message": w["message"],
        "show_email": is_approver,
        "last_updated": w["last_updated"],
        "blocked_workers": blocked_workers,
    }


@view_config(route_name="nn_upload", renderer="nn_upload.mak", require_csrf=True)
def upload(request):
    ensure_logged_in(request)

    if request.method != "POST":
        return {}
    try:
        filename = request.POST["network"].filename
        input_file = request.POST["network"].file
        network = input_file.read()
    except AttributeError:
        request.session.flash(
            "Specify a network file with the 'Choose File' button", "error"
        )
        return {}
    except Exception as e:
        print("Error reading the network file:", e)
        request.session.flash("Error reading the network file", "error")
        return {}
    if request.rundb.get_nn(filename):
        request.session.flash(f"Network {filename} already exists", "error")
        return {}
    errors = []
    if len(network) >= 120000000:
        errors.append("Network must be < 120MB")
    if not re.match(r"^nn-[0-9a-f]{12}\.nnue$", filename):
        errors.append('Name must match "nn-[SHA256 first 12 digits].nnue"')
    hash = hashlib.sha256(network).hexdigest()
    if hash[:12] != filename[3:15]:
        errors.append(f"Wrong SHA256 hash: {hash[:12]} Filename: {filename[3:15]}")
    if errors:
        for error in errors:
            request.session.flash(error, "error")
        return {}
    net_file_gz = Path("/var/www/fishtest/nn") / f"{filename}.gz"
    try:
        with gzip.open(net_file_gz, "xb") as f:
            f.write(network)
    except FileExistsError as e:
        print(f"Network {filename} already uploaded:", e)
        request.session.flash(f"Network {filename} already uploaded", "error")
        return {}
    except Exception as e:
        net_file_gz.unlink(missing_ok=True)
        print(f"Failed to write network {filename}:", e)
        request.session.flash(f"Failed to write network {filename}", "error")
        return {}
    try:
        net_data = gzip.decompress(net_file_gz.read_bytes())
    except Exception as e:
        net_file_gz.unlink()
        print(f"Failed to read uploaded network {filename}:", e)
        request.session.flash(f"Failed to read uploaded network {filename}", "error")
        return {}

    hash = hashlib.sha256(net_data).hexdigest()
    if hash[:12] != filename[3:15]:
        net_file_gz.unlink()
        request.session.flash(f"Invalid hash for uploaded network {filename}", "error")
        return {}

    if request.rundb.get_nn(filename):
        request.session.flash(f"Network {filename} already exists", "error")
        return {}

    request.rundb.upload_nn(request.authenticated_userid, filename)

    request.actiondb.upload_nn(
        username=request.authenticated_userid,
        nn=filename,
    )

    return HTTPFound(location=request.route_url("nns"))


@view_config(route_name="logout", require_csrf=True, request_method="POST")
def logout(request):
    session = request.session
    headers = forget(request)
    session.invalidate()
    return HTTPFound(location=request.route_url("tests"), headers=headers)


@view_config(
    route_name="signup",
    renderer="signup.mak",
    require_csrf=True,
    request_method=("GET", "POST"),
)
def signup(request):
    if request.authenticated_userid:
        return home(request)
    if request.method != "POST":
        return {}
    errors = []

    signup_username = request.POST.get("username", "").strip()
    signup_password = request.POST.get("password", "").strip()
    signup_password_verify = request.POST.get("password2", "").strip()
    signup_email = request.POST.get("email", "").strip()
    tests_repo = request.POST.get("tests_repo", "").strip()

    strong_password, password_err = password_strength(
        signup_password, signup_username, signup_email
    )
    if not strong_password:
        errors.append("Error! Weak password: " + password_err)
    if signup_password != signup_password_verify:
        errors.append("Error! Matching verify password required")
    email_is_valid, validated_email = email_valid(signup_email)
    if not email_is_valid:
        errors.append("Error! Invalid email: " + validated_email)
    if len(signup_username) == 0:
        errors.append("Error! Username required")
    if not signup_username.isalnum():
        errors.append("Error! Alphanumeric username required")

    try:
        validate(union(github_repo, ""), tests_repo, "tests_repo")
    except ValidationError as e:
        errors.append(f"Error! Invalid tests repo {tests_repo}: {str(e)}")

    if errors:
        for error in errors:
            request.session.flash(error, "error")
        return {}

    secret = os.environ.get("FISHTEST_CAPTCHA_SECRET")
    if not secret:
        print("FISHTEST_CAPTCHA_SECRET is missing.", flush=True)
    else:
        payload = {
            "secret": secret,
            "response": request.POST.get("g-recaptcha-response", ""),
            "remoteip": request.remote_addr,
        }
        response = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data=payload,
            timeout=HTTP_TIMEOUT,
        ).json()
        if "success" not in response or not response["success"]:
            if "error-codes" in response:
                print(response["error-codes"])
            request.session.flash("Captcha failed", "error")
            return {}

    result = request.userdb.create_user(
        username=signup_username,
        password=signup_password,
        email=validated_email,
        tests_repo=tests_repo,
    )
    if result is None:
        request.session.flash("Error! Invalid username or password", "error")
    elif not result:
        request.session.flash("Username or email is already registered", "error")
    else:
        request.session.flash(
            "Account created! "
            "To avoid spam, a person will now manually approve your new account. "
            "This is usually quick but sometimes takes a few hours. "
            "Thank you for contributing!"
        )
        return HTTPFound(location=request.route_url("login"))
    return {}


@view_config(route_name="nns", renderer="nns.mak")
def nns(request):
    user = request.params.get("user", "")
    network_name = request.params.get("network_name", "")
    master_only = request.params.get("master_only", False)

    page_param = request.params.get("page", "")
    page_idx = max(0, int(page_param) - 1) if page_param.isdigit() else 0
    page_size = 25

    nns, num_nns = request.rundb.get_nns(
        user=user,
        network_name=network_name,
        master_only=master_only,
        limit=page_size,
        skip=page_idx * page_size,
    )

    query_params = ""
    if user:
        query_params += "&user={}".format(user)
    if network_name:
        query_params += "&network_name={}".format(network_name)
    if master_only:
        query_params += "&master_only={}".format(master_only)

    pages = pagination(page_idx, num_nns, page_size, query_params)

    return {
        "nns": nns,
        "pages": pages,
        "master_only": request.cookies.get("master_only") == "true",
    }


@view_config(route_name="sprt_calc", renderer="sprt_calc.mak")
def sprt_calc(request):
    return {}


@view_config(route_name="rate_limits", renderer="rate_limits.mak")
def rate_limits(request):
    return {}


# Different LOCALES may have different quotation marks.
# See https://op.europa.eu/en/web/eu-vocabularies/formex/physical-specifications/character-encoding/quotation-marks

quotation_marks = (
    0x0022,
    0x0027,
    0x00AB,
    0x00BB,
    0x2018,
    0x2019,
    0x201A,
    0x201B,
    0x201C,
    0x201D,
    0x201E,
    0x201F,
    0x2039,
    0x203A,
)

quotation_marks = "".join(chr(c) for c in quotation_marks)
quotation_marks_translation = str.maketrans(quotation_marks, len(quotation_marks) * '"')


def sanitize_quotation_marks(text):
    return text.translate(quotation_marks_translation)


@view_config(route_name="actions", renderer="actions.mak")
def actions(request):
    search_action = request.params.get("action", "")
    username = request.params.get("user", "")
    text = sanitize_quotation_marks(request.params.get("text", ""))
    before = request.params.get("before", None)
    max_actions = request.params.get("max_actions", None)
    run_id = request.params.get("run_id", "")

    if before:
        before = float(before)
    if max_actions:
        max_actions = int(max_actions)

    page_param = request.params.get("page", "")
    page_idx = max(0, int(page_param) - 1) if page_param.isdigit() else 0
    page_size = 25

    actions, num_actions = request.actiondb.get_actions(
        username=username,
        action=search_action,
        text=text,
        skip=page_idx * page_size,
        limit=page_size,
        utc_before=before,
        max_actions=max_actions,
        run_id=run_id,
    )

    query_params = ""
    if username:
        query_params += "&user={}".format(username)
    if search_action:
        query_params += "&action={}".format(search_action)
    if text:
        query_params += "&text={}".format(text)
    if max_actions:
        query_params += "&max_actions={}".format(num_actions)
    if before:
        query_params += "&before={}".format(before)
    if run_id:
        query_params += "&run_id={}".format(run_id)

    pages = pagination(page_idx, num_actions, page_size, query_params)

    return {
        "actions": actions,
        "approver": request.has_permission("approve_run"),
        "pages": pages,
        "action_param": search_action,
        "username_param": username,
        "text_param": text,
        "run_id_param": run_id,
    }


def get_idle_users(users, request):
    idle = {}
    for u in users:
        idle[u["username"]] = u
    for u in request.userdb.user_cache.find():
        del idle[u["username"]]
    idle = list(idle.values())
    return idle


@view_config(route_name="user_management", renderer="user_management.mak")
def user_management(request):
    if not request.has_permission("approve_run"):
        request.session.flash("You cannot view user management", "error")
        return home(request)

    users = list(request.userdb.get_users())

    return {
        "all_users": users,
        "pending_users": request.userdb.get_pending(),
        "blocked_users": request.userdb.get_blocked(),
        "approvers_users": [
            user for user in users if "group:approvers" in user.get("groups", [])
        ],
        "idle_users": get_idle_users(users, request),  # depends on cache too
    }


@view_config(route_name="user", renderer="user.mak")
@view_config(route_name="profile", renderer="user.mak")
def user(request):
    userid = ensure_logged_in(request)

    user_name = request.matchdict.get("username", userid)
    profile = user_name == userid
    if not profile and not request.has_permission("approve_run"):
        request.session.flash("You cannot inspect users", "error")
        return home(request)

    user_data = request.userdb.get_user(user_name)
    if user_data is None:
        raise HTTPNotFound()
    if "user" in request.POST:
        if profile:
            old_password = request.params.get("old_password").strip()
            new_password = request.params.get("password").strip()
            new_password_verify = request.params.get("password2", "").strip()
            new_email = request.params.get("email").strip()
            tests_repo = request.params.get("tests_repo").strip()

            # Temporary comparison until passwords are hashed.
            if old_password != user_data["password"].strip():
                request.session.flash("Invalid password!", "error")
                return home(request)

            if len(new_password) > 0:
                if new_password == new_password_verify:
                    strong_password, password_err = password_strength(
                        new_password,
                        user_name,
                        user_data["email"],
                        (new_email if len(new_email) > 0 else None),
                    )
                    if strong_password:
                        user_data["password"] = new_password
                        request.session.flash("Success! Password updated")
                    else:
                        request.session.flash(
                            "Error! Weak password: " + password_err, "error"
                        )
                        return home(request)
                else:
                    request.session.flash(
                        "Error! Matching verify password required", "error"
                    )
                    return home(request)

            try:
                validate(union(github_repo, ""), tests_repo, "tests_repo")
            except ValidationError as e:
                request.session.flash(
                    f"Error! Invalid test repo {tests_repo}: {str(e)}", "error"
                )
                return home(request)

            user_data["tests_repo"] = tests_repo

            if len(new_email) > 0 and user_data["email"] != new_email:
                email_is_valid, validated_email = email_valid(new_email)
                if not email_is_valid:
                    request.session.flash(
                        "Error! Invalid email: " + validated_email, "error"
                    )
                    return home(request)
                else:
                    user_data["email"] = validated_email
                    request.session.flash("Success! Email updated")
            request.userdb.save_user(user_data)
        elif "blocked" in request.POST and request.POST["blocked"].isdigit():
            user_data["blocked"] = bool(int(request.POST["blocked"]))
            request.session.flash(
                ("Blocked" if user_data["blocked"] else "Unblocked")
                + " user "
                + user_name
            )
            request.userdb.last_blocked_time = 0
            request.userdb.save_user(user_data)
            request.actiondb.block_user(
                username=userid,
                user=user_name,
                message="blocked" if user_data["blocked"] else "unblocked",
            )

        elif "pending" in request.POST and user_data["pending"]:
            request.userdb.last_pending_time = 0
            if request.POST["pending"] == "0":
                user_data["pending"] = False
                request.userdb.save_user(user_data)
                request.actiondb.accept_user(
                    username=userid,
                    user=user_name,
                    message="accepted",
                )
            else:
                request.userdb.remove_user(user_data, userid)
        return home(request)
    userc = request.userdb.user_cache.find_one({"username": user_name})
    hours = int(userc["cpu_hours"]) if userc is not None else 0

    if user_data["tests_repo"] != "":
        user, repo = gh.parse_repo(user_data["tests_repo"])
        extract_repo_from_link = f"{user}/{repo}"
    else:
        extract_repo_from_link = ""

    return {
        "format_date": format_date,
        "user": user_data,
        "limit": request.userdb.get_machine_limit(user_name),
        "hours": hours,
        "profile": profile,
        "extract_repo_from_link": extract_repo_from_link,
    }


@view_config(route_name="contributors", renderer="contributors.mak")
def contributors(request):
    users_list = list(request.userdb.user_cache.find())
    users_list.sort(key=lambda k: k["cpu_hours"], reverse=True)
    return {
        "users": users_list,
        "approver": request.has_permission("approve_run"),
    }


@view_config(route_name="contributors_monthly", renderer="contributors.mak")
def contributors_monthly(request):
    users_list = list(request.userdb.top_month.find())
    users_list.sort(key=lambda k: k["cpu_hours"], reverse=True)
    return {
        "users": users_list,
        "approver": request.has_permission("approve_run"),
    }


def get_master_info(
    user="official-stockfish", repo="Stockfish", ignore_rate_limit=False
):
    try:
        commits = gh.get_commits(
            user=user, repo=repo, ignore_rate_limit=ignore_rate_limit
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


def get_sha(branch, repo_url):
    """Resolves the git branch to sha commit"""
    user, repo = gh.parse_repo(repo_url)
    try:
        commit = gh.get_commit(user=user, repo=repo, branch=branch)
    except Exception as e:
        raise Exception(f"Unable to access developer repository {repo_url}: {str(e)}")
    if "sha" in commit:
        return commit["sha"], commit["commit"]["message"].split("\n")[0]
    else:
        return "", ""


def get_nets(commit_sha, repo_url):
    """Get the nets from evaluate.h or ucioption.cpp in the repo"""
    try:
        nets = []
        pattern = re.compile("nn-[a-f0-9]{12}.nnue")

        user, repo = gh.parse_repo(repo_url)
        options = gh.download_from_github(
            "/src/evaluate.h", user=user, repo=repo, branch=commit_sha, method="raw"
        ).decode()
        for line in options.splitlines():
            if "EvalFileDefaultName" in line and "define" in line:
                m = pattern.search(line)
                if m:
                    nets.append(m.group(0))

        if nets:
            return nets

        options = gh.download_from_github(
            "/src/ucioption.cpp", user=user, repo=repo, branch=commit_sha, method="raw"
        ).decode()
        for line in options.splitlines():
            if "EvalFile" in line and "Option" in line:
                m = pattern.search(line)
                if m:
                    nets.append(m.group(0))
        return nets
    except Exception as e:
        raise Exception(f"Unable to access developer repository {repo_url}: {str(e)}")


def parse_spsa_params(spsa):
    raw = spsa["raw_params"]
    params = []
    for line in raw.split("\n"):
        chunks = line.strip().split(",")
        if len(chunks) == 1 and chunks[0] == "":  # blank line
            continue
        if len(chunks) != 6:
            raise Exception("the line {} does not have 6 entries".format(chunks))
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


def validate_modify(request, run):
    now = datetime.now(UTC)
    if "start_time" not in run or (now - run["start_time"]).days > 30:
        request.session.flash("Run too old to be modified", "error")
        raise home(request)

    if "num-games" not in request.POST:
        request.session.flash("Unable to modify with no number of games!", "error")
        raise home(request)

    bad_values = not all(
        value is not None and value.replace("-", "").isdigit()
        for value in [
            request.POST["priority"],
            request.POST["num-games"],
            request.POST["throughput"],
        ]
    )

    if bad_values:
        request.session.flash("Bad values!", "error")
        raise home(request)

    num_games = int(request.POST["num-games"])
    if (
        num_games > run["args"]["num_games"]
        and "sprt" not in run["args"]
        and "spsa" not in run["args"]
    ):
        request.session.flash(
            "Unable to modify number of games in a fixed game test!", "error"
        )
        raise home(request)

    if "spsa" in run["args"] and num_games != run["args"]["num_games"]:
        request.session.flash(
            "Unable to modify number of games for SPSA tests, SPSA hyperparams are based off the initial number of games",
            "error",
        )
        raise home(request)

    max_games = 3200000
    if num_games > max_games:
        request.session.flash("Number of games must be <= " + str(max_games), "error")
        raise home(request)


def sanitize_options(options):
    try:
        options.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("Options must contain only ASCII characters")

    tokens = options.split()
    token_regex = re.compile(r"^[^\s=]+=[^\s=]+$", flags=re.ASCII)
    for token in tokens:
        if not token_regex.fullmatch(token):
            raise ValueError(
                "Each option must be a 'key=value' pair with no extra spaces and exactly one '='"
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
        "base_options": sanitize_options(request.POST["base-options"]),
        "new_options": sanitize_options(request.POST["new-options"]),
        "username": request.authenticated_userid,
        "tests_repo": request.POST["tests-repo"],
        "info": request.POST["run-info"],
    }
    try:
        # Deal with people that have changed their GitHub username
        # but still use the old repo url
        data["tests_repo"] = gh.normalize_repo(data["tests_repo"])
    except Exception as e:
        raise Exception(
            f"Unable to access developer repository {data['tests_repo']}: {str(e)}"
        ) from e

    user, repo = gh.parse_repo(data["tests_repo"])
    username = request.authenticated_userid
    u = request.userdb.get_user(username)

    # Deal with people that have forked from "mcostalba/Stockfish" instead
    # of from "official-stockfish/Stockfish".
    official_repo = "https://github.com/official-stockfish/Stockfish"
    master_repo = official_repo
    try:
        master_repo = gh.get_master_repo(user, repo, ignore_rate_limit=True)
    except Exception as e:
        print(
            f"Unable to determine master repo for {data['tests_repo']}: {str(e)}",
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
            else:
                request.session.flash(
                    message + " " + suffix_soft,
                    "warning",
                )
    odds = request.POST.get("odds", "off")  # off checkboxes are not posted
    if odds == "off":
        data["new_tc"] = data["tc"]

    validate(tc_schema, data["tc"], "data['tc']")
    validate(tc_schema, data["new_tc"], "data['new_tc']")

    if request.POST.get("rescheduled_from"):
        data["rescheduled_from"] = request.POST["rescheduled_from"]

    def strip_message(m):
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

    # Fill new_signature/info from commit info if left blank
    if len(data["new_signature"]) == 0 or len(data["info"]) == 0:
        try:
            c = gh.get_commit(
                user=user, repo=repo, branch=data["new_tag"], ignore_rate_limit=True
            )
        except Exception as e:
            raise Exception(
                f"Unable to access developer repository {data['tests_repo']}: {str(e)}"
            ) from e
        if "commit" not in c:
            raise Exception(
                f"Cannot find branch {data['new_tag']} in developer repository"
            )
        if len(data["new_signature"]) == 0:
            bench_search = re.compile(r"(^|\s)[Bb]ench[ :]+([1-9]\d{5,7})(?!\d)")
            lines = c["commit"]["message"].split("\n")
            for line in reversed(lines):  # Iterate in reverse to find the last match
                m = bench_search.search(line)
                if m:
                    data["new_signature"] = m.group(2)
                    break
            else:
                raise Exception(
                    "This commit has no signature: please supply it manually."
                )
        if len(data["info"]) == 0:
            data["info"] = strip_message(c["commit"]["message"])

    if request.POST["stop_rule"] == "spsa":
        data["base_signature"] = data["new_signature"]

    for k, v in data.items():
        if len(v) == 0:
            raise Exception("Missing required option: {}".format(k))

    # Handle boolean options
    data["auto_purge"] = request.POST.get("auto-purge") is not None
    # checkbox is to _disable_ adjudication
    data["adjudication"] = request.POST.get("adjudication") is None

    # In case of reschedule use old data,
    # otherwise resolve sha and update user's tests_repo
    if "resolved_base" in request.POST:
        data["resolved_base"] = request.POST["resolved_base"]
        data["resolved_new"] = request.POST["resolved_new"]
        data["msg_base"] = request.POST["msg_base"]
        data["msg_new"] = request.POST["msg_new"]
    else:
        data["resolved_base"], data["msg_base"] = get_sha(
            data["base_tag"], data["tests_repo"]
        )
        data["resolved_new"], data["msg_new"] = get_sha(
            data["new_tag"], data["tests_repo"]
        )
        u = request.userdb.get_user(data["username"])
        if u.get("tests_repo", "") != data["tests_repo"]:
            u["tests_repo"] = data["tests_repo"]
            request.userdb.save_user(u)

    if len(data["resolved_base"]) == 0 or len(data["resolved_new"]) == 0:
        raise Exception("Unable to find branch!")

    # Check entered bench
    if data["base_tag"] == "master":
        master_info = get_master_info(user=user, repo=repo, ignore_rate_limit=True)
        if master_info is None or master_info["bench"] != data["base_signature"]:
            raise Exception(
                "Bench signature of Base master does not match, "
                + 'please "git pull upstream master" !'
            )

    stop_rule = request.POST["stop_rule"]

    # Store nets info
    data["base_nets"] = get_nets(data["resolved_base"], data["tests_repo"])
    data["new_nets"] = get_nets(data["resolved_new"], data["tests_repo"])

    # Test existence of nets
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
            )
        )

    # Integer parameters
    data["threads"] = int(request.POST["threads"])
    data["priority"] = int(request.POST["priority"])
    data["throughput"] = int(request.POST["throughput"])

    if data["threads"] <= 0:
        raise Exception("Threads must be >= 1")

    if stop_rule == "sprt":
        # Too small a number results in many API calls, especially with highly concurrent workers.
        # This expression results in 32 games per batch for single threaded STC games.
        # This means a batch with be completed in roughly 2 minutes on a 8 core worker.
        # This expression adjusts the batch size for threads and TC, to keep timings somewhat similar.
        sprt_batch_size_games = 2 * max(
            1, int(0.5 + 16 / get_tc_ratio(data["tc"], data["threads"]))
        )
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
        )  # game pairs
        # Limit on number of games played.
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
        data["spsa"]["params"] = parse_spsa_params(data["spsa"])
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


def del_tasks(run):
    run = copy.copy(run)
    run.pop("tasks", None)
    run = copy.deepcopy(run)
    return run


def update_nets(request, run):
    run_id = str(run["_id"])
    data = run["args"]
    base_nets, new_nets, missing_nets = [], [], []
    for net_name in set(data["base_nets"]) | set(data["new_nets"]):
        net = request.rundb.get_nn(net_name)
        if net is None:
            # This should never happen
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
            )
        )

    tests_repo_ = tests_repo(run)
    user, repo = gh.parse_repo(tests_repo_)
    try:
        if gh.is_master(
            run["args"]["resolved_base"],
        ):
            for net in base_nets:
                if "is_master" not in net:
                    net["is_master"] = True
                    request.rundb.update_nn(net)
    except Exception as e:
        print(f"Unable to evaluate is_master({run['args']['resolved_base']}): {str(e)}")

    for net in new_nets:
        if "first_test" not in net:
            net["first_test"] = {"id": run_id, "date": datetime.now(UTC)}
        net["last_test"] = {"id": run_id, "date": datetime.now(UTC)}
        request.rundb.update_nn(net)


def new_run_message(request, run):
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


@view_config(route_name="tests_run", renderer="tests_run.mak", require_csrf=True)
def tests_run(request):
    user_id = ensure_logged_in(request)

    if request.method == "POST":
        try:
            data = validate_form(request)
            if is_sprt_ltc_data(data):
                data["info"] = "LTC: " + data["info"]
            run_id = request.rundb.new_run(**data)
            run = request.rundb.get_run(run_id)
            request.actiondb.new_run(
                username=user_id,
                run=run,
                message=new_run_message(request, run),
            )
            request.session.flash(
                "The test was submitted to the queue. Please wait for approval."
            )
            return HTTPFound(location="/tests/view/" + str(run_id) + "?follow=1")
        except Exception as e:
            request.session.flash(str(e), "error")

    run_args = {}
    if "id" in request.params:
        run = request.rundb.get_run(request.params["id"])
        if run is None:
            raise HTTPNotFound()
        run_args = copy.deepcopy(run["args"])
        if "spsa" in run_args:
            # needs deepcopy
            run_args["spsa"]["A"] = (
                round(1000 * 2 * run_args["spsa"]["A"] / run_args["num_games"]) / 1000
            )

    username = request.authenticated_userid
    u = request.userdb.get_user(username)

    # Make sure that a newly committed book can be used immediately
    request.rundb.update_books()
    # Make sure that when the test is viewed after submission,
    # official_master_sha is up to date
    gh.update_official_master_sha()

    return {
        "args": run_args,
        "is_rerun": len(run_args) > 0,
        "rescheduled_from": request.params["id"] if "id" in request.params else None,
        "tests_repo": u.get("tests_repo", ""),
        "master_info": get_master_info(ignore_rate_limit=True),
        "valid_books": request.rundb.books.keys(),
        "pt_info": request.rundb.pt_info,
    }


def is_same_user(request, run):
    return run["args"]["username"] == request.authenticated_userid


def can_modify_run(request, run):
    return is_same_user(request, run) or request.has_permission("approve_run")


@view_config(route_name="tests_modify", require_csrf=True, request_method="POST")
def tests_modify(request):
    userid = ensure_logged_in(request)

    run = request.rundb.get_run(request.POST["run"])
    if run is None:
        request.session.flash("No run with this id", "error")
        return home(request)

    validate_modify(request, run)

    if not can_modify_run(request, run):
        request.session.flash("Unable to modify another user's run!", "error")
        return home(request)

    run_id = run["_id"]
    is_approver = request.has_permission("approve_run")
    was_approved = run["approved"]
    if (
        not is_approver
        and was_approved
        and (
            (int(request.POST["throughput"]) > max(run["args"]["throughput"], 100))
            or (int(request.POST["priority"]) > max(run["args"]["priority"], 0))
            or run["failed"]
        )
    ):
        request.actiondb.approve_run(username=userid, run=run, message="unapproved")
        with request.rundb.active_run_lock(run_id):
            run["approved"] = False
            run["approver"] = ""

    before = del_tasks(run)
    with request.rundb.active_run_lock(run_id):
        run["args"]["num_games"] = int(request.POST["num-games"])
        run["args"]["priority"] = int(request.POST["priority"])
        run["args"]["throughput"] = int(request.POST["throughput"])
        run["args"]["auto_purge"] = True if request.POST.get("auto_purge") else False
        if (
            is_same_user(request, run)
            and "info" in request.POST
            and request.POST["info"].strip() != ""
        ):
            run["args"]["info"] = request.POST["info"].strip()

    if is_undecided(run):
        request.rundb.set_active_run(run)

    after = del_tasks(run)
    message = []

    for k in ("priority", "num_games", "throughput", "auto_purge"):
        try:
            before_ = before["args"][k]
            after_ = after["args"][k]
        except KeyError:
            pass
        else:
            if before_ != after_:
                message.append(
                    "{} changed from {} to {}".format(
                        k.replace("_", "-"), before_, after_
                    )
                )

    message = "modify: " + ", ".join(message)
    request.actiondb.modify_run(
        username=request.authenticated_userid,
        run=before,
        message=message,
    )
    if run["approved"]:
        request.session.flash("The test was successfully modified!")
    elif was_approved:
        request.session.flash(
            "The test was successfully modified but it will have to be reapproved...",
            "warning",
        )
    else:
        request.session.flash(
            "The test was successfully modified. Please wait for approval.",
        )
    return home(request)


@view_config(route_name="tests_stop", require_csrf=True, request_method="POST")
def tests_stop(request):
    if not request.authenticated_userid:
        request.session.flash("Please login")
        return HTTPFound(location=request.route_url("login"))
    if "run-id" in request.POST:
        run = request.rundb.get_run(request.POST["run-id"])
        if not can_modify_run(request, run):
            request.session.flash("Unable to modify another users run!", "error")
            return home(request)

        request.rundb.stop_run(request.POST["run-id"])
        request.actiondb.stop_run(
            username=request.authenticated_userid,
            run=run,
            message="User stop",
        )
        request.session.flash("Stopped run")
    return home(request)


@view_config(route_name="tests_approve", require_csrf=True, request_method="POST")
def tests_approve(request):
    if not request.authenticated_userid:
        return HTTPFound(location=request.route_url("login"))
    if not request.has_permission("approve_run"):
        request.session.flash("Please login as approver")
        return HTTPFound(location=request.route_url("login"))
    username = request.authenticated_userid
    run_id = request.POST["run-id"]
    run, message = request.rundb.approve_run(run_id, username)
    if run is None:
        request.session.flash(message, "error")
    else:
        try:
            update_nets(request, run)
        except Exception as e:
            request.session.flash(str(e), "error")
        request.actiondb.approve_run(username=username, run=run, message="approved")
        request.session.flash(message)
    return home(request)


@view_config(route_name="tests_purge", require_csrf=True, request_method="POST")
def tests_purge(request):
    run = request.rundb.get_run(request.POST["run-id"])
    if not request.has_permission("approve_run") and not is_same_user(request, run):
        request.session.flash(
            "Only approvers or the submitting user can purge the run."
        )
        return HTTPFound(location=request.route_url("login"))

    # More relaxed conditions than with auto purge.
    message = request.rundb.purge_run(run, p=0.01, res=4.5)

    username = request.authenticated_userid
    request.actiondb.purge_run(
        username=username,
        run=run,
        message=(
            f"Manual purge (not performed): {message}" if message else "Manual purge"
        ),
    )
    if message != "":
        request.session.flash(message)
        return home(request)

    request.session.flash("Purged run")
    return home(request)


@view_config(route_name="tests_delete", require_csrf=True, request_method="POST")
def tests_delete(request):
    if not request.authenticated_userid:
        request.session.flash("Please login")
        return HTTPFound(location=request.route_url("login"))
    if "run-id" in request.POST:
        run = request.rundb.get_run(request.POST["run-id"])
        if not can_modify_run(request, run):
            request.session.flash("Unable to modify another users run!", "error")
            return home(request)

        request.rundb.set_inactive_run(run)
        run["deleted"] = True
        try:
            validate(runs_schema, run, "run")
        except ValidationError as e:
            message = (
                f"The run object {request.POST['run-id']} does not validate: {str(e)}"
            )
            print(message, flush=True)
            if "version" in run and run["version"] >= RUN_VERSION:
                request.actiondb.log_message(
                    username="fishtest.system",
                    message=message,
                )
        request.rundb.buffer(run, priority=Prio.SAVE_NOW)

        request.actiondb.delete_run(
            username=request.authenticated_userid,
            run=run,
        )
        request.session.flash("Deleted run")
    return home(request)


def get_page_title(run):
    if run["args"].get("sprt"):
        page_title = "SPRT {} vs {}".format(
            run["args"]["new_tag"], run["args"]["base_tag"]
        )
    elif run["args"].get("spsa"):
        page_title = "SPSA {}".format(run["args"]["new_tag"])
    else:
        page_title = "{} games - {} vs {}".format(
            run["args"]["num_games"], run["args"]["new_tag"], run["args"]["base_tag"]
        )
    return page_title


@view_config(route_name="tests_live_elo", renderer="tests_live_elo.mak")
def tests_live_elo(request):
    run = request.rundb.get_run(request.matchdict["id"])
    if run is None or "sprt" not in run["args"]:
        raise HTTPNotFound()
    return {"run": run, "page_title": get_page_title(run)}


@view_config(route_name="tests_stats", renderer="tests_stats.mak")
def tests_stats(request):
    run = request.rundb.get_run(request.matchdict["id"])
    if run is None:
        raise HTTPNotFound()
    return {"run": run, "page_title": get_page_title(run)}


@view_config(route_name="tests_tasks", renderer="tasks.mak")
def tests_tasks(request):
    run = request.rundb.get_run(request.matchdict["id"])
    if run is None:
        raise HTTPNotFound()
    chi2 = get_chi2(run["tasks"])

    try:
        show_task = int(request.params.get("show_task", -1))
    except ValueError:
        show_task = -1
    if show_task >= len(run["tasks"]) or show_task < -1:
        show_task = -1

    return {
        "run": run,
        "approver": request.has_permission("approve_run"),
        "show_task": show_task,
        "chi2": chi2,
    }


@view_config(route_name="tests_machines", http_cache=10, renderer="machines.mak")
def tests_machines(request):
    return {"machines_list": request.rundb.get_machines()}


@view_config(route_name="tests_view", renderer="tests_view.mak")
def tests_view(request):
    run = request.rundb.get_run(request.matchdict["id"])
    if run is None:
        raise HTTPNotFound()
    run_id = str(run["_id"])
    follow = 1 if "follow" in request.params else 0
    run_args = [("id", str(run["_id"]), "")]
    if run.get("rescheduled_from"):
        run_args.append(("rescheduled_from", run["rescheduled_from"], ""))

    for name in (
        "new_tag",
        "new_signature",
        "new_options",
        "resolved_new",
        "new_net",
        "new_nets",
        "base_tag",
        "base_signature",
        "base_options",
        "resolved_base",
        "base_net",
        "base_nets",
        "sprt",
        "num_games",
        "spsa",
        "tc",
        "new_tc",
        "threads",
        "book",
        "book_depth",
        "auto_purge",
        "priority",
        "itp",
        "throughput",
        "username",
        "tests_repo",
        "master_repo",
        "adjudication",
        "info",
    ):
        if name not in run["args"]:
            continue

        value = run["args"][name]
        url = ""

        if name == "new_tag" and "msg_new" in run["args"]:
            value += "  (" + run["args"]["msg_new"][:50] + ")"

        if name == "base_tag" and "msg_base" in run["args"]:
            value += "  (" + run["args"]["msg_base"][:50] + ")"

        if name in ("new_nets", "base_nets"):
            value = ", ".join(value)

        if name == "sprt" and value != "-":
            value = "elo0: {:.2f} alpha: {:.2f} elo1: {:.2f} beta: {:.2f} state: {} ({})".format(
                value["elo0"],
                value["alpha"],
                value["elo1"],
                value["beta"],
                value.get("state", "-"),
                value.get("elo_model", "BayesElo"),
            )

        if name == "spsa" and value != "-":
            iter_local = value["iter"] + 1  # start from 1 to avoid division by zero
            A = value["A"]
            alpha = value["alpha"]
            gamma = value["gamma"]
            summary = "iter: {:d}, A: {:d}, alpha: {:0.3f}, gamma: {:0.3f}".format(
                iter_local,
                A,
                alpha,
                gamma,
            )
            params = value["params"]
            value = [summary]
            for p in params:
                c_iter = p["c"] / (iter_local**gamma)
                r_iter = p["a"] / (A + iter_local) ** alpha / c_iter**2
                value.append(
                    [
                        p["name"],
                        "{:.2f}".format(p["theta"]),
                        int(p["start"]),
                        int(p["min"]),
                        int(p["max"]),
                        "{:.3f}".format(c_iter),
                        "{:.3f}".format(p["c_end"]),
                        "{:.2e}".format(r_iter),
                        "{:.2e}".format(p["r_end"]),
                    ]
                )

        tests_repo_ = tests_repo(run)
        user, repo = gh.parse_repo(tests_repo_)
        if name == "tests_repo":
            value = tests_repo_
            url = value

        if name == "master_repo":
            url = value

        if name == "new_tag":
            url = gh.commit_url(
                user=user, repo=repo, branch=run["args"]["resolved_new"]
            )
        elif name == "base_tag":
            url = gh.commit_url(
                user=user, repo=repo, branch=run["args"]["resolved_base"]
            )

        if name == "spsa":
            run_args.append(("spsa", value, ""))
        else:
            run_args.append((name, html.escape(str(value)), url))

    active = 0
    cores = 0
    for task in run["tasks"]:
        if task["active"]:
            active += 1
            cores += task["worker_info"]["concurrency"]

    chi2 = get_chi2(run["tasks"])

    try:
        show_task = int(request.params.get("show_task", -1))
    except ValueError:
        show_task = -1
    if show_task >= len(run["tasks"]) or show_task < -1:
        show_task = -1

    same_user = is_same_user(request, run)

    spsa_data = request.rundb.spsa_handler.get_spsa_data(run_id)

    same_options = True
    try:
        # use sanitize_options for compatibility with old tests
        same_options = sanitize_options(run["args"]["new_options"]) == sanitize_options(
            run["args"]["base_options"]
        )
    except Exception:
        pass

    notes = []
    if (
        "spsa" not in run["args"]
        and run["args"]["base_signature"] == run["args"]["new_signature"]
    ):
        notes.append("new signature and base signature are identical")
    if run["deleted"]:
        notes.append("this test has been deleted")

    warnings = []
    if run["args"]["throughput"] > 100:
        warnings.append("throughput exceeds the normal limit")
    if run["args"]["priority"] > 0:
        warnings.append("priority exceeds the normal limit")
    if not reasonable_run_hashes(run):
        warnings.append("hash options are too low or too high for this TC")
    if not same_options:
        warnings.append("base options differ from new options")
    if (f := run.get("failures", 0)) > 0:
        warnings.append(f"this test had {f} {plural(f, 'failure')}")
    elif run["failed"]:
        # for backward compatibility
        warnings.append("this is a failed test")
    if run["args"]["tc"] != run["args"]["new_tc"]:
        warnings.append("this is a test with time odds")
    book_exits = request.rundb.books.get(run["args"]["book"], {}).get("total", 100000)
    if book_exits < 100000:
        warnings.append(f"this test uses a small book with only {book_exits} exits")
    if "master_repo" in run["args"]:  # if present then it is non-standard
        warnings.append(
            "the developer repository is not forked from official-stockfish/Stockfish"
        )

    def allow_github_api_calls():
        # Avoid making pointless GitHub api calls on behalf of
        # crawlers
        if "master_repo" in run["args"]:  # if present then it is non-standard
            return False
        if request.authenticated_userid:
            return True
        now = datetime.now(UTC)
        # Period should be short enough so that it can be
        # served from the api cache!
        if (now - run["last_updated"]).days > 30:
            return False
        return True

    user, repo = gh.parse_repo(tests_repo(run))

    anchor_url = gh.compare_branches_url(
        user1="official-stockfish",
        branch1=gh.official_master_sha,
        user2=user,
        branch2=run["args"]["resolved_base"],
    )
    anchor = f'<a class="alert-link" href="{anchor_url}" target="_blank" rel="noopener">base diff</a>'
    use_3dot_diff = False
    if "spsa" not in run["args"] and allow_github_api_calls():
        irl = bool(request.authenticated_userid)
        try:
            if not gh.is_master(
                run["args"]["resolved_new"],
            ):
                # new hasn't been merged
                if not gh.is_master(
                    run["args"]["resolved_base"],
                    ignore_rate_limit=irl,
                ):
                    warnings.append(f"base is not an ancestor of master: {anchor}")
                elif not gh.is_ancestor(
                    user1=user,
                    sha1=run["args"]["resolved_base"],
                    sha2=run["args"]["resolved_new"],
                    ignore_rate_limit=irl,
                ):
                    warnings.append("base is not an ancestor of new")
                else:
                    merge_base_commit = gh.get_merge_base_commit(
                        sha1=gh.official_master_sha,
                        user2=user,
                        sha2=run["args"]["resolved_new"],
                        ignore_rate_limit=irl,
                    )
                    if merge_base_commit != run["args"]["resolved_base"]:
                        warnings.append(
                            "base is not the latest common ancestor of new and master"
                        )
            use_3dot_diff = gh.is_ancestor(
                user1=user,
                sha1=run["args"]["resolved_base"],
                sha2=run["args"]["resolved_new"],
                ignore_rate_limit=irl,
            )
        except Exception as e:
            print(f"Exception processing api calls for {run['_id']}: {str(e)}")

    return {
        "run": run,
        "run_args": run_args,
        "page_title": get_page_title(run),
        "approver": request.has_permission("approve_run"),
        "chi2": chi2,
        "totals": "({} active worker{} with {} core{})".format(
            active, ("s" if active != 1 else ""), cores, ("s" if cores != 1 else "")
        ),
        "tasks_shown": show_task != -1 or request.cookies.get("tasks_state") == "Hide",
        "show_task": show_task,
        "follow": follow,
        "can_modify_run": can_modify_run(request, run),
        "same_user": same_user,
        "pt_info": request.rundb.pt_info,
        "document_size": len(bson.BSON.encode(run)),
        "spsa_data": spsa_data,
        "notes": notes,
        "warnings": warnings,
        "use_3dot_diff": use_3dot_diff,
        "allow_github_api_calls": allow_github_api_calls(),
    }


def get_paginated_finished_runs(request):
    username = request.matchdict.get("username", "")
    success_only = request.params.get("success_only", False)
    yellow_only = request.params.get("yellow_only", False)
    ltc_only = request.params.get("ltc_only", False)

    page_param = request.params.get("page", "")
    page_idx = max(0, int(page_param) - 1) if page_param.isdigit() else 0
    page_size = 25

    finished_runs, num_finished_runs = request.rundb.get_finished_runs(
        username=username,
        success_only=success_only,
        yellow_only=yellow_only,
        ltc_only=ltc_only,
        skip=page_idx * page_size,
        limit=page_size,
    )

    query_params = ""
    if success_only:
        query_params += "&success_only=1"
    if yellow_only:
        query_params += "&yellow_only=1"
    if ltc_only:
        query_params += "&ltc_only=1"
    pages = pagination(page_idx, num_finished_runs, page_size, query_params)

    failed_runs = []
    if page_idx == 0:
        for run in finished_runs:
            # Look for failed runs
            if "failed" in run and run["failed"]:
                failed_runs.append(run)

    return {
        "finished_runs": finished_runs,
        "finished_runs_pages": pages,
        "num_finished_runs": num_finished_runs,
        "failed_runs": failed_runs,
        "page_idx": page_idx,
    }


@view_config(route_name="tests_finished", renderer="tests_finished.mak")
def tests_finished(request):
    return get_paginated_finished_runs(request)


@view_config(route_name="tests_user", renderer="tests_user.mak")
def tests_user(request):
    request.response.headerlist.extend(
        (
            ("Cache-Control", "no-store"),
            ("Expires", "0"),
        )
    )
    username = request.matchdict.get("username", "")
    user_data = request.userdb.get_user(username)
    if user_data is None:
        raise HTTPNotFound()
    is_approver = request.has_permission("approve_run")
    response = {
        **get_paginated_finished_runs(request),
        "username": username,
        "is_approver": is_approver,
    }
    page_param = request.params.get("page", "")
    if not page_param.isdigit() or int(page_param) <= 1:
        response["runs"] = request.rundb.aggregate_unfinished_runs(
            username=username,
        )[0]
    # page 2 and beyond only show finished test results
    return response


def homepage_results(request):
    # Get updated results for unfinished runs + finished runs

    (
        runs,
        pending_hours,
        cores,
        nps,
        games_per_minute,
        machines_count,
    ) = request.rundb.aggregate_unfinished_runs()
    return {
        **get_paginated_finished_runs(request),
        "runs": runs,
        "machines_count": machines_count,
        "pending_hours": "{:.1f}".format(pending_hours),
        "cores": cores,
        "nps": nps,
        "games_per_minute": int(games_per_minute),
    }


@view_config(route_name="tests", renderer="tests.mak")
def tests(request):
    request.response.headerlist.extend(
        (
            ("Cache-Control", "no-store"),
            ("Expires", "0"),
        )
    )
    page_param = request.params.get("page", "")
    if page_param.isdigit() and int(page_param) > 1:
        # page 2 and beyond only show finished test results
        return get_paginated_finished_runs(request)

    last_tests = homepage_results(request)

    return {
        **last_tests,
        "machines_shown": request.cookies.get("machines_state") == "Hide",
    }
