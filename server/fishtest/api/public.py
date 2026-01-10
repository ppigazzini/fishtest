"""FastAPI public/user API routes.

Ports selected endpoints from `server/fishtest/api.py`.

Current scope:
- `GET /api/pgn/{id}`
- `GET /api/run_pgns/{id}`
- `GET /api/active_runs`
- `GET /api/finished_runs`
- `POST /api/actions`
- `GET /api/get_run/{id}`
- `GET /api/get_task/{id}/{task_id}`
- `GET /api/get_elo/{id}`
- `GET /api/calc_elo`
- `GET /api/nn/{id}`

These are needed by the run detail UI (`tests_view.mak`).
"""

from __future__ import annotations

import copy
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol, cast

import fishtest.github_api as gh
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fishtest.dependencies import get_rundb
from fishtest.stats.stat_util import SPRT_elo
from fishtest.stats.stat_util import get_elo as stat_get_elo
from fishtest.util import strip_run

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from fishtest.rundb import RunDb

router = APIRouter(tags=["api"], include_in_schema=False)

_RUN_PGNS_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^([a-zA-Z0-9]+)\.pgn\.gz$")
_CHUNK_SIZE: Final[int] = 1024 * 1024
_MAX_SPRT_ABS_ELO: Final[float] = 10.0


class _ByteReader(Protocol):
    def read(self, size: int, /) -> bytes: ...

    def close(self) -> None: ...


def _cors_headers() -> dict[str, str]:
    return {
        "access-control-allow-origin": "*",
        "access-control-allow-headers": "content-type",
    }


def _truthy_query(value: str | None) -> bool:
    # Match Pyramid's behavior: the presence of the query param makes it truthy
    # (even "0" is truthy there).
    return bool(value)


def _json_error(
    *,
    request: Request,
    message: str,
    status_code: int = 400,
) -> JSONResponse:
    api = request.url.path
    return JSONResponse(
        status_code=status_code,
        content={"error": f"{api}: {message}"},
    )


def _external_base_url(request: Request) -> str:
    override = os.environ.get("FISHTEST_NN_URL", "").strip().rstrip("/")
    if override:
        return override

    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    scheme = forwarded or request.url.scheme
    host = request.headers.get("host")
    if not host:
        return str(request.base_url).rstrip("/")
    return f"{scheme}://{host}".rstrip("/")


def _to_str_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return str(value)
    return str(value)


def _calc_fixed_game_elo(results: dict[str, object]) -> dict[str, object]:
    if "pentanomial" in results:
        ptnml = cast("list[int]", results["pentanomial"])
        elo_value, elo95, los = stat_get_elo(ptnml)
        return {
            "elo": elo_value,
            "ci": [elo_value - elo95, elo_value + elo95],
            "LOS": los,
        }

    wins = cast("int", results["wins"])
    draws = cast("int", results["draws"])
    losses = cast("int", results["losses"])
    wld = [wins, losses, draws]
    elo_value, elo95, los = stat_get_elo([wld[1], wld[2], wld[0]])
    return {
        "elo": elo_value,
        "ci": [elo_value - elo95, elo_value + elo95],
        "LOS": los,
    }


def _calc_sprt_elo(
    *,
    results: dict[str, object],
    elo0: float,
    elo1: float,
    elo_model: str,
) -> dict[str, object]:
    alpha = 0.05
    beta = 0.05
    return cast(
        "dict[str, object]",
        SPRT_elo(
            results,
            alpha=alpha,
            beta=beta,
            elo0=elo0,
            elo1=elo1,
            elo_model=elo_model,
        ),
    )


def _redact_worker_info(task: dict[str, object]) -> None:
    worker_info = task.get("worker_info")
    if not isinstance(worker_info, dict):
        return

    worker_info_map = cast("dict[str, object]", worker_info)

    unique_key = worker_info_map.get("unique_key")
    if isinstance(unique_key, str):
        worker_info_map["unique_key"] = unique_key[:8] + "..."
    if "remote_addr" in worker_info_map:
        worker_info_map["remote_addr"] = "?.?.?.?"


