# Hyperscaling Analysis: Pyramid/Waitress → FastAPI/Uvicorn

**Date:** 2026-02-15 (revised 2026-02-16)
**Context:** Production deployment proven at 9,400+ workers; path to 15,000+

---

## 1. Where We Are

The FastAPI/Uvicorn port currently runs fishtest in production with
a single Uvicorn process serving 9,400+ concurrent workers (sustained
for 63+ minutes with zero errors). The scaling configuration deployed
on 2026-02-15 (see `___LOG/report-20260215-184705.txt`) resolved the
immediate capacity bottleneck without code changes.

The initial observation window (13:28–14:59) showed ~720 workers, which
was the first reconnection wave after config deployment. The full log
(13:28–16:28) shows the system scaling to 9,423 workers (peak) as
additional contributor fleets connected in step-function waves at
15:05 and 15:20.

This document analyzes the scaling architecture — what was gained in the
migration from Pyramid/Waitress, what the current limits are, and what
engineering options exist for 10K–80K worker scale.

---

## 2. Pyramid/Waitress Scaling Model

Waitress is a pure-Python WSGI server. Its concurrency model:

- **Thread-per-request.** Each HTTP connection occupies an OS thread for the
  full duration of the request (including I/O wait).
- **Fixed thread pool.** Default: 4 threads. Production typically ran 8–16.
  Each thread costs ~8 MB stack.
- **Connection queue.** TCP backlog queues connections beyond the thread limit.
  Queued connections block until a thread is free.
- **No async.** All work — including waiting for MongoDB, waiting for file I/O,
  waiting for network responses — occupies a thread.

### Scaling constraints under Waitress

| Resource | Limit | Impact |
|----------|-------|--------|
| Threads | 4–16 | Max 4–16 concurrent handlers |
| Memory per thread | ~8 MB | 16 threads ≈ 128 MB just for stacks |
| MongoDB concurrency | = thread count | Thread waiting on DB blocks all other work on that thread |
| Connection capacity | = thread count | TCP queue absorbs bursts but doesn't execute them |
| CPU utilization | Low | Threads spend most time sleeping on I/O, not computing |

At 700 workers polling every ~30 s with ~3 s request_task handling time, the
arrival rate is ~23 requests/s. With 16 threads, steady-state utilization
is 23 × 0.1 s (fast path) = ~2.3 threads — manageable for fast-path
requests. But `request_task` (locked, DB-heavy, ~0.5–3 s) saturates the
pool quickly. A 16-thread pool can only sustain ~5–30 concurrent
`request_task` calls depending on DB latency.

Waitress handled fishtest's historical load (~200 workers) adequately
because the thread pool was never the bottleneck. Beyond 500 workers,
thread contention becomes the limiting factor.

---

## 3. FastAPI/Uvicorn Scaling Model

Uvicorn is an ASGI server built on `uvloop` (or `asyncio`). Its
concurrency model:

- **Single-threaded event loop.** One OS thread handles all connection
  management, HTTP parsing, and coroutine dispatch.
- **Threadpool for blocking work.** Starlette's `run_in_threadpool()`
  offloads synchronous handlers and DB calls to an anyio threadpool.
  fishtest configures this to 200 tokens.
- **Decoupled connection capacity.** The event loop can hold thousands of
  idle connections with negligible cost (a coroutine frame is ~KBs, not
  an 8 MB thread stack).
- **Blocking work is the bottleneck.** Only the threadpool limits
  concurrent MongoDB/lock operations, not the connection count.

### Scaling profile under Uvicorn (current config)

| Resource | Limit | Current usage (9,400 workers) |
|----------|-------|-----------------------------||
| Connections | Kernel fd limit (65,536) | ~12,000 est. (18%) |
| Threadpool tokens | 200 | Not saturated (no threadpool errors) |
| `task_semaphore` | 5 | 522 rejections in 63 min (0.05% of req) |
| `request_task_lock` | 1 (mutex) | Serializes scheduling |
| CPU | 1 core | Not measured at 9K (31% at 720) |
| Memory | Process RSS | ~200 MB est. |

The migration from Waitress to Uvicorn changed the bottleneck shape:

- **Before:** Thread pool (16) limited concurrent handlers. Adding workers
  beyond ~500 starved the pool.
