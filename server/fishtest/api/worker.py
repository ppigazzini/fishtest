"""Worker API endpoints (Phase 1).

These endpoints are protocol-critical and must preserve Pyramid error semantics:
- Always respond with a JSON dictionary.
- Include a `duration` field (seconds) for success and error responses.
- Error payloads include the request path prefix, e.g. `/api/update_task: ...`.

This module intentionally reuses the existing schema validation and userdb/rundb
logic, so behavior stays aligned during the Pyramid→FastAPI strangler phase.
"""

from __future__ import annotations

import base64
import binascii
import copy
import functools
import time
from datetime import UTC, datetime
from typing import Any, Final, TypedDict, cast

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fishtest.schemas import api_access_schema, api_schema, gzip_data
from fishtest.util import worker_name
from fishtest.versions import WORKER_VERSION
from vtjson import ValidationError, validate


class _ErrorResponse(TypedDict):
    error: str
    duration: float


STOP_RUN_MIN_CPU_HOURS = 1000


def _duration_seconds(t0: float) -> float:
    return time.monotonic() - t0


def _api_error(*, path: str, message: str, t0: float) -> _ErrorResponse:
    # Pyramid prefixes errors with the API path.
    return {
        "error": f"{path}: {message}",
        "duration": _duration_seconds(t0),
    }


def _country_code(request: Request) -> str:
    value = request.headers.get("X-Country-Code")
    return "?" if value in (None, "ZZ") else value


def _client_ip(request: Request) -> str:
    client = request.client
    return "?" if client is None else client.host


def _host_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


async def _json_body_or_response(
    request: Request,
    *,
    t0: float,
) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.json()
    except (TypeError, ValueError):
        payload = _api_error(
            path=request.url.path,
            message="request is not json encoded",
            t0=t0,
        )
        return JSONResponse(payload, status_code=400)

    if not isinstance(body, dict):
        payload = _api_error(
            path=request.url.path,
            message="request is not json encoded",
            t0=t0,
        )
        return JSONResponse(payload, status_code=400)

    return body


def _json_body_or_response_sync(
    request: Request,
    *,
    t0: float,
) -> dict[str, Any] | JSONResponse:
    return anyio.from_thread.run(
        functools.partial(_json_body_or_response, t0=t0),
        request,
    )


def _validate_username_password(
    *,
    request: Request,
    body: dict[str, Any],
    t0: float,
) -> JSONResponse | None:
    try:
        validate(api_access_schema, body, "request")
    except ValidationError as exc:
        payload = _api_error(path=request.url.path, message=str(exc), t0=t0)
        return JSONResponse(payload, status_code=400)

    username = body["worker_info"]["username"]
    password = body["password"]

    token = request.app.state.userdb.authenticate(username, password)
    if isinstance(token, dict) and "error" in token:
        payload = _api_error(path=request.url.path, message=token["error"], t0=t0)
        return JSONResponse(payload, status_code=401)

    return None


def _validate_request(
    *,
    request: Request,
    body: dict[str, Any],
    t0: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, JSONResponse | None]:
    """Validate a worker request, returning (run, task, error_response).

    On success, `run` may be None when no run_id is supplied.
    `task` is only non-None when task_id is supplied.
    """
    error = _validate_username_password(request=request, body=body, t0=t0)
    if error is not None:
        return None, None, error

    try:
        validate(api_schema, body, "request")
    except ValidationError as exc:
        payload = _api_error(path=request.url.path, message=str(exc), t0=t0)
        return None, None, JSONResponse(payload, status_code=400)

    rundb = request.app.state.rundb

    run: dict[str, Any] | None = None
    task: dict[str, Any] | None = None

    if "run_id" in body:
        run_id = body["run_id"]
        run = rundb.get_run(run_id)
        if run is None:
            payload = _api_error(
                path=request.url.path,
                message=f"Invalid run_id: {run_id}",
                t0=t0,
            )
            return None, None, JSONResponse(payload, status_code=400)

    if "task_id" in body:
        # api_schema guarantees run_id if task_id is present.
        run_id = body["run_id"]
        task_id = body["task_id"]

        run = cast("dict[str, Any]", run)

        tasks = run["tasks"]
        if task_id < 0 or task_id >= len(tasks):
            payload = _api_error(
                path=request.url.path,
                message=f"Invalid task_id {task_id} for run_id {run_id}",
                t0=t0,
            )
            return None, None, JSONResponse(payload, status_code=400)

        task = tasks[task_id]
        for key in ("unique_key", "username"):
            value_request = body["worker_info"][key]
            value_task = task["worker_info"][key]
            if value_request != value_task:
                payload = _api_error(
                    path=request.url.path,
                    message=(
                        f"Invalid {key} for task {run_id}/{task_id}. "
                        f"From task: {value_task}. "
                        f"From request: {value_request}."
                    ),
                    t0=t0,
                )
                return None, None, JSONResponse(payload, status_code=400)

    return run, task, None