def _normalize_task(task: dict[str, object]) -> dict[str, object]:
    _redact_worker_info(task)

    last_updated = task.get("last_updated")
    if last_updated is not None:
        task["last_updated"] = _to_str_datetime(last_updated)

    residual = task.get("residual")
    if residual == float("inf"):
        task["residual"] = "inf"

    spsa_params = task.get("spsa_params")
    if isinstance(spsa_params, dict) and "packed_flips" in spsa_params:
        spsa_params_map = cast("dict[str, object]", spsa_params)
        packed = spsa_params_map.get("packed_flips")
        if isinstance(packed, (bytes, bytearray)):
            spsa_params_map["packed_flips"] = list(packed)

    return task


def _stream_reader(reader: _ByteReader) -> Iterator[bytes]:
    try:
        while True:
            chunk = reader.read(_CHUNK_SIZE)
            if not chunk:
                return
            yield chunk
    finally:
        reader.close()


@router.get("/api/rate_limit")
async def rate_limit() -> dict[str, object]:
    """Return current GitHub rate limit info (matches Pyramid)."""
    return gh.rate_limit()


@router.get("/api/pgn/{id}")
async def download_pgn(
    id: str,  # noqa: A002
    request: Request,
    rundb: "RunDb" = Depends(get_rundb),
) -> Response:
    """Download a gzipped PGN for a single task.

    Path param `id` is treated as a filename (e.g. `<runid>-<taskid>.pgn`).
    """
    zip_name = id
    run_id = zip_name.split(".")[0]  # strip .pgn

    pgn_zip, size = rundb.get_pgn(run_id)
    if pgn_zip is None:
        raise HTTPException(status_code=404)

    headers = {
        "Content-Disposition": f'attachment; filename="{zip_name}"',
        "Content-Encoding": "gzip",
        "Content-Length": str(size),
    }
    return Response(content=pgn_zip, media_type="application/gzip", headers=headers)


@router.get("/api/run_pgns/{id}")
async def download_run_pgns(
    id: str,  # noqa: A002
    request: Request,
    rundb: "RunDb" = Depends(get_rundb),
) -> StreamingResponse:
    """Download a gzipped tar of all PGNs for a run.

    Path param `id` is treated as a filename (e.g. `<runid>.pgn.gz`).
    """
    match = _RUN_PGNS_NAME_RE.match(id)
    if not match:
        raise HTTPException(status_code=400)

    run_id = match.group(1)
    pgns_reader, total_size = rundb.get_run_pgns(run_id)
    if pgns_reader is None:
        raise HTTPException(status_code=404)

    headers = {
        "Content-Disposition": f'attachment; filename="{id}"',
        "Content-Length": str(total_size),
    }
    return StreamingResponse(
        _stream_reader(cast("_ByteReader", pgns_reader)),
        media_type="application/gzip",
        headers=headers,
    )


@router.get("/api/active_runs")
async def active_runs(rundb: "RunDb" = Depends(get_rundb)) -> dict[str, object]:
    """Return all active runs.

    Matches Pyramid: returns a dict mapping run_id -> run document.
    """
    runs = rundb.runs.find(
        {"finished": False},
        {"tasks": 0, "bad_tasks": 0, "args.spsa.param_history": 0},
    )
    active: dict[str, object] = {}
    for run in runs:
        for key in ("_id", "start_time", "last_updated"):
            run[key] = str(run[key])
        active[str(run["_id"])] = run
    return active


@router.get("/api/finished_runs")
async def finished_runs(
    request: Request,
    rundb: "RunDb" = Depends(get_rundb),
) -> Response:
    """Return a page of finished runs.

    Requires `?page=` like Pyramid.
    """
    qp = request.query_params
    username = qp.get("username", "")
    success_only = _truthy_query(qp.get("success_only"))
    yellow_only = _truthy_query(qp.get("yellow_only"))
    ltc_only = _truthy_query(qp.get("ltc_only"))
    timestamp = qp.get("timestamp", "")
    page_param = qp.get("page", "")

    if page_param == "":
        return _json_error(request=request, message="Please provide a Page number.")
    if not page_param.isdigit() or int(page_param) < 1:
        return _json_error(
            request=request,
            message="Please provide a valid Page number.",
        )
    page_idx = int(page_param) - 1
    page_size = 50

    last_updated: datetime | None
    if timestamp != "" and re.match(r"^\d{10}(\.\d+)?$", timestamp):
        last_updated = datetime.fromtimestamp(float(timestamp), tz=UTC).replace(
            tzinfo=None,
        )
    elif timestamp != "":
        return _json_error(
            request=request,
            message="Please provide a valid UNIX timestamp.",
        )
    else:
        last_updated = None

    runs, _num_finished = rundb.get_finished_runs(
        username=username,
        success_only=success_only,
        yellow_only=yellow_only,
        ltc_only=ltc_only,
        skip=page_idx * page_size,
        limit=page_size,
        last_updated=last_updated,
    )

    finished: dict[str, object] = {}
    for run in runs:
        for key in ("_id", "start_time", "last_updated"):
            run[key] = str(run[key])
        finished[str(run["_id"])] = run
    return JSONResponse(content=finished)