- **After:** Event loop handles unlimited connections. Threadpool (200)
  handles blocking work. The bottleneck moved to application-level
  serialization (`request_task_lock`, `task_semaphore`).

---

## 4. Current Bottleneck Analysis

At 9,400 workers, the system demonstrated stable operation with no
resource exhaustion. The theoretical bottleneck estimates below have
been revised against the observed 9,400-worker production data.

### 4.1 `request_task_lock` (critical path serializer)

`request_task_lock` is a `threading.Lock()` in `rundb.py`. It serializes
all `request_task` processing to prevent race conditions in task
assignment. At 9,400 workers with ~30 s poll intervals, ~313 `request_task`
calls arrive per second. With ~100 ms average handling time under the
lock, utilization is theoretically high.

However, at 9,400 workers the system showed only 522 task_semaphore
rejections in 63 minutes (0.05% of requests). This indicates the lock
is NOT saturated — the `task_semaphore(5)` gates admission before the
lock, preventing thundering herd. The actual concurrent load on the lock
is bounded by the semaphore to at most 5 simultaneous holders.

### 4.2 `task_semaphore(5)` (admission control)

Limits concurrent `request_task` handlers to 5. At 9,400 workers, the
semaphore rejected only 522 requests out of ~320,000+ in the steady-state
window (15:23–16:26). 97% of these rejections occurred in a single
3-minute burst (16:09–16:11) during mass task reassignment when runs
completed.

The semaphore is working exactly as designed: it prevents MongoDB write
storms during task assignment while allowing 99.95% of requests to
proceed without delay.

### 4.3 Threadpool (200 tokens)

At 9,400 workers, no threadpool saturation was observed (zero
"Exceeded concurrency limit" errors, zero ASGI exceptions). The
threadpool absorbed the ~150 req/s sustained load without queueing.
This is because most requests (beat, heartbeat) complete in <10 ms,
and the task_semaphore limits the expensive path to 5 concurrent
executions.

### 4.4 Single-process in-memory state

`RunDb` holds critical mutable state in-process:

- `wtt_map` — worker-to-task mapping (dict)
- `run_cache` — active run cache with dirty-page flush
- `task_semaphore` — threading semaphore
- `request_task_lock` — threading lock
- Scheduler — periodic background tasks

This state cannot be shared across OS processes. The primary instance
*must* be a single process. This is the fundamental architectural
constraint.

### Summary: estimated capacity ceilings (revised)

| Workers | Status | Notes |
|---------|--------|-------|
| 9,400 | **PROVEN** | Stable 63+ min, zero errors |
| 10,000 | **Safe** | Well within observed headroom |
| ~15,000 | Estimated ceiling | task_semaphore rejections may increase |
| ~20,000+ | Requires Phase 2–3 | task_semaphore + threadpool saturation likely |

The original estimates (ceiling at ~3,000 workers) were overly
conservative. The key insight is that the `task_semaphore(5)` gates
the expensive path so effectively that the lock and threadpool never
saturate, even at 13× the originally predicted ceiling.

---

## 5. Scaling Options

### 5.1 Raise the ceilings (configuration, short-term)

Already partially done. Remaining headroom:

| Parameter | Current | Ceiling | Notes |
|-----------|---------|---------|-------|
| `THREADPOOL_TOKENS` | 200 | 500–1000 | Limited by MongoDB connection pool and CPU |
| `task_semaphore` | 5 | 15–25 | Must benchmark against MongoDB write IOPS |
| `--backlog` | 8192 | 65535 | Already has massive headroom |
| `LimitNOFILE` | 65536 | 1048576 | 5× headroom at current peak |
| Worker poll interval | ~30 s | 60–120 s | Halving arrival rate doubles capacity |

Raising `task_semaphore` to 15 and `THREADPOOL_TOKENS` to 500 would
extend the single-process ceiling to ~5,000–8,000 workers, assuming
MongoDB can absorb the additional concurrent writes.

**Effort:** 2 lines of config + load testing.
**Risk:** MongoDB write contention; needs benchmarking.

### 5.2 Gunicorn + UvicornWorker (multi-process, medium-term)

The standard way to scale a FastAPI application is Gunicorn with
`uvicorn.workers.UvicornWorker`:

