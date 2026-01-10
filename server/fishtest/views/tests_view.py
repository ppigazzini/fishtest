"""FastAPI UI routes for individual test views.

Ports Pyramid routes:
- `/tests/view/{id}` (full run detail page)
- `/tests/tasks/{id}` (tasks table fragment)

These reuse the existing Mako templates and existing RunDb logic.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, cast

import bson
import fishtest.github_api as gh
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fishtest.cookie_session import commit_session, load_session
from fishtest.mako import default_template_lookup, render_template
from fishtest.template_request import TemplateRequest
from fishtest.util import get_chi2, plural, reasonable_run_hashes, tests_repo
from fishtest.views.common import authenticated_user, is_https

if TYPE_CHECKING:
    from fishtest.rundb import RunDb
    from fishtest.userdb import UserDb


router = APIRouter(tags=["ui"], include_in_schema=False)
TEMPLATE_LOOKUP = default_template_lookup()

APPROVERS_GROUP: Final[str] = "group:approvers"

_LOGGER = logging.getLogger(__name__)
_OPTION_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"^[^\s=]+=[^\s=]+$",
    flags=re.ASCII,
)

_ANON_GITHUB_CALLS_MAX_DAYS: Final[int] = 30
_MAX_THROUGHPUT: Final[int] = 100
_DEFAULT_BOOK_EXITS: Final[int] = 100_000
_MAX_PRIORITY: Final[int] = 0


class _InvalidOptionsError(ValueError):
    """Raised when a `new_options`/`base_options` string is malformed."""


def _to_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise TypeError


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError


@dataclass(frozen=True)
class _Viewer:
    username: str | None
    is_approver: bool


@dataclass(frozen=True)
class _RepoInfo:
    tests_repo_value: str
    gh_user: str
    gh_repo: str


def _viewer_from_request(*, request: Request) -> _Viewer:
    session = load_session(request)
    username = authenticated_user(session)
    if not username:
        return _Viewer(username=None, is_approver=False)

    userdb = cast("UserDb", request.app.state.userdb)
    groups = userdb.get_user_groups(username) or []
    return _Viewer(username=username, is_approver=APPROVERS_GROUP in groups)


def _sanitize_options(options: str) -> str:
    try:
        options.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _InvalidOptionsError from exc

    tokens = options.split()
    for token in tokens:
        if not _OPTION_TOKEN_RE.fullmatch(token):
            raise _InvalidOptionsError
    return " ".join(tokens)


def _int_query(*, request: Request, key: str, default: int) -> int:
    value = request.query_params.get(key, "")
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _show_task(*, request: Request, run: dict[str, Any]) -> int:
    show_task = _int_query(request=request, key="show_task", default=-1)
    if show_task >= len(run.get("tasks", [])) or show_task < -1:
        return -1
    return show_task


def _tasks_shown(*, request: Request, show_task: int) -> bool:
    return show_task != -1 or request.cookies.get("tasks_state") == "Hide"


def _can_modify_run(*, viewer: _Viewer, run: dict[str, Any]) -> bool:
    if viewer.username is None:
        return False
    return bool(viewer.is_approver or run["args"]["username"] == viewer.username)


def _same_user(*, viewer: _Viewer, run: dict[str, Any]) -> bool:
    if viewer.username is None:
        return False
    return run["args"]["username"] == viewer.username


def _allow_github_api_calls(
    *,
    run: dict[str, Any],
    viewer: _Viewer,
) -> bool:
    if "master_repo" in run.get("args", {}):
        return False
    if viewer.username:
        return True
    now = datetime.now(UTC)
    last_updated = run.get("last_updated")
    if not isinstance(last_updated, datetime):
        return False
    return (now - last_updated).days <= _ANON_GITHUB_CALLS_MAX_DAYS


def _format_sprt(value: dict[str, object]) -> str:
    return (
        f"elo0: {_to_float(value['elo0']):.2f} "
        f"alpha: {_to_float(value['alpha']):.2f} "
        f"elo1: {_to_float(value['elo1']):.2f} "
        f"beta: {_to_float(value['beta']):.2f} "
        f"state: {value.get('state', '-')} ({value.get('elo_model', 'BayesElo')})"
    )


def _format_spsa(value: dict[str, object]) -> list[object]:
    iter_local = _to_int(value["iter"]) + 1
    a_param = _to_int(value["A"])
    alpha = _to_float(value["alpha"])
    gamma = _to_float(value["gamma"])
    summary = (
        f"iter: {iter_local:d}, A: {a_param:d}, "
        f"alpha: {alpha:0.3f}, gamma: {gamma:0.3f}"
    )

    params = cast("list[dict[str, object]]", value["params"])
    rows: list[object] = [summary]
    for param in params:
        c_iter = _to_float(param["c"]) / (iter_local**gamma)
        r_iter = _to_float(param["a"]) / (a_param + iter_local) ** alpha / c_iter**2
        rows.append(
            [
                str(param["name"]),
                f"{_to_float(param['theta']):.2f}",
                _to_int(param["start"]),
                _to_int(param["min"]),
                _to_int(param["max"]),
                f"{c_iter:.3f}",
                f"{_to_float(param['c_end']):.3f}",
                f"{r_iter:.2e}",
                f"{_to_float(param['r_end']):.2e}",
            ],
        )
    return rows


def _maybe_truncate_msg(*, tag: object, msg: object) -> str:
    return f"{tag}  ({str(msg)[:50]})"


def _normalize_value_for_run_args(*, name: str, value: object) -> object:
    if name in {"new_nets", "base_nets"} and isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return value


def _maybe_url_for_run_arg(
    *,
    name: str,
    run: dict[str, Any],
    repo_info: _RepoInfo,
    value: object,
) -> str:
    if name == "tests_repo":
        return repo_info.tests_repo_value
    if name == "master_repo":
        return str(value)
    if name == "new_tag":
        return gh.commit_url(
            user=repo_info.gh_user,
            repo=repo_info.gh_repo,
            branch=run["args"]["resolved_new"],
        )
    if name == "base_tag":
        return gh.commit_url(
            user=repo_info.gh_user,
            repo=repo_info.gh_repo,
            branch=run["args"]["resolved_base"],
        )
    return ""


def _run_args(
    *,
    run: dict[str, Any],
) -> list[tuple[str, object, str]]:
    run_args: list[tuple[str, object, str]] = [("id", str(run["_id"]), "")]
    if run.get("rescheduled_from"):
        run_args.append(("rescheduled_from", run["rescheduled_from"], ""))

    tests_repo_value = tests_repo(run)
    gh_user, gh_repo = gh.parse_repo(tests_repo_value)
    repo_info = _RepoInfo(
        tests_repo_value=tests_repo_value,
        gh_user=gh_user,
        gh_repo=gh_repo,
    )
    args = cast("dict[str, object]", run.get("args", {}))

    names = (
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
        "arch_filter",
        "info",
    )

    for name in names:
        if name not in args:
            continue

        value: object = args[name]
        if name == "new_tag" and "msg_new" in args:
            value = _maybe_truncate_msg(tag=value, msg=args["msg_new"])
        if name == "base_tag" and "msg_base" in args:
            value = _maybe_truncate_msg(tag=value, msg=args["msg_base"])

        value = _normalize_value_for_run_args(name=name, value=value)

        if name == "sprt" and value != "-" and isinstance(value, dict):
            value = _format_sprt(cast("dict[str, object]", value))

        if name == "spsa" and value != "-" and isinstance(value, dict):
            run_args.append(
                (
                    "spsa",
                    _format_spsa(cast("dict[str, object]", value)),
                    "",
                ),
            )
            continue

        if name == "tests_repo":
            value = tests_repo_value

        url = _maybe_url_for_run_arg(
            name=name,
            run=run,
            repo_info=repo_info,
            value=value,
        )

        run_args.append((name, html.escape(str(value)), url))

    return run_args


def _same_options(*, run: dict[str, Any]) -> bool:
    try:
        return _sanitize_options(run["args"]["new_options"]) == _sanitize_options(
            run["args"]["base_options"],
        )
    except Exception:  # noqa: BLE001
        return True


def _notes(*, run: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if (
        "spsa" not in run["args"]
        and run["args"]["base_signature"] == run["args"]["new_signature"]
    ):
        notes.append("new signature and base signature are identical")
    if run.get("deleted"):
        notes.append("this test has been deleted")
    return notes


def _warnings(*, run: dict[str, Any], same_options: bool) -> list[str]:
    warnings: list[str] = []
    if run["args"].get("throughput", 0) > _MAX_THROUGHPUT:
        warnings.append("throughput exceeds the normal limit")
    if run["args"].get("priority", 0) > _MAX_PRIORITY:
        warnings.append("priority exceeds the normal limit")
    if not reasonable_run_hashes(run):
        warnings.append("hash options are too low or too high for this TC")
    if not same_options:
        warnings.append("base options differ from new options")

    failures = int(run.get("failures", 0) or 0)
    if failures > 0:
        warnings.append(f"this test had {failures} {plural(failures, 'failure')}")
    elif run.get("failed"):
        warnings.append("this is a failed test")

    if run["args"].get("tc") != run["args"].get("new_tc"):
        warnings.append("this is a test with time odds")
    if run["args"].get("arch_filter", ""):
        warnings.append("this test has a non-trivial arch filter")

    if "master_repo" in run["args"]:
        warnings.append(
            "the developer repository is not forked from official-stockfish/Stockfish",
        )
    return warnings


def _notes_and_option_warnings(*, run: dict[str, Any]) -> tuple[list[str], list[str]]:
    same = _same_options(run=run)
    return _notes(run=run), _warnings(run=run, same_options=same)


def _warnings_github(
    *,
    run: dict[str, Any],
    viewer: _Viewer,
    allow_github_calls: bool,
    existing_warnings: list[str],
) -> tuple[list[str], bool]:
    warnings = list(existing_warnings)
    use_3dot_diff = False

    if "spsa" in run["args"] or not allow_github_calls:
        return warnings, use_3dot_diff

    try:
        user, _repo = gh.parse_repo(gh.normalize_repo(tests_repo(run)))
    except Exception as exc:  # noqa: BLE001
        user, _repo = gh.parse_repo(tests_repo(run))
        _LOGGER.info("Unable to normalize_repo: %s", exc)

    anchor_url = gh.compare_branches_url(
        user1="official-stockfish",
        branch1=gh.official_master_sha,
        user2=user,
        branch2=run["args"]["resolved_base"],
    )
    anchor = (
        f'<a class="alert-link" href="{anchor_url}" target="_blank" rel="noopener">'
        "base diff</a>"
    )

    ignore_rate_limit = bool(viewer.username)

    try:
        if not gh.is_master(run["args"]["resolved_new"]):
            base_is_master = gh.is_master(
                run["args"]["resolved_base"],
                ignore_rate_limit=ignore_rate_limit,
            )
            if not base_is_master:
                warnings.append(f"base is not an ancestor of master: {anchor}")
            elif not gh.is_ancestor(
                user1=user,
                sha1=run["args"]["resolved_base"],
                sha2=run["args"]["resolved_new"],
                ignore_rate_limit=ignore_rate_limit,
            ):
                warnings.append("base is not an ancestor of new")
            else:
                merge_base_commit = gh.get_merge_base_commit(
                    sha1=gh.official_master_sha,
                    user2=user,
                    sha2=run["args"]["resolved_new"],
                    ignore_rate_limit=ignore_rate_limit,
                )
                if merge_base_commit != run["args"]["resolved_base"]:
                    warnings.append(
                        "base is not the latest common ancestor of new and master",
                    )

        use_3dot_diff = gh.is_ancestor(
            user1=user,
            sha1=run["args"]["resolved_base"],
            sha2=run["args"]["resolved_new"],
            ignore_rate_limit=ignore_rate_limit,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.info(
            "Exception processing GitHub calls for %s: %s",
            run.get("_id"),
            exc,
        )

    return warnings, use_3dot_diff


def _book_warning(*, rundb: RunDb, run: dict[str, Any]) -> str | None:
    book = run["args"].get("book")
    if not book:
        return None
    exits = int(rundb.books.get(book, {}).get("total", _DEFAULT_BOOK_EXITS))
    if exits >= _DEFAULT_BOOK_EXITS:
        return None
    return f"this test uses a small book with only {exits} exits"


def _page_title(*, run: dict[str, Any]) -> str:
    if run["args"].get("sprt"):
        return f"SPRT {run['args']['new_tag']} vs {run['args']['base_tag']}"
    if run["args"].get("spsa"):
        return f"SPSA {run['args']['new_tag']}"
    num_games = run["args"]["num_games"]
    new_tag = run["args"]["new_tag"]
    base_tag = run["args"]["base_tag"]
    return f"{num_games} games - {new_tag} vs {base_tag}"


def _render_page(
    *,
    request: Request,
    template_name: str,
    context: dict[str, object],
    cache_control: str,
) -> HTMLResponse:
    session = load_session(request)
    userdb = cast("UserDb", request.app.state.userdb)

    template_request = TemplateRequest(
        headers=request.headers,
        cookies=request.cookies,
        query_params=request.query_params,
        session=session,
        authenticated_userid=authenticated_user(session),
        userdb=userdb,
        url=str(request.url),
    )

    rendered = render_template(
        lookup=TEMPLATE_LOOKUP,
        template_name=template_name,
        context={"request": template_request, **context},
    )

    response = HTMLResponse(rendered.html)
    response.headers["Cache-Control"] = cache_control
    if cache_control == "no-store":
        response.headers["Expires"] = "0"

    commit_session(
        response=response,
        session=session,
        remember=False,
        secure=is_https(request),
    )
    return response


@router.get("/tests/tasks/{id}", response_class=HTMLResponse)
async def tests_tasks(request: Request, id: str) -> HTMLResponse:  # noqa: A002
    """Render the tasks table fragment for a run."""
    rundb = cast("RunDb", request.app.state.rundb)
    run = rundb.get_run(id)
    if run is None:
        raise HTTPException(status_code=404)

    viewer = _viewer_from_request(request=request)

    chi2 = get_chi2(run["tasks"])
    show_task = _show_task(request=request, run=run)

    rendered = render_template(
        lookup=TEMPLATE_LOOKUP,
        template_name="tasks.mak",
        context={
            "run": run,
            "approver": viewer.is_approver,
            "show_task": show_task,
            "chi2": chi2,
        },
    )
    response = HTMLResponse(rendered.html)
    response.headers["Cache-Control"] = "max-age=10"
    return response


@router.get("/tests/view/{id}", response_class=HTMLResponse)
async def tests_view(request: Request, id: str) -> HTMLResponse:  # noqa: A002
    """Render the full run details page."""
    rundb = cast("RunDb", request.app.state.rundb)

    run = rundb.get_run(id)
    if run is None:
        raise HTTPException(status_code=404)

    run_id = str(run["_id"])
    viewer = _viewer_from_request(request=request)

    follow = 1 if "follow" in request.query_params else 0

    active = 0
    cores = 0
    for task in run.get("tasks", []):
        if task.get("active"):
            active += 1
            worker_info = task.get("worker_info", {})
            cores += int(worker_info.get("concurrency", 0))

    chi2 = get_chi2(run["tasks"])
    show_task = _show_task(request=request, run=run)

    allow_github_calls = _allow_github_api_calls(run=run, viewer=viewer)

    run_args = _run_args(run=run)

    notes, warnings = _notes_and_option_warnings(run=run)

    book_warning = _book_warning(rundb=rundb, run=run)
    if book_warning:
        warnings.append(book_warning)

    warnings, use_3dot_diff = _warnings_github(
        run=run,
        viewer=viewer,
        allow_github_calls=allow_github_calls,
        existing_warnings=warnings,
    )

    totals = "({} active worker{} with {} core{})".format(
        active,
        ("s" if active != 1 else ""),
        cores,
        ("s" if cores != 1 else ""),
    )

    spsa_data: object = rundb.spsa_handler.get_spsa_data(run_id)

    page_title = _page_title(run=run)

    context: dict[str, object] = {
        "run": run,
        "run_args": run_args,
        "page_title": page_title,
        "approver": viewer.is_approver,
        "chi2": chi2,
        "totals": totals,
        "tasks_shown": _tasks_shown(request=request, show_task=show_task),
        "show_task": show_task,
        "follow": follow,
        "can_modify_run": _can_modify_run(viewer=viewer, run=run),
        "same_user": _same_user(viewer=viewer, run=run),
        "pt_info": rundb.pt_info,
        "document_size": len(bson.BSON.encode(run)),
        "spsa_data": spsa_data,
        "notes": notes,
        "warnings": warnings,
        "use_3dot_diff": use_3dot_diff,
        "allow_github_api_calls": allow_github_calls,
    }

    return _render_page(
        request=request,
        template_name="tests_view.mak",
        context=context,
        cache_control="no-store",
    )


@router.get("/tests/live_elo/{id}", response_class=HTMLResponse)
async def tests_live_elo(request: Request, id: str) -> HTMLResponse:  # noqa: A002
    """Render the live Elo page for an SPRT run."""
    rundb = cast("RunDb", request.app.state.rundb)

    run = rundb.get_run(id)
    if run is None or "sprt" not in run.get("args", {}):
        raise HTTPException(status_code=404)

    return _render_page(
        request=request,
        template_name="tests_live_elo.mak",
        context={"run": run, "page_title": _page_title(run=run)},
        cache_control="no-store",
    )


@router.get("/tests/stats/{id}", response_class=HTMLResponse)
async def tests_stats(request: Request, id: str) -> HTMLResponse:  # noqa: A002
    """Render the run statistics page."""
    rundb = cast("RunDb", request.app.state.rundb)

    run = rundb.get_run(id)
    if run is None:
        raise HTTPException(status_code=404)

    return _render_page(
        request=request,
        template_name="tests_stats.mak",
        context={"run": run, "page_title": _page_title(run=run)},
        cache_control="no-store",
    )