@router.post("/api/actions")
async def actions(
    request: Request,
    rundb: "RunDb" = Depends(get_rundb),
) -> Response:
    """Query recent actions.

    Matches Pyramid: accepts a JSON query body and returns up to 200 results.
    """
    try:
        query_obj = await request.json()
    except ValueError:
        query_obj = None

    if not isinstance(query_obj, dict):
        return JSONResponse(content=[], headers=_cors_headers())

    try:
        cursor = rundb.db["actions"].find(query_obj).limit(200)
        actions_iter = list(cursor)
    except Exception:  # noqa: BLE001
        actions_iter = []

    ret: list[dict[str, object]] = []
    for action in actions_iter:
        action["_id"] = str(action["_id"])
        ret.append(action)

    return JSONResponse(content=ret, headers=_cors_headers())


@router.get("/api/get_run/{id}")
async def get_run(
    id: str,  # noqa: A002
    request: Request,
    rundb: "RunDb" = Depends(get_rundb),
) -> Response:
    """Return a single run document (stripped for JSON)."""
    run = rundb.get_run(id)
    if run is None:
        return _json_error(
            request=request,
            message=f"The run {id} does not exist",
            status_code=404,
        )
    return JSONResponse(content=strip_run(run), headers=_cors_headers())


@router.get("/api/get_task/{id}/{task_id}")
async def get_task(
    id: str,  # noqa: A002
    task_id: str,
    request: Request,
    rundb: "RunDb" = Depends(get_rundb),
) -> Response:
    """Return a specific task from a run."""
    run = rundb.get_run(id)
    if run is None:
        return _json_error(
            request=request,
            message=f"The task {id}/{task_id} does not exist",
            status_code=404,
        )

    try:
        if task_id.endswith("bad"):
            idx = int(task_id[:-3])
            task = copy.deepcopy(run["bad_tasks"][idx])
        else:
            idx = int(task_id)
            task = copy.deepcopy(run["tasks"][idx])
    except Exception:  # noqa: BLE001
        return _json_error(
            request=request,
            message=f"The task {id}/{task_id} does not exist",
            status_code=404,
        )

    normalized = _normalize_task(cast("dict[str, object]", task))
    return JSONResponse(content=normalized)


@router.get("/api/get_elo/{id}")
async def get_elo(
    id: str,  # noqa: A002
    rundb: "RunDb" = Depends(get_rundb),
) -> dict[str, object]:
    """Return run data augmented with computed SPRT elo (only for SPRT runs)."""
    run = rundb.get_run(id)
    if run is None:
        raise HTTPException(status_code=404)

    results = run["results"]
    if "sprt" not in run["args"]:
        return {}

    run_stripped = strip_run(run)
    sprt = cast("dict[str, object]", run_stripped["args"].get("sprt"))
    elo_model = cast("str", sprt.get("elo_model", "BayesElo"))
    alpha = cast("float", sprt["alpha"])
    beta = cast("float", sprt["beta"])
    elo0 = cast("float", sprt["elo0"])
    elo1 = cast("float", sprt["elo1"])
    sprt["elo_model"] = elo_model

    run_stripped["elo"] = SPRT_elo(
        results,
        alpha=alpha,
        beta=beta,
        elo0=elo0,
        elo1=elo1,
        elo_model=elo_model,
    )
    return cast("dict[str, object]", run_stripped)