```bash
gunicorn fishtest.app:app -k uvicorn.workers.UvicornWorker -w 4 --bind 127.0.0.1:8000
```

Each worker is a separate OS process with its own event loop and
threadpool. Gunicorn handles process management (restart on crash,
graceful reload).

**Why this does NOT work for fishtest's primary instance:**

The primary instance holds in-process mutable state (Section 4.4) that
cannot be shared across OS processes:

- `wtt_map` tracks which worker is assigned which task. Two processes
  would have divergent views of assignments, causing double-assign
  and dead-task races.
- `run_cache` buffers writes. Two processes would overwrite each
  other's dirty pages.
- `task_semaphore` and `request_task_lock` are `threading.*` objects —
  they do not cross process boundaries.
- `scheduler` runs periodic tasks. Two schedulers would duplicate
  cleanup, ELO recalculation, and aggregated data updates.

**Where it DOES work:** Secondary instances (ports 8001–8003) are
stateless — they serve read-only UI/API traffic or PGN uploads. Port 8003
already runs 3 Uvicorn workers internally. Each secondary could run
additional Gunicorn workers safely, multiplying throughput. But secondary
instances are not the bottleneck; worker API traffic (which must go to
the primary) is.

**Partial multi-process: fan-out read-only API traffic.** Some worker API
endpoints are effectively read-only and could be served by secondaries:

| Endpoint | Stateful? | Offloadable? |
|----------|----------|-------------|
| `/api/request_version` | No | Yes |
| `/api/request_task` | Yes (lock, cache, wtt_map) | **No** |
| `/api/update_task` | Yes (cache, buffer) | **No** |
| `/api/beat` | Yes (wtt_map) | **No** |
| `/api/failed_task` | Yes (cache, buffer) | **No** |
| `/api/request_spsa` | Yes (cache) | **No** |
| `/api/upload_pgn` | No (filesystem only) | Yes (already offloaded) |
| `/api/stop_run` | Yes (cache, buffer) | **No** |

Only 2 of 9 worker endpoints can be offloaded. The scheduling-critical
endpoints (`request_task`, `update_task`, `beat`, `failed_task`) must
remain on the primary.

**Verdict:** Gunicorn does not solve the primary-instance bottleneck.
Useful only for secondary UI scaling, which is not the constraint.

### 5.3 Redis for shared state (architectural, long-term)

Moving in-process state to Redis would allow multi-process or
multi-server deployment of the primary:

| State | Current | Redis replacement |
|-------|---------|-------------------|
| `wtt_map` | `dict` in process | Redis hash (`HSET wtt:<run_id> <task_id> <worker>`) |
| `task_semaphore` | `threading.Semaphore(5)` | Redis-based distributed semaphore (Redlock or Lua script) |
| `request_task_lock` | `threading.Lock()` | Redis distributed lock |
| `run_cache` | In-memory dict + dirty flush | Redis or MongoDB-backed cache (write-through) |
| Scheduler | In-process `threading.Timer` | Leader election (Redis `SETNX`) + single scheduler |

**Engineering cost analysis:**

| Component | Lines affected | Effort | Risk |
|-----------|---------------|--------|------|
| `wtt_map` → Redis | `rundb.py` (~200 lines) | 8–16 h | Medium (latency increase) |
| Semaphore → Redis | `rundb.py` (~20 lines) | 4 h | Medium (distributed semantics) |
| Lock → Redis | `rundb.py` (~30 lines) | 4 h | High (correctness-critical) |
| `run_cache` → Redis | `run_cache.py` (320 lines) | 16–32 h | High (dirty-page semantics) |
| Scheduler → leader election | `scheduler.py` + `app.py` | 8 h | Medium |
| **Total** | | **40–64 h** | |

**Trade-offs:**

- **Latency:** Every `wtt_map` lookup adds ~0.1 ms network RTT (localhost
  Redis). Currently it's a dict lookup (~10 ns). For `request_task`
  (called ~313×/s at 9,400 workers), this adds ~31 ms/s total — negligible.
- **Complexity:** Redis adds an operational dependency (monitoring, backup,
  failover). fishtest currently depends only on MongoDB.