def _require_primary_instance(*, request: Request, t0: float) -> JSONResponse | None:
    rundb = request.app.state.rundb
    if hasattr(rundb, "is_primary_instance") and not rundb.is_primary_instance():
        payload = _api_error(
            path=request.url.path,
            message="This endpoint must be served by the primary instance",
            t0=t0,
        )
        return JSONResponse(payload, status_code=503)
    return None


def _worker_info_for_rundb(
    *,
    request: Request,
    body: dict[str, Any],
    task: dict[str, Any] | None,
) -> dict[str, Any]:
    worker_info = dict(body["worker_info"])

    if task is None:
        worker_info["remote_addr"] = _client_ip(request)
    else:
        worker_info["remote_addr"] = task["worker_info"]["remote_addr"]

    worker_info["country_code"] = _country_code(request)
    return worker_info


router = APIRouter(tags=["worker"], include_in_schema=False)


@router.post("/api/request_version")
def request_version(request: Request) -> JSONResponse:
    """Return the worker protocol version."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    error = _validate_username_password(request=request, body=body, t0=t0)
    if error is not None:
        return error

    return JSONResponse({"version": WORKER_VERSION, "duration": _duration_seconds(t0)})


@router.post("/api/update_task")
def update_task(request: Request) -> JSONResponse:
    """Update a task's progress and stats."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    primary_error = _require_primary_instance(request=request, t0=t0)
    if primary_error is not None:
        return primary_error

    _run, task, error = _validate_request(request=request, body=body, t0=t0)
    if error is not None:
        return error

    rundb = request.app.state.rundb

    run_id: Final[str] = body["run_id"]
    task_id: Final[int] = body["task_id"]
    stats: dict[str, Any] = body.get("stats", {})
    spsa_results: dict[str, Any] = body.get("spsa", {})

    worker_info = _worker_info_for_rundb(request=request, body=body, task=task)

    result = rundb.update_task(
        worker_info=worker_info,
        run_id=run_id,
        task_id=task_id,
        stats=stats,
        spsa_results=spsa_results,
    )

    if not isinstance(result, dict):
        # Defensive: workers expect a JSON dictionary.
        result = {"info": result}

    # Pyramid's worker API always returned HTTP 200 for application-level
    # errors (encoded as an "error" field in the JSON payload). Preserve that
    # behavior for protocol compatibility and existing tests.
    result["duration"] = _duration_seconds(t0)
    return JSONResponse(result, status_code=200)


@router.post("/api/request_task")
def request_task(request: Request) -> JSONResponse:
    """Request a new task assignment for a worker."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    primary_error = _require_primary_instance(request=request, t0=t0)
    if primary_error is not None:
        return primary_error

    _run, task, error = _validate_request(request=request, body=body, t0=t0)
    if error is not None:
        return error

    worker_info = _worker_info_for_rundb(request=request, body=body, task=task)
    worker_info["host_url"] = _host_url(request)

    rundb = request.app.state.rundb
    result = rundb.request_task(worker_info)
    if not isinstance(result, dict):
        result = {"info": result}

    if "task_waiting" in result:
        result["duration"] = _duration_seconds(t0)
        return JSONResponse(result)

    run_obj = result.get("run")
    task_id = result.get("task_id")
    if run_obj is None or task_id is None:
        # Defensive: if rundb ever returns an unexpected shape.
        result["duration"] = _duration_seconds(t0)
        return JSONResponse(result)

    task_obj = run_obj["tasks"][task_id]
    min_task = {"num_games": task_obj["num_games"], "start": task_obj["start"]}
    if "stats" in task_obj:
        min_task["stats"] = task_obj["stats"]

    args = copy.copy(run_obj["args"])
    book = args.get("book")
    books = getattr(rundb, "books", {})
    if book in books:
        args["book_sri"] = books[book]["sri"]

    min_run = {"_id": str(run_obj["_id"]), "args": args, "my_task": min_task}
    result["run"] = min_run
    result["duration"] = _duration_seconds(t0)
    return JSONResponse(result)


@router.post("/api/beat")
def beat(request: Request) -> JSONResponse:
    """Heartbeat endpoint to keep tasks alive."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    primary_error = _require_primary_instance(request=request, t0=t0)
    if primary_error is not None:
        return primary_error

    run, task, error = _validate_request(request=request, body=body, t0=t0)
    if error is not None:
        return error

    if run is None or task is None:
        payload = _api_error(
            path=request.url.path,
            message="Missing run_id/task_id",
            t0=t0,
        )
        return JSONResponse(payload, status_code=400)

    rundb = request.app.state.rundb
    run_id: str = body["run_id"]
    with rundb.active_run_lock(run_id):
        if task.get("active"):
            task["last_updated"] = datetime.now(UTC)
            # On the primary instance this updates the cache/buffers.
            if hasattr(rundb, "buffer"):
                rundb.buffer(run)
        response: dict[str, Any] = {"task_alive": bool(task.get("active", True))}
        response["duration"] = _duration_seconds(t0)
        return JSONResponse(response)