def _parse_calc_elo_results(qp: Mapping[str, str]) -> dict[str, object] | None:
    def parse_nonneg_int(raw: str | None) -> int | None:
        if raw is None or raw == "":
            return None
        if not raw.replace("-", "").replace(".", "").isdigit():
            return None
        value = int(float(raw))
        return value if value >= 0 else None

    ll_i = parse_nonneg_int(qp.get("LL"))
    ld_i = parse_nonneg_int(qp.get("LD"))
    ddwl_i = parse_nonneg_int(qp.get("DDWL"))
    wd_i = parse_nonneg_int(qp.get("WD"))
    ww_i = parse_nonneg_int(qp.get("WW"))
    if None not in (ll_i, ld_i, ddwl_i, wd_i, ww_i):
        return {
            "pentanomial": [
                cast("int", ll_i),
                cast("int", ld_i),
                cast("int", ddwl_i),
                cast("int", wd_i),
                cast("int", ww_i),
            ],
        }

    wins_i = parse_nonneg_int(qp.get("W"))
    draws_i = parse_nonneg_int(qp.get("D"))
    losses_i = parse_nonneg_int(qp.get("L"))
    if None not in (wins_i, draws_i, losses_i):
        return {
            "wins": cast("int", wins_i),
            "draws": cast("int", draws_i),
            "losses": cast("int", losses_i),
        }

    return None


def _calc_total_games(results: dict[str, object]) -> int:
    if "pentanomial" in results:
        ptnml = cast("list[int]", results["pentanomial"])
        return sum(ptnml) * 2
    return (
        cast("int", results["wins"])
        + cast("int", results["draws"])
        + cast("int", results["losses"])
    )


def _parse_sprt_params(qp: Mapping[str, str]) -> tuple[float, float, str] | None:
    elo0_raw = qp.get("elo0", "")
    elo1_raw = qp.get("elo1", "")
    if elo0_raw == "" or elo1_raw == "":
        return None

    elo0_f = float(elo0_raw)
    elo1_f = float(elo1_raw)
    elo_model = qp.get("elo_model", "normalized")
    return elo0_f, elo1_f, elo_model


def _calc_sprt_elo_response(
    *,
    request: Request,
    results: dict[str, object],
    elo0: float,
    elo1: float,
    elo_model: str,
) -> Response:
    invalid_elo = (
        elo1 < elo0 + 0.5
        or abs(elo0) > _MAX_SPRT_ABS_ELO
        or abs(elo1) > _MAX_SPRT_ABS_ELO
    )
    if invalid_elo:
        return _json_error(request=request, message="Bad elo0, and elo1 values.")

    valid_models = {"BayesElo", "logistic", "normalized"}
    if elo_model not in valid_models:
        return _json_error(
            request=request,
            message=("Valid Elo models are: BayesElo, logistic, and normalized."),
        )

    return JSONResponse(
        content=_calc_sprt_elo(
            results=results,
            elo0=elo0,
            elo1=elo1,
            elo_model=elo_model,
        ),
    )


def _calc_elo_from_results(
    *,
    request: Request,
    qp: Mapping[str, str],
    results: dict[str, object],
) -> Response:
    total_games = _calc_total_games(results)
    if total_games > 2**32:
        return _json_error(
            request=request,
            message="Number of games exceeds the limit.",
        )
    if total_games == 0:
        return _json_error(request=request, message="No games to calculate Elo.")

    try:
        sprt_params = _parse_sprt_params(qp)
    except ValueError:
        return _json_error(request=request, message="Bad elo0, and elo1 values.")

    if sprt_params is None:
        return JSONResponse(content=_calc_fixed_game_elo(results))

    elo0, elo1, elo_model = sprt_params
    return _calc_sprt_elo_response(
        request=request,
        results=results,
        elo0=elo0,
        elo1=elo1,
        elo_model=elo_model,
    )


@router.get("/api/calc_elo")
async def calc_elo(request: Request) -> Response:
    """Compute Elo/CI/LOS for either WDL or pentanomial results.

    Matches the Pyramid endpoint signature.
    """
    qp = request.query_params
    results = _parse_calc_elo_results(qp)
    if results is None:
        return _json_error(
            request=request,
            message=(
                "Invalid or missing parameters. "
                "Please provide all values as valid numbers."
            ),
        )

    return _calc_elo_from_results(request=request, qp=qp, results=results)


@router.get("/api/nn/{id}")
async def download_nn(
    id: str,  # noqa: A002
    request: Request,
    rundb: "RunDb" = Depends(get_rundb),
) -> RedirectResponse:
    """Redirect to the static NN download endpoint after incrementing downloads."""
    nn = rundb.get_nn(id)
    if nn is None:
        raise HTTPException(status_code=404)

    rundb.increment_nn_downloads(id)
    nn_base_url = _external_base_url(request)
    return RedirectResponse(url=f"{nn_base_url}/nn/{id}", status_code=303)