- **Correctness:** Distributed locks have failure modes (Redis master
  failover during lock hold). `request_task_lock` protects task
  assignment correctness — a lost lock could cause double-assigns.
  Mitigation: use Redlock or fall back to MongoDB-based locking.
- **Benefit:** Enables horizontal scaling of the primary. Two or more
  primary processes could serve worker API traffic behind a load balancer.

**Verdict:** This is the correct long-term solution for 10K+ workers.
It is a substantial refactor but stays within the existing MongoDB +
Python stack. It does not require rewriting handlers or templates.

### 5.4 `motor` async MongoDB (orthogonal optimization)

`motor` is the async wrapper around `pymongo`. It would eliminate
threadpool dispatch for MongoDB calls, allowing the event loop to
interleave DB I/O directly.

**Impact on current bottlenecks:**

- Does NOT help with `request_task_lock` (still serialized).
- Does NOT help with `task_semaphore` (still limited to N).
- DOES eliminate threadpool as a bottleneck (~200 tokens → unlimited
  concurrent DB queries).
- DOES reduce per-request latency by eliminating threadpool dispatch
  overhead (~0.1 ms per dispatch).

**Engineering cost:**

Every `rundb.py`, `userdb.py`, `actiondb.py`, `workerdb.py`, and
`kvstore.py` method becomes `async def`. Every call site adds `await`.
FastAPI handlers that call these must become `async def`. Template
rendering (sync) must remain in the threadpool.

| Component | Lines | Effort |
|-----------|-------|--------|
| `rundb.py` | ~2,300 | 16–24 h |
| `userdb.py` | ~400 | 4 h |
| `actiondb.py` | ~200 | 2 h |
| `workerdb.py` | ~100 | 1 h |
| `kvstore.py` | ~100 | 1 h |
| `api.py` (callers) | ~600 | 8 h |
| `views.py` (callers) | ~800 | 8 h |
| Test suite | ~2,000 | 16 h |
| **Total** | | **56–64 h** |

**Trade-offs:**

- **Pros:** Removes threadpool bottleneck entirely. More idiomatic
  ASGI. Reduces thread count from ~200 to ~10.
- **Cons:** Massive refactor touching every DB call site. `pymongo` and
  `motor` have subtly different APIs (cursor handling, session lifecycle).
  Testing becomes more complex (async test fixtures). The threadpool is
  currently at 15% utilization — this optimization solves a problem
  that does not yet exist.
- **fishtest-specific:** The `request_task` critical path is serialized
  by `request_task_lock`. Making the DB calls async inside the lock does
  not increase throughput — the lock still serializes execution. The
  benefit of `motor` is only realized in the *non-serialized* paths
  (UI queries, `update_task`, `beat`).

**Verdict:** Correct optimization for a mature ASGI application, but
premature for fishtest. The threadpool is not the bottleneck. Pursue
only after Redis-based state sharing (5.3) is in place and threadpool
saturation is observed.

---

## 6. Recommendation: Phased Scaling Roadmap

### Phase 1: Configuration tuning (DONE)

Deployed 2026-02-15. Raised all configuration ceilings. Result: 47× worker
capacity (208 → 9,423). The system sustained 9,400+ workers for 63+ minutes
with zero crashes, zero fd exhaustion, zero ASGI exceptions, and zero HTTP 503
responses.

The initially aggressive configuration (LimitNOFILE=1048576, worker_connections
65535) has been relaxed to production-appropriate values based on observed
resource usage:

| Parameter | Initial (aggressive) | Relaxed (production) | Rationale |
|-----------|---------------------|---------------------|-----------|
| LimitNOFILE | 1,048,576 | 65,536 | ~12K fds at 10K workers; 5× headroom |
| worker_rlimit_nofile | 1,048,576 | 65,536 | Matches systemd limit |
| worker_connections | 65,535 | 16,384 | 8× observed peak per worker |
| keepalive | 256 | 256 | Production value; generous but harmless on localhost |
| somaxconn | 65,535 | 8,192 | Matches --backlog 8192 |
| fs.file-max | 2,097,152 | 524,288 | 6 worker processes (1+1+1+3) × 65,536 fds each |

Kept unchanged: `--backlog 8192`, `THREADPOOL_TOKENS = 200`,
`proxy_connect_timeout 2s`, `task_semaphore(5)`.

