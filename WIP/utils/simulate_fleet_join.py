#!/usr/bin/env python3
# ruff: noqa: D101,D103,TRY003,EM101,EM102,FBT001,S324,PLR2004,PLR0913,C901,PLR0912,T201,ANN401,BLE001,PLR0915,TC003
"""Concurrent worker-join simulator for Fishtest-compatible APIs.

This tool reproduces the initial worker handshake used by the worker:
1) POST /api/request_version
2) POST /api/request_task

Use only against infrastructure you own or are explicitly authorized to test.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

WORKER_VERSION = 311
MAX_WORKERS = 1_000_000
MAX_CONCURRENCY = 100_000
REQUEST_OK_STATUS = 200

requests = cast("Any", importlib.import_module("requests"))


@dataclass
class StepResult:
    endpoint: str
    ok: bool
    status_code: int | None
    latency_ms: float
    error: str | None
    body: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize a step result for JSON reports."""
        return {
            "endpoint": self.endpoint,
            "ok": self.ok,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "body": self.body,
        }


@dataclass
class WorkerRunResult:
    worker_index: int
    unique_key: str
    steps: list[StepResult]


@dataclass(frozen=True)
class Config:
    base_url: str
    username: str
    password: str
    workers: int
    concurrency: int
    mode: str
    timeout_seconds: float
    ramp_seconds: float
    verbose_errors: bool
    allow_external: bool
    json_report: Path | None
    json_mode: str


@dataclass
class Summary:
    exit_code: int
    interrupted: bool
    workers_planned: int
    workers_completed: int
    requests_sent: int
    successes: int
    failures: int
    wall_time_s: float
    throughput_req_s: float
    latency_ms: dict[str, float]
    per_endpoint: dict[str, dict[str, float | int]]
    task_waiting: int
    top_errors: list[dict[str, str | int]]


def positive_int(name: str) -> Callable[[str], int]:
    def _parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if parsed <= 0:
            raise argparse.ArgumentTypeError(f"{name} must be > 0")
        return parsed

    return _parse


def non_negative_float(name: str) -> Callable[[str], float]:
    def _parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
        if parsed < 0:
            raise argparse.ArgumentTypeError(f"{name} must be >= 0")
        return parsed

    return _parse


def positive_float(name: str) -> Callable[[str], float]:
    def _parse(value: str) -> float:
        parsed = non_negative_float(name)(value)
        if parsed <= 0:
            raise argparse.ArgumentTypeError(f"{name} must be > 0")
        return parsed

    return _parse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate many workers concurrently joining a Fishtest server.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Target server base URL (default: %(default)s)",
    )
    parser.add_argument("--username", required=True, help="Worker account username")
    parser.add_argument("--password", required=True, help="Worker account password")
    parser.add_argument(
        "--workers",
        type=positive_int("--workers"),
        default=200,
        help="Number of unique virtual workers to simulate (default: %(default)s)",
    )
    parser.add_argument(
        "--concurrency",
        type=positive_int("--concurrency"),
        default=100,
        help="Maximum parallel worker handshakes (default: %(default)s)",
    )
    parser.add_argument(
        "--mode",
        choices=("version", "task", "both"),
        default="both",
        help="Which API steps to run per worker (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=positive_float("--timeout-seconds"),
        default=10.0,
        help="Per-request HTTP timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--ramp-seconds",
        type=non_negative_float("--ramp-seconds"),
        default=0.0,
        help="Spread worker starts over this duration (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose-errors",
        action="store_true",
        help="Print every failed request with worker id and endpoint",
    )
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="Allow non-local targets (required for anything not localhost/127.0.0.1)",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="Write a JSON report to this path",
    )
    parser.add_argument(
        "--json-mode",
        choices=("full", "compact"),
        default="full",
        help="JSON report detail level (default: %(default)s)",
    )
    return parser.parse_args()


def normalize_base_url(raw_base_url: str) -> str:
    normalized = raw_base_url.strip().rstrip("/")
    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"}:
        raise SystemExit("--base-url must start with http:// or https://")
    if not parsed.hostname:
        raise SystemExit("--base-url must include a hostname")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise SystemExit(
            "--base-url must be an origin only, for example https://host:port"
        )

    return normalized