@router.post("/api/request_spsa")
def request_spsa(request: Request) -> JSONResponse:
    """Request SPSA parameters/data for a task."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    primary_error = _require_primary_instance(request=request, t0=t0)
    if primary_error is not None:
        return primary_error

    run, task, error = _validate_request(request=request, body=body, t0=t0)
    if error is not None:
        return error

    if run is None or task is None:
        payload = _api_error(
            path=request.url.path,
            message="Missing run_id/task_id",
            t0=t0,
        )
        return JSONResponse(payload, status_code=400)

    rundb = request.app.state.rundb
    result = rundb.spsa_handler.request_spsa_data(body["run_id"], body["task_id"])
    if not isinstance(result, dict):
        result = {"info": result}
    result["duration"] = _duration_seconds(t0)
    return JSONResponse(result)


@router.post("/api/failed_task")
def failed_task(request: Request) -> JSONResponse:
    """Report that a task failed."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    primary_error = _require_primary_instance(request=request, t0=t0)
    if primary_error is not None:
        return primary_error

    _run, _task, error = _validate_request(request=request, body=body, t0=t0)
    if error is not None:
        return error

    rundb = request.app.state.rundb
    result = rundb.failed_task(body["run_id"], body["task_id"], body.get("message", ""))
    if not isinstance(result, dict):
        result = {"info": result}
    result["duration"] = _duration_seconds(t0)
    return JSONResponse(result)


@router.post("/api/stop_run")
def stop_run(request: Request) -> JSONResponse:
    """Allow certain workers to stop a run (primary instance only)."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    primary_error = _require_primary_instance(request=request, t0=t0)
    if primary_error is not None:
        return primary_error

    run, task, error = _validate_request(request=request, body=body, t0=t0)
    if error is not None:
        return error

    if run is None or task is None:
        payload = _api_error(
            path=request.url.path,
            message="Missing run_id/task_id",
            t0=t0,
        )
        return JSONResponse(payload, status_code=400)

    rundb = request.app.state.rundb
    userdb = request.app.state.userdb
    actiondb = request.app.state.actiondb

    username = body["worker_info"]["username"]
    user = userdb.user_cache.find_one({"username": username})
    cpu_hours = -1 if user is None else user.get("cpu_hours", -1)

    error_message = ""
    if cpu_hours < STOP_RUN_MIN_CPU_HOURS:
        error_message = f"User {username} has too few games to stop a run"

    run_id: str = body["run_id"]
    task_id: int = body["task_id"]
    message: str = body.get("message", "")

    with rundb.active_run_lock(run_id):
        if not run.get("finished", False):
            full_message = message
            if error_message:
                full_message = full_message + " (not authorized)"
            actiondb.stop_run(
                username=username,
                run=run,
                task_id=task_id,
                message=full_message,
            )
            if not error_message:
                run["failed"] = True
                run["failures"] = run.get("failures", 0) + 1
                rundb.stop_run(run_id)
            else:
                rundb.set_inactive_task(task_id, run)
        else:
            error_message = f"Run {run_id} is already finished"

    if error_message:
        payload = _api_error(path=request.url.path, message=error_message, t0=t0)
        return JSONResponse(payload, status_code=401)

    return JSONResponse({"duration": _duration_seconds(t0)})


@router.post("/api/worker_log")
def worker_log(request: Request) -> JSONResponse:
    """Submit a worker log message."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    error = _validate_username_password(request=request, body=body, t0=t0)
    if error is not None:
        return error

    # Validate the broader schema too (mirrors Pyramid).
    try:
        validate(api_schema, body, "request")
    except ValidationError as exc:
        payload = _api_error(path=request.url.path, message=str(exc), t0=t0)
        return JSONResponse(payload, status_code=400)

    task = None
    worker_info = _worker_info_for_rundb(request=request, body=body, task=task)
    message: str = body.get("message", "")
    username: str = body["worker_info"]["username"]

    request.app.state.actiondb.log_message(
        username=username,
        message=message,
        worker=worker_name(worker_info),
    )
    return JSONResponse({"duration": _duration_seconds(t0)})


@router.post("/api/upload_pgn")
def upload_pgn(request: Request) -> JSONResponse:
    """Upload a base64-encoded gzip PGN payload for a task."""
    t0 = time.monotonic()
    body_or_response = _json_body_or_response_sync(request, t0=t0)
    if isinstance(body_or_response, JSONResponse):
        return body_or_response
    body = body_or_response

    primary_error = _require_primary_instance(request=request, t0=t0)
    if primary_error is not None:
        return primary_error

    _run, _task, error = _validate_request(request=request, body=body, t0=t0)
    if error is not None:
        return error

    try:
        pgn_zip = base64.b64decode(body["pgn"])
        validate(gzip_data, pgn_zip, "pgn")
    except (ValidationError, ValueError, TypeError, binascii.Error) as exc:
        payload = _api_error(path=request.url.path, message=str(exc), t0=t0)
        return JSONResponse(payload, status_code=400)

    rundb = request.app.state.rundb
    run_id: str = body["run_id"]
    task_id: int = body["task_id"]
    result = rundb.upload_pgn(run_id=f"{run_id}-{task_id}", pgn_zip=pgn_zip)
    if not isinstance(result, dict):
        result = {"info": result}
    result["duration"] = _duration_seconds(t0)
    return JSONResponse(result)