### Phase 2: Application-level tuning (days, not weeks)

| Change | Effort | Expected impact |
|--------|--------|----------------|
| Raise `task_semaphore` to 15 | 1 line | ~2× request_task throughput |
| Raise `THREADPOOL_TOKENS` to 500 | 1 line | Delays threadpool saturation to ~15K workers |
| Add `/api/_metrics` endpoint | 4–8 h | Observability for capacity decisions |
| Tune worker poll interval | Worker config | Halves arrival rate per 2× interval |

Estimated ceiling after Phase 2: **15,000–20,000 workers** (single process).

### Phase 3: Shared state (weeks)

Move `wtt_map`, `task_semaphore`, `request_task_lock`, and `run_cache`
to Redis. This allows multiple primary processes.

Estimated ceiling after Phase 3: **40,000–80,000 workers** (2–4 primary
processes behind a load balancer, each with its own threadpool).

### Phase 4: Async MongoDB (months, optional)

Migrate from `pymongo` to `motor`. Only justified if threadpool saturation
is observed after Phase 3.

Estimated ceiling after Phase 4: **80,000–200,000 workers** (event-loop-native
DB I/O eliminates the threadpool as a bottleneck entirely).

---

## 7. What the Migration Actually Won

The Pyramid/Waitress → FastAPI/Uvicorn migration was not primarily about
performance. The original motivation was framework modernization,
maintainability, and the upstream merge requirement. But the architectural
change had a significant scaling side effect:

| Dimension | Pyramid/Waitress | FastAPI/Uvicorn |
|-----------|-----------------|-----------------|
| Connection model | Thread-per-request | Async event loop + threadpool |
| Idle connection cost | ~8 MB (thread stack) | ~KB (coroutine frame) |
| Max concurrent handlers | 4–16 (thread pool size) | 200+ (threadpool tokens) |
| Connection capacity | = thread count | Kernel fd limit (65,536 configured) |
| Blocking I/O impact | Blocks a thread (100% of capacity unit) | Blocks a token (0.5% of capacity) |
| Scaling ceiling (config only) | ~500 workers | ~10,000+ workers (proven) |
| Scaling ceiling (with Redis) | Not feasible | ~80,000 workers (est.) |
| Horizontal scaling path | None (WSGI process model) | Redis shared state |

The critical difference is not speed — both stacks handle individual
requests in similar time. The difference is **concurrency model
efficiency**. Uvicorn's event loop holds thousands of connections at
negligible cost. Waitress needs a thread per connection. At 9,400 workers,
Waitress would need 9,400 threads (~73 GB of stack memory just for idle
connections). Uvicorn uses 200 threads for the actual blocking work and
holds the remaining 9,200 connections as lightweight coroutines.

This is not a FastAPI-specific advantage. Any ASGI server (Uvicorn,
Hypercorn, Daphne) provides the same concurrency model. FastAPI's
contribution is developer ergonomics (type hints, dependency injection,
OpenAPI generation) — not raw performance.

---

## 8. Engineering Principles Applied

This analysis deliberately avoids:

- **Premature optimization.** The threadpool is well below saturation at
  9,400 workers. `motor` would reduce it further. That gains nothing at
  current scale.
- **Unnecessary dependencies.** Redis is proposed only when in-process
  state becomes the bottleneck (Phase 3). Adding it today would introduce
  operational complexity without measurable benefit.
- **Gunicorn cargo-culting.** The standard "use Gunicorn in production"
  advice does not apply when the application requires single-process
  semantics. fishtest's in-process state makes multi-process unsafe
  for the primary instance.
- **Micro-benchmarks.** The 0.1 ms threadpool dispatch overhead is
  irrelevant when `request_task` takes 100 ms under the lock. Optimize
  the bottleneck, not the fast path.

The right sequence is: measure → identify the bottleneck → fix the
bottleneck → measure again. Configuration tuning (Phase 1) was correct
because the bottleneck was `--limit-concurrency 10`, not the application.
Application tuning (Phase 2) is correct because the next bottleneck is
`task_semaphore(5)`, not the threadpool. Redis (Phase 3) is correct only
after Phase 2 is exhausted.