def build_config(args: argparse.Namespace) -> Config:
    username = args.username.strip()
    password = args.password
    if not username:
        raise SystemExit("--username must not be empty")
    if not password:
        raise SystemExit("--password must not be empty")

    if args.workers > MAX_WORKERS:
        raise SystemExit(f"--workers must be <= {MAX_WORKERS}")
    if args.concurrency > MAX_CONCURRENCY:
        raise SystemExit(f"--concurrency must be <= {MAX_CONCURRENCY}")

    concurrency = min(args.concurrency, args.workers)
    base_url = normalize_base_url(args.base_url)

    return Config(
        base_url=base_url,
        username=username,
        password=password,
        workers=args.workers,
        concurrency=concurrency,
        mode=args.mode,
        timeout_seconds=args.timeout_seconds,
        ramp_seconds=args.ramp_seconds,
        verbose_errors=args.verbose_errors,
        allow_external=args.allow_external,
        json_report=args.json_report,
        json_mode=args.json_mode,
    )


def ensure_safe_target(base_url: str, allow_external: bool) -> None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return

    if not allow_external:
        raise SystemExit(
            "Refusing non-local target without --allow-external. "
            "Use this only on systems you are authorized to load test.",
        )


def make_unique_key(worker_index: int) -> str:
    # Must match server schema: [0-9a-zA-Z]{2,8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}
    prefix = f"fleet{worker_index % 1000:03d}"[:8]
    digest = hashlib.sha1(f"{worker_index}".encode("ascii")).hexdigest()
    return f"{prefix}-{digest[0:4]}-{digest[4:8]}-{digest[8:12]}-{digest[12:24]}"


def make_worker_info(username: str, worker_index: int) -> dict[str, Any]:
    py = sys.version_info
    uname = platform.uname()
    arch_bits, arch_linkage = platform.architecture()
    return {
        "uname": f"{uname.system} {uname.release}",
        "architecture": [arch_bits, arch_linkage],
        "concurrency": 1,
        "max_memory": 4096,
        "min_threads": 1,
        "username": username,
        "version": WORKER_VERSION,
        "python_version": [py.major, py.minor, py.micro],
        "gcc_version": [12, 2, 0],
        "compiler": "g++",
        "unique_key": make_unique_key(worker_index),
        "modified": False,
        "worker_arch": "x86-64-avx512",
        "ARCH": "?",
        "nps": 0.0,
        "near_github_api_limit": False,
    }


def post_json(
    session: Any,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> StepResult:
    t0 = time.perf_counter()
    try:
        response = session.post(
            url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        status_code = response.status_code
        body: dict[str, Any] | None
        try:
            body = response.json()
            if not isinstance(body, dict):
                body = {"non_dict_json": True}
        except ValueError:
            body = None

        is_ok = status_code == REQUEST_OK_STATUS and not (body and "error" in body)
        err = None
        if not is_ok:
            if body and "error" in body:
                err = str(body["error"])
            else:
                err = f"HTTP {status_code}"

        return StepResult(
            endpoint=url,
            ok=is_ok,
            status_code=status_code,
            latency_ms=latency_ms,
            error=err,
            body=body,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return StepResult(
            endpoint=url,
            ok=False,
            status_code=None,
            latency_ms=latency_ms,
            error=str(exc),
            body=None,
        )


def simulate_one_worker(
    *,
    worker_index: int,
    base_url: str,
    username: str,
    password: str,
    mode: str,
    timeout_seconds: float,
    ramp_delay: float,
    stop_event: threading.Event,
) -> WorkerRunResult:
    if stop_event.is_set():
        return WorkerRunResult(worker_index=worker_index, unique_key="", steps=[])
    if ramp_delay > 0 and stop_event.wait(ramp_delay):
        return WorkerRunResult(worker_index=worker_index, unique_key="", steps=[])

    info = make_worker_info(username, worker_index)
    payload = {"worker_info": info, "password": password}
    steps: list[StepResult] = []

    with requests.Session() as session:
        if not stop_event.is_set() and mode in {"version", "both"}:
            steps.append(
                post_json(
                    session,
                    f"{base_url}/api/request_version",
                    payload,
                    timeout_seconds,
                ),
            )

        if not stop_event.is_set() and mode in {"task", "both"}:
            steps.append(
                post_json(
                    session,
                    f"{base_url}/api/request_task",
                    payload,
                    timeout_seconds,
                ),
            )

    return WorkerRunResult(
        worker_index=worker_index,
        unique_key=info["unique_key"],
        steps=steps,
    )


def summarize(
    results: list[WorkerRunResult],
    started_at: float,
    verbose_errors: bool,
    workers_planned: int,
    interrupted: bool,
) -> Summary:
    all_steps = [step for result in results for step in result.steps]
    total = len(all_steps)
    ok = sum(1 for step in all_steps if step.ok)
    fail = total - ok
    duration = time.perf_counter() - started_at

    latencies = [step.latency_ms for step in all_steps]
    p50 = percentile(latencies, 50.0)
    p95 = percentile(latencies, 95.0)
    p99 = percentile(latencies, 99.0)

    by_endpoint: dict[str, list[StepResult]] = {}
    for step in all_steps:
        by_endpoint.setdefault(step.endpoint.split("/api/")[-1], []).append(step)

    print("\n=== Fleet Join Simulation Summary ===")
    print(f"workers planned   : {workers_planned}")
    print(f"workers completed : {len(results)}")
    print(f"interrupted       : {interrupted}")
    print(f"requests sent     : {total}")
    print(f"successes         : {ok}")
    print(f"failures          : {fail}")
    print(f"wall time (s)     : {duration:.2f}")
    if duration > 0:
        print(f"throughput req/s  : {total / duration:.2f}")
    print(f"latency p50 (ms)  : {p50:.2f}")
    print(f"latency p95 (ms)  : {p95:.2f}")
    print(f"latency p99 (ms)  : {p99:.2f}")

    print("\nPer endpoint:")
    for endpoint, steps in sorted(by_endpoint.items()):
        endpoint_ok = sum(1 for step in steps if step.ok)
        endpoint_fail = len(steps) - endpoint_ok
        e_p95 = percentile([step.latency_ms for step in steps], 95.0)
        print(
            f"  {endpoint:16s} total={len(steps):5d} "
            f"ok={endpoint_ok:5d} fail={endpoint_fail:5d} p95={e_p95:8.2f} ms",
        )

    task_waiting = 0
    app_errors: dict[str, int] = {}
    for result in results:
        for step in result.steps:
            if step.body and step.body.get("task_waiting") is True:
                task_waiting += 1
            if step.error:
                app_errors[step.error] = app_errors.get(step.error, 0) + 1

    if task_waiting:
        print(f"\nrequest_task responses with task_waiting=true: {task_waiting}")

    if app_errors:
        print("\nTop errors:")
        top = sorted(app_errors.items(), key=lambda kv: kv[1], reverse=True)[:10]
        for message, count in top:
            print(f"  {count:5d}  {message}")

    if verbose_errors and fail:
        print("\nDetailed failures:")
        for result in results:
            for step in result.steps:
                if not step.ok:
                    print(
                        f"  worker={result.worker_index:6d} "
                        f"key={result.unique_key} endpoint={step.endpoint} "
                        f"status={step.status_code} error={step.error}",
                    )

    top = sorted(app_errors.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return Summary(
        exit_code=130 if interrupted else (0 if fail == 0 else 2),
        interrupted=interrupted,
        workers_planned=workers_planned,
        workers_completed=len(results),
        requests_sent=total,
        successes=ok,
        failures=fail,
        wall_time_s=duration,
        throughput_req_s=(total / duration if duration > 0 else 0.0),
        latency_ms={"p50": p50, "p95": p95, "p99": p99},
        per_endpoint={
            endpoint: {
                "total": len(steps),
                "ok": sum(1 for step in steps if step.ok),
                "fail": len(steps) - sum(1 for step in steps if step.ok),
                "p95": percentile([step.latency_ms for step in steps], 95.0),
            }
            for endpoint, steps in sorted(by_endpoint.items())
        },
        task_waiting=task_waiting,
        top_errors=[{"count": count, "message": message} for message, count in top],
    )


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return min(values)
    if pct >= 100:
        return max(values)
    values_sorted = sorted(values)
    rank = (len(values_sorted) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(values_sorted) - 1)
    frac = rank - low
    return values_sorted[low] * (1.0 - frac) + values_sorted[high] * frac


def write_json_report(
    config: Config,
    summary: Summary,
    results: list[WorkerRunResult],
    started_at_epoch: float,
) -> None:
    if config.json_report is None:
        return

    config.json_report.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "report_version": 1,
        "json_mode": config.json_mode,
        "target": config.base_url,
        "started_at_epoch": started_at_epoch,
        "mode": config.mode,
        "workers": config.workers,
        "concurrency": config.concurrency,
        "timeout_seconds": config.timeout_seconds,
        "ramp_seconds": config.ramp_seconds,
        "summary": {
            "exit_code": summary.exit_code,
            "interrupted": summary.interrupted,
            "workers_planned": summary.workers_planned,
            "workers_completed": summary.workers_completed,
            "requests_sent": summary.requests_sent,
            "successes": summary.successes,
            "failures": summary.failures,
            "wall_time_s": summary.wall_time_s,
            "throughput_req_s": summary.throughput_req_s,
            "latency_ms": summary.latency_ms,
            "per_endpoint": summary.per_endpoint,
            "task_waiting": summary.task_waiting,
            "top_errors": summary.top_errors,
        },
    }

    if config.json_mode == "full":
        payload["results"] = [
            {
                "worker_index": result.worker_index,
                "unique_key": result.unique_key,
                "steps": [step.to_dict() for step in result.steps],
            }
            for result in results
        ]

    with config.json_report.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def main() -> int:
    args = parse_args()
    config = build_config(args)
    ensure_safe_target(config.base_url, config.allow_external)

    print("Starting fleet-join simulation")
    print(f"target        : {config.base_url}")
    print(f"workers       : {config.workers}")
    print(f"concurrency   : {config.concurrency}")
    print(f"mode          : {config.mode}")
    print(f"ramp seconds  : {config.ramp_seconds}")
    print(f"client host   : {socket.gethostname()}")
    if config.json_report is not None:
        print(f"json report   : {config.json_report}")
        print(f"json mode     : {config.json_mode}")

    started_at_epoch = time.time()
    started_at = time.perf_counter()
    results: list[WorkerRunResult] = []
    stop_event = threading.Event()
    interrupted = False

    def _request_stop(signum: int, _frame: Any) -> None:
        del _frame
        signal_name = signal.Signals(signum).name
        if not stop_event.is_set():
            print(
                f"\nReceived {signal_name}. "
                "Stopping new submissions and consolidating stats...",
            )
        stop_event.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    def ramp_delay(i: int) -> float:
        if config.ramp_seconds <= 0 or config.workers <= 1:
            return 0.0
        return config.ramp_seconds * (i / (config.workers - 1))

    pool = ThreadPoolExecutor(
        max_workers=config.concurrency,
        thread_name_prefix="fleetjoin",
    )
    pending: set[Future[WorkerRunResult]] = set()
    next_worker = 0

    try:

        def submit_until_limit() -> None:
            nonlocal next_worker
            while (
                not stop_event.is_set()
                and next_worker < config.workers
                and len(pending) < config.concurrency
            ):
                future = pool.submit(
                    simulate_one_worker,
                    worker_index=next_worker,
                    base_url=config.base_url,
                    username=config.username,
                    password=config.password,
                    mode=config.mode,
                    timeout_seconds=config.timeout_seconds,
                    ramp_delay=ramp_delay(next_worker),
                    stop_event=stop_event,
                )
                pending.add(future)
                next_worker += 1

        submit_until_limit()

        while pending:
            done, still_pending = wait(
                pending,
                return_when=FIRST_COMPLETED,
                timeout=0.25,
            )
            pending = set(still_pending)
            for future in done:
                try:
                    result = future.result()
                except Exception as exc:
                    print(f"Worker task raised unexpected exception: {exc}")
                    continue
                if result.steps:
                    results.append(result)
            submit_until_limit()
    except KeyboardInterrupt:
        interrupted = True
        stop_event.set()
        print("\nKeyboardInterrupt received. Consolidating partial statistics...")
    finally:
        for future in pending:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    if stop_event.is_set():
        interrupted = True

    summary = summarize(
        results,
        started_at,
        config.verbose_errors,
        workers_planned=config.workers,
        interrupted=interrupted,
    )
    write_json_report(config, summary, results, started_at_epoch)
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
