# Pyramid → FastAPI migration plan for fishtest

This document defines an incremental migration from the current Pyramid (WSGI) server to a FastAPI (ASGI) service, with **worker protocol correctness** as the top priority.

Repository context (as of 2026-01-08):

- Pyramid app factory + auth/session wiring: `server/fishtest_pyr/__init__.py`
- Pyramid routes: `server/fishtest_pyr/routes.py`
- Pyramid API handlers (worker + public): `server/fishtest_pyr/api.py`
- Pyramid UI handlers (HTML/Mako): `server/fishtest_pyr/views.py`
- FastAPI app (ASGI): `server/fishtest/app.py`
- FastAPI routers:
  - Public + user API: `server/fishtest/api/public.py`
  - Worker protocol API: `server/fishtest/api/worker.py`
  - UI routes (Mako): `server/fishtest/views/*`
- Templates/static: `server/fishtest/templates`, `server/fishtest/static`
- Config: `server/development.ini`, `server/production.ini`

## Goals

- Preserve external behavior (paths, status codes, response JSON shape, headers).
- Keep rollback trivial (reverse-proxy routing switch).
- Migrate incrementally with contract tests and canaries.
- Avoid regressions in worker protocol and run/task lifecycle semantics.

## Current implementation status (repo)

As of this document update, the repository contains a running FastAPI service under `server/fishtest/`.
The old `server/fishtest_fastapi/` package has been merged into `server/fishtest/`, and Pyramid-only entrypoints were moved into `server/fishtest_pyr/`.

Implemented:

- **FastAPI app + DB wiring**: `server/fishtest/app.py`
  - Initializes `RunDb` and attaches `rundb/userdb/actiondb/workerdb` to `app.state`.
  - Mounts existing static assets at `/static`.
  - Provides `GET /health`.
  - Implements the **primary-instance** behavior gate using env vars:
    - `FISHTEST_PORT` and `FISHTEST_PRIMARY_PORT`.
    - If unset/unknown (default `-1`), behavior matches Pyramid’s “assume primary when not determinable”.

- **PoC module removed (no duplication)**
  - The original PoC router file has been deleted.
  - `GET /api/rate_limit` lives in `server/fishtest/api/public.py`.
  - `GET /rate_limits` is implemented in `server/fishtest/views/rate_limits.py`.

- **Worker protocol endpoints (Phase 1 scope)**: `server/fishtest/api/worker.py`
  - `/api/request_version`
  - `/api/request_task`
  - `/api/update_task`
  - `/api/beat`
  - `/api/request_spsa`
  - `/api/failed_task`
  - `/api/stop_run`
  - `/api/upload_pgn`
  - `/api/worker_log`

  Notes:

  - Error responses are shaped to match the worker expectations: JSON dict with `error` and `duration`.
  - Mutating worker endpoints enforce “primary instance only” via `RunDb.is_primary_instance()`.

- **UI auth endpoints (`/login`, `/logout`)**

  Update: FastAPI-native auth is implemented in `server/fishtest/views/auth.py` using the custom HMAC-signed cookie session + CSRF (no Pyramid proxying).

- **UI test listings + run detail pages**:
  - `server/fishtest/views/tests.py`: `/tests`, `/tests/finished`, `/tests/machines`, `/tests/user/{username}`.
  - `server/fishtest/views/tests_view.py`: `/tests/view/{id}`, `/tests/tasks/{id}`.

- **Public API needed by the UI**:
  - `server/fishtest/api/public.py`:
    - `GET /api/rate_limit`
    - `GET /api/active_runs`
    - `GET /api/finished_runs`
    - `POST /api/actions`
    - `GET /api/get_run/{id}`
    - `GET /api/get_task/{id}/{task_id}`
    - `GET /api/get_elo/{id}`
    - `GET /api/calc_elo`
    - `GET /api/nn/{id}`
    - `GET /api/pgn/{id}`
    - `GET /api/run_pgns/{id}`

Update: FastAPI now implements all Pyramid `/api/*` routes listed in `server/fishtest/routes.py` (worker + public).

Note: those route declarations live in `server/fishtest_pyr/routes.py` after the Pyramid-only split.

Operational notes:

- Install FastAPI runtime deps via the optional dependency group: `server/pyproject.toml` → `[dependency-groups].fastapi`.
- If deploying multiple instances, set `FISHTEST_PORT`/`FISHTEST_PRIMARY_PORT` so only one instance runs primary-only behaviors.

### Recent compatibility updates (2026-01-10)

This section documents a couple of “behavior parity” fixes that matter in production.

#### Worker endpoints: sync handlers to preserve threaded semantics

Pyramid/Waitress ran handlers in a pool of threads. Much of fishtest’s core logic (notably `RunDb`) and DB access is blocking.

FastAPI supports `async def` endpoints, but **calling blocking code from `async def` blocks the event loop** and changes the effective concurrency model.

Current approach:

- Worker endpoints in `server/fishtest/api/worker.py` are defined as **synchronous** `def` handlers.
- FastAPI runs sync handlers in its worker threadpool, which restores “Waitress-like” semantics for:
  - `threading.Semaphore` throttles inside `RunDb.request_task()`.
  - `threading.Lock`-based critical sections.
  - Blocking `pymongo` I/O.

Notes:

- This is a per-process model. Running Uvicorn with `--workers > 1` creates multiple processes, so in-memory semaphores/locks do not coordinate across workers.
- For the **primary instance** (scheduler/background jobs), use `--workers 1`.

#### Static assets: Pyramid-compatible cache-busting token

Pyramid historically served static assets with a query-string cache buster.
The UI templates use `request.static_url('fishtest:static/...')` and expect the URL to vary when the underlying file changes.

Current approach:

- `TemplateRequest.static_url()` (used by the FastAPI UI/Mako shim) returns `/static/<path>?x=<token>`.
- The token is a **Pyramid-compatible** value: base64(sha384(file-bytes)).
- Tokens are cached in-process to avoid hashing on every request.

Deployment implication:

- To take advantage of the cache buster, serve `/static/` with long-lived cache headers (e.g., `Cache-Control: public, max-age=31536000, immutable`).
- If nginx serves static files, ensure it serves the `/static/` namespace (not only `/css`, `/js`, etc.) and handle conventional root fetches like `/robots.txt` and `/favicon.ico`.

#### Signals and shutdown: uvicorn + lifespan parity

Legacy Pyramid behavior:

- `SIGUSR1` prints stack traces for all threads (debugging).
- `SIGINT`/`SIGTERM` call `RunDb.exit_run()`, which:
  - sets `rundb._shutdown = True` (reject new requests),
  - stops the scheduler,
  - flushes the run cache and saves persistent state (primary instance only),
  - writes a `system_event` "stop fishtest@<port>",
  - exits.

FastAPI/Uvicorn considerations:

- Uvicorn already owns `SIGINT`/`SIGTERM` (graceful server stop). Installing our own
  handlers for those signals can conflict with Uvicorn.

Current FastAPI approach:

- `SIGUSR1` is supported via `faulthandler.register(..., all_threads=True)` in `server/fishtest/app.py`.
- `SIGINT`/`SIGTERM` cleanup is implemented in the FastAPI **lifespan shutdown** in `server/fishtest/app.py`.
  This preserves Pyramid's cleanup steps without overriding Uvicorn signal handlers.
- A small middleware returns HTTP `503` once `rundb._shutdown` is set (matching Pyramid's
  per-request `HTTPServiceUnavailable` guard).

#### Pyramid subscribers → FastAPI equivalents

Pyramid installed several per-request and app-start subscribers. In FastAPI these behaviors are implemented via lifespan + middleware:

- `add_rundb (NewRequest)` → request access via `request.app.state.*`, plus middleware attaches `rundb/userdb/actiondb/workerdb` to `request.state`.
- `check_shutdown (NewRequest)` → middleware returns HTTP `503` when `rundb._shutdown` is set.
- `check_blocked_user (NewRequest)` → middleware checks UI sessions for blocked users and clears the session + redirects to `/tests`.
- `set_default_base_url (NewRequest)` → middleware sets `rundb.base_url` from the external request scheme/host the first time a request arrives (when `FISHTEST_URL` is not configured).
- `init_app (ApplicationCreated)` → FastAPI lifespan startup runs `gh.init`, `update_aggregated_data`, and `schedule_tasks` on the primary instance.
- `add_renderer_globals (BeforeRender)` → no-op in Pyramid (not needed).

## Modern FastAPI stack (2026) — packages + application shape

This section documents the **target “modern FastAPI” stack** for this migration.
It is intentionally scoped to what fits fishtest’s constraints:

- MongoDB access remains via the existing `RunDb`/`UserDb`/… adapters during migration.
- Worker protocol stays strict and stable.
- UI must move to FastAPI (Pyramid is a temporary dependency during the strangler phase).

### Recommended packages

Core runtime (already present or already used by the repo FastAPI service):

- `fastapi` — ASGI web framework.
- `uvicorn` — ASGI server (systemd-managed is fine for the primary instance).
- `pydantic` (v2) — request/response models for new FastAPI-native endpoints.

Security + UX (UI-focused, cookie-based authentication):

- `redis` (redis-py) — Redis client used for sessions and (optionally) rate-limiting.
- A Redis-backed session library/middleware (choose one, see “Session strategy” below).
- `fastapi-csrf-protect` (or an equivalent CSRF implementation) — CSRF protection for cookie-auth UI POSTs.

Traffic shaping / abuse protection (optional during early migration):

- `fastapi-limiter` — Redis-backed rate limiting.

OAuth/OIDC (optional, only if/when you add social login or SSO):

- `Authlib` — OAuth2/OIDC client/server building blocks.

Observability (recommended once routing real traffic):

- `structlog` (optional) — JSON-structured logs.
- OpenTelemetry packages (optional) — distributed tracing if you already have an OTEL backend.

Notes:

- “FastAPI Users” is not recommended as the first move for fishtest because it tends to own your user model and flows; fishtest already has a custom `userdb` schema and worker protocol semantics. If you want it later, introduce it after UI auth is stable.
- Password encryption/hashing strategy is intentionally out of scope for this document revision.

### Suggested dependency groups (`server/pyproject.toml`)

The repo already has:

- `[dependency-groups].fastapi = ["fastapi", "uvicorn"]`

Recommended additions (proposal; implement when ready):

- `[dependency-groups].fastapi_security`:
  - `redis`
  - `fastapi-csrf-protect`
  - `fastapi-limiter`
  - `Authlib` (optional)

This keeps the migration incremental: you can run worker + public endpoints without Redis first, then turn on UI auth with Redis+CSRF.

### Target application shape (what “replace Pyramid” means)

The target end state is a single ASGI service:

- FastAPI owns all routes: worker API, public API, and UI routes.
- MongoDB remains the system of record.
- Redis is used for UI sessions (and optionally rate limiting).

Practical FastAPI structure (recommended modules):

- `server/fishtest/app.py` — app factory + lifespan init + router wiring.
- `server/fishtest/cookie_session.py` — HMAC-signed cookie session + CSRF + flash.
- `server/fishtest/mako.py` — Mako lookup + rendering helpers.
- `server/fishtest/api/worker.py` — worker protocol endpoints.
- `server/fishtest/api/public.py` — public/user endpoints used by the UI.
- `server/fishtest/views/` — UI routes (Mako rendering).

Middleware order (high-level):

1. Request id (set `X-Request-Id`, log correlation).
2. Access logging + timing.
3. Security headers (HSTS, X-Content-Type-Options, etc.)
4. Session middleware (cookie → session id → Redis)
5. CSRF middleware (enforce on unsafe methods for UI routes)
6. Router handlers

### Session strategy (UI)

Preferred direction for the UI:

- Use **server-side sessions** (Redis) and store only a signed session id in the browser cookie.
- Keep cookies `HttpOnly`, `Secure` (behind HTTPS), and `SameSite=Lax`.

This provides modern safety properties (logout invalidation, multi-instance friendly) and cleanly supports flash messages.

### Deployment guidance (fits fishtest “primary instance”)

- Primary instance: run **one** FastAPI process (avoid multiple scheduler threads/processes). Systemd + `uvicorn` is fine.
- Secondary instances (if any): can run multi-worker safely only if you disable primary-only behaviors.

Remember to set:

- `FISHTEST_PORT` and `FISHTEST_PRIMARY_PORT`
- Existing env vars already used in deployment (examples): `FISHTEST_URL`, `FISHTEST_NN_URL`, `FISHTEST_AUTHENTICATION_SECRET`, `FISHTEST_CAPTCHA_SECRET`

## Non-goals (for this plan)

- Redesigning the UI/UX or changing user-visible behavior.
- Replacing MongoDB or redesigning the data model.

---

## 1) Status quo analysis (APIs, views, storage, operational shape)

### 1.1 Runtime components

- **Server (Pyramid, WSGI)**: handles both `/api/...` and UI routes.
- **Worker client** (see `worker/`): calls worker endpoints and is highly sensitive to protocol compatibility.
- **UI (HTML + Mako templates)**: rendered by Pyramid views, includes JavaScript that calls `/api/...`.

### 1.2 Storage and core state

- Primary persistent storage is **MongoDB**, accessed via `pymongo.MongoClient`.
- Core DB adapters:
  - `server/fishtest/rundb.py` (run/task lifecycle, locking, scheduling coupling)
  - `server/fishtest/userdb.py` (users/permissions)
  - `server/fishtest/actiondb.py` (actions/logs)
  - `server/fishtest/kvstore.py` (key-value store)

Migration implication: the “hard” work is not HTTP routing; it is preserving these database-backed invariants.

### 1.3 API surface (what must remain stable)

This repository has two broad API classes:

- **Worker API**: strict POST+JSON protocol with identity invariants. Any behavior drift can break workers.
- **Public/Web API**: mostly GET-ish JSON/streaming endpoints used by UI and external consumers.

Key worker correctness constraints (from `WorkerApi.validate_request()` and endpoint logic):

- Request bodies validate against schemas (see `server/fishtest/schemas.py`).
- When `run_id/task_id` are present, they must be valid and correspond to an existing task.
- `worker_info.username` and `worker_info.unique_key` must match the assigned task’s stored worker identity.
- Concurrency/locking behavior (notably `active_run_lock(run_id)`) must be preserved.

Hard-first implementation priority (worker endpoints):

1. `/api/update_task`
2. `/api/request_task`
3. `/api/beat`
4. `/api/failed_task`
5. `/api/stop_run`
6. `/api/request_spsa`
7. `/api/upload_pgn`
8. `/api/worker_log`
9. `/api/request_version`

Full API inventory is maintained in **Appendix A**.

### 1.4 UI/views surface (what makes UI migration hard)

UI routes in Pyramid are not “just HTML”. They are tightly coupled to:

- **Auth cookie semantics**: `AuthTktAuthenticationPolicy` + `remember()` / `forget()`.
- **Sessions and flash messages**: `SignedCookieSessionFactory("fishtest")` + `request.session.flash(...)`.
- **Per-view CSRF**: selective `require_csrf=True` for sensitive POST routes.
- **Template rendering**: Mako (`*.mak`) and Pyramid request/response patterns.

UI migration is a **requirement**: the UI must ultimately be served by FastAPI.

The safe way to get there is still incremental. Early in the migration we may keep some UI routes on Pyramid while we validate auth/session/CSRF/template parity in FastAPI (see Phase 1).

Full UI route checklist is maintained in **Appendix B**.

---

## 2) Migration process and technologies

### 2.1 Chosen migration approach: strangler fig via reverse proxy

Use two processes behind a reverse proxy:

- **Pyramid** remains authoritative for everything not yet migrated.
- **FastAPI** serves a growing subset of paths.
- **nginx** routes specific paths to FastAPI.
- Rollback is a proxy rule revert.

This is the best fit for fishtest because it cleanly isolates protocol-critical worker routes and provides an operationally safe rollback.

Note: Starlette `WSGIMiddleware` (mounting Pyramid into FastAPI) is acceptable for local/dev experiments only; do not use it as the production migration strategy.

### 2.1a Architecture diagram (text)

Target steady state during migration (two processes behind a proxy):

```text
    +------------------+
    |  Reverse proxy   |
    | (nginx)  |
    +---------+--------+
         |
     +-------------+-------------+
     |                           |
   /api/* (migrated)           everything else
     |                           |
  +-------v--------+          +-------v--------+
  |  FastAPI (ASGI)|          | Pyramid (WSGI) |
  |  API service   |          | UI + legacy API|
  +-------+--------+          +-------+--------+
     |                           |
     +-------------+-------------+
         |
       +------v------+
       |  MongoDB    |
       | (pymongo)   |
       +-------------+
```

Key property: rollback is a proxy rule change; Pyramid stays intact.

### 2.2 FastAPI service shape (constraints)

The FastAPI service must preserve:

- Paths and methods exactly.
- Status codes and error shaping.
- Response JSON shape (including any `duration` augmentation).
- Headers (notably for CORS and streaming/download endpoints).

Implementation notes:

- Use Pydantic models to mirror existing schema constraints.
- Keep a thin HTTP layer: “parse/validate → call shared core → shape response”.

### 2.3 Shared-core strategy (avoid duplicating business logic)

Do not fork `rundb` semantics into two independent implementations.
Instead, extract or wrap shared logic so both stacks converge on the same core behavior.

Minimum shared adapters needed for worker endpoints:

- validation mirroring `WorkerApi.validate_username_password()` and `WorkerApi.validate_request()`
- locking mirroring `active_run_lock(run_id)`
- error shaping mirroring the existing `handle_error(...)` behavior

### 2.4 Testing, observability, and rollout safety

Non-negotiables before routing real worker traffic to FastAPI:

- Contract tests for worker endpoints (golden request/response behavior).
- Structured logs: request id, endpoint, latency, and safe identifiers (e.g., worker username).
- Proxy kill switch and a canary plan.

The worker contract test matrix is maintained in **Appendix C**.

### 2.5 Risks and mitigations

| Risk | Why it matters | Mitigation (planned) | Detection/exit criteria |
|---|---|---|---|
| Worker protocol drift | Breaks workers; highest blast radius | Contract tests + golden fixtures; shadow/compare before cutover; canary per endpoint | Zero/near-zero diffs in shadow; contract tests pass vs Pyramid |
| Locking/concurrency mismatch | Data corruption or stuck runs/tasks | Preserve `active_run_lock(run_id)` semantics; keep shared-core logic close to `rundb` | No lock-related regressions in shadow/canary; stable run/task invariants |
| Error/status/JSON shape changes | Downstream clients depend on exact shape | Centralize error shaping; snapshot responses for key endpoints | Contract tests assert status + JSON shape + key headers |
| Auth/session/CSRF incompatibility (UI) | Forces relogin, breaks forms, security regressions | UI migration is required: implement AuthTkt/session/CSRF parity early (Phase 1) and migrate UI incrementally | Login/logout parity proven; Appendix B checklist completed |
| Proxy misrouting | Sends traffic to wrong stack; hard to debug quickly | nginx route by explicit path allowlist; keep a kill-switch; deploy with small blast radius | Immediate rollback possible; monitor 404/5xx by upstream |
| Performance regressions under load | Worker endpoints are latency-sensitive | Baseline perf tests for worker endpoints; keep handlers thin; add caching where already present | p95 latency and error rate comparable to Pyramid during canary |
| Observability gaps | Makes debugging diffs/canary failures slow | Request id, structured logs, endpoint tagging; explicit metrics by endpoint | Can correlate proxy → app logs; endpoint-level dashboards |

### 2.6 Success metrics (canary gates)

These are the concrete go/no-go gates for Phase 5 cutover. Defaults below assume we are comparing FastAPI vs Pyramid for the *same endpoint*.

| Metric | How to measure | Gate (before expanding canary) | Notes |
|---|---|---|---|
| p95 latency | per-endpoint p95 over a stable window (e.g., 30–60 min) | FastAPI p95 ≤ 1.2× Pyramid p95 (or an explicit exception) | Track separately for worker vs public endpoints; watch tail latency. |
| Error rate | 5xx rate + “logical error” responses (endpoint-specific) | 5xx rate not higher than Pyramid; no new error modes | For worker endpoints, also track schema/auth failures to catch drift. |
| Diff rate (shadow compare) | % of shadow requests with mismatched status/shape | ≤ 0.1% mismatches, and all mismatches explained/accepted | Prefer “diffs near zero” before routing real worker traffic. |
| Contract test pass rate | CI and local contract suite | 100% pass vs Pyramid baseline | Treat any failure as a block for cutover. |
| Run/task invariant violations | auditing logs/alerts for bad `run_id/task_id/unique_key` bindings | 0 | Any invariant break is a rollback trigger. |

Rollback trigger (always): any sustained regression on the above metrics after cutover.

---

## 3) Proof of Concept (PoC): migrate one API + one view

Purpose: prove end-to-end strangler routing with both JSON and HTML, without involving auth/CSRF/worker protocol.

PoC endpoints (low risk, intentionally boring):

- API: `GET /api/rate_limit` (Pyramid: `UserApi.rate_limit`)
- View: `GET /rate_limits` (Pyramid: `rate_limits`, template `rate_limits.mak`)

Implementation note (current repo state):

- The PoC endpoint `GET /api/rate_limit` is implemented in `server/fishtest/api/public.py`.
- The PoC HTML view `GET /rate_limits` is implemented in `server/fishtest/views/rate_limits.py`.

Why this pair works:

- The page `/rate_limits` relies on browser JS that calls `/api/rate_limit`, so we validate that **both** migrated routes work together.

PoC deliverables:

- FastAPI serves:
  - `GET /api/rate_limit` with the same JSON shape as Pyramid
  - `GET /rate_limits` rendered using the existing Mako template
- Reverse proxy routes exactly these two paths to FastAPI; everything else stays on Pyramid.

PoC exit criteria:

- Loading `/rate_limits` renders and populates correctly (JS fetch to `/api/rate_limit` succeeds).
- Status code + JSON shape match Pyramid for `/api/rate_limit`.
- Rollback is a single proxy config change.

---

## 4) Migration phases (after PoC is proven)

These phases start **after** the PoC succeeds.

### Phase 1 — Hard-parts cost assessment (test first)

Goal: assess the true cost of migration by tackling the hardest compatibility work first.

Deliverables:

- FastAPI baseline (minimum needed to run the assessment):
  - health endpoint (internal)
  - consistent error response shaping
  - request id + latency logging
  - configuration loading consistent with Pyramid deployment needs

- Worker hard parts (protocol correctness):
  - Contract test harness + golden fixtures for all worker endpoints (Appendix C).
  - Implement one strict worker endpoint end-to-end in FastAPI (recommended: `/api/update_task`) behind shadow/compare.

- UI hard parts (UI migration is required):
  - Implement AuthTkt compatibility in FastAPI (read/validate Pyramid cookie).
  - Implement sessions + flash messages sufficient for migrated pages.
  - Implement per-route CSRF parity.
  - Render Mako templates (reuse existing templates initially).
  - Migrate one hard UI slice end-to-end (recommended: `/login` + `/logout`) and verify parity.

Implementation note (current repo state):

- `/login` + `/logout` are currently routed through FastAPI but handled by forwarding to Pyramid (see “Current implementation status”).
- The “target end state” for Phase 1 remains a native FastAPI implementation of auth/session/CSRF for these routes.

Exit criteria:

- Contract tests pass against Pyramid baseline.
- Shadow diff rate meets Section 2.6 gates for the selected strict worker endpoint.
- Login/logout parity verified (cookie set/cleared, CSRF enforced, redirects correct).
- Written cost assessment: remaining endpoints grouped by complexity with effort estimates.

Exact validation commands (once the harness exists):

- Contract test files (to be added):
  - Worker API contracts: `server/tests/contract/test_worker_api_contract.py`
  - UI auth contracts (login/logout): `server/tests/contract/test_ui_auth_contract.py`

- Run Pyramid locally (dev):
  - `pserve server/development.ini`
  - Pyramid listens on `http://127.0.0.1:6542` by default (see `[server:main]` in `server/development.ini`).

- Run worker contracts against Pyramid:
  - `FISHTEST_BASE_URL=http://127.0.0.1:6542 python -m pytest server/tests/contract/test_worker_api_contract.py -q`

- Run UI auth contracts against Pyramid:
  - `FISHTEST_BASE_URL=http://127.0.0.1:6542 python -m pytest server/tests/contract/test_ui_auth_contract.py -q`

- Run FastAPI locally:
  - Install the optional FastAPI deps: `pip install -e server[fastapi]`
  - `uvicorn fishtest.app:app --host 0.0.0.0 --port 8000`
  - FastAPI listens on `http://127.0.0.1:8000`.

- Run the same contracts against FastAPI:
  - `FISHTEST_BASE_URL=http://127.0.0.1:8000 python -m pytest server/tests/contract/test_worker_api_contract.py -q`
  - `FISHTEST_BASE_URL=http://127.0.0.1:8000 python -m pytest server/tests/contract/test_ui_auth_contract.py -q`

### Phase 2 — Shared-core extraction (minimum viable)

Deliverables:

- Shared adapters for validation, locking, and error shaping sufficient to implement worker endpoints without drifting behavior.

### Phase 3 — Implement remaining hard worker endpoints (shadow + compare)

For each remaining endpoint in the hard-first list:

- Implement FastAPI handler.
- Run contract tests against both implementations.
- Shadow/compare until diffs are essentially zero.

Exit criteria per endpoint:

- Contract tests stable.
- Shadow diffs essentially zero (or explicitly accepted).
- No lock-related regressions.

### Phase 4 — Canary cutover for worker endpoints

Deliverables:

- nginx routes one endpoint (or a restricted cohort) to FastAPI.
- Gradually expand by endpoint and/or percentage.

Rollback: revert that route immediately back to Pyramid.

### Phase 5 — Migrate public/web endpoints

After worker protocol stability:

- Migrate JSON read endpoints.
- Migrate streaming/download endpoints with strict header and streaming parity.

### Phase 6 — Migrate UI routes to FastAPI (required)

UI migration is required. Execute it incrementally using Appendix B as the checklist.

Recommended order:

- Read-only, public pages first.
- Authenticated read-only pages once AuthTkt parity is proven.
- Form POST pages once CSRF + flash/session parity is proven.

Exit criteria:

- Appendix B checklist completed.
- Forbidden/notfound behavior parity (HTML vs JSON expectations).
- No user-facing regressions during canary.

### Phase 7 — Decommission Pyramid (required end state)

Only when all required routes are migrated and stable:

- Remove Pyramid-only wiring.
- Simplify deployment to a single ASGI service.

---

## Immediate next actions

1. Implement the PoC in Section 3 behind path routing.
2. Start Phase 1: build worker contract tests (Appendix C) and run them against Pyramid.
3. Still in Phase 1: migrate one strict worker endpoint in shadow/compare and migrate `/login` + `/logout` in FastAPI to validate AuthTkt/session/CSRF.

---

## Appendix A — API inventory and classification (current `/api/...` surface)

Authoritative route list is in `server/fishtest/routes.py`. Handler mapping is from `server/fishtest/api.py`.

Legend:

- **Consumer**: `worker` (strict protocol) vs `public/web` (looser)
- **Auth**: `worker password` = `validate_username_password()`; `worker strict` = `validate_request()` (also checks `run_id/task_id/unique_key`)
- **R/W**: read vs write, from server state perspective
- **Response**: json / streaming / redirect
- **Hardness**: H/M/L (migration risk)

### Worker API (POST + JSON, strict auth, stateful)

| Path | Handler | Consumer | Method | Auth | R/W | Response | Hardness / why |
|---|---|---:|---:|---:|---:|---:|---|
| `/api/request_task` | `WorkerApi.request_task` | worker | POST | worker strict | write | json | **H**: run/task allocation, protocol-critical, coupled to `rundb.request_task()` |
| `/api/update_task` | `WorkerApi.update_task` | worker | POST | worker strict | write | json | **H**: core state transitions + stats ingestion; correctness-critical |
| `/api/failed_task` | `WorkerApi.failed_task` | worker | POST | worker strict | write | json | **H**: failure semantics feed scheduling/health |
| `/api/stop_run` | `WorkerApi.stop_run` | worker | POST | worker strict | write | json | **H**: authorization policy + `active_run_lock` + run lifecycle mutation |
| `/api/beat` | `WorkerApi.beat` | worker | POST | worker strict | write | json | **H**: uses `active_run_lock`, drives liveness/timeouts |
| `/api/request_spsa` | `WorkerApi.request_spsa` | worker | POST | worker strict | write/read | json | **H**: coupled to SPSA handler + task/run invariants |
| `/api/upload_pgn` | `WorkerApi.upload_pgn` | worker | POST | worker strict | write | json | **H**: large payload, base64+gzip validation, storage side effects |
| `/api/worker_log` | `WorkerApi.worker_log` | worker | POST | worker strict | write | json | **M**: side-effect into actions/logging; still worker-auth’d |
| `/api/request_version` | `WorkerApi.request_version` | worker | POST | worker password | read | json | **M**: bootstrap compatibility |

### Public/web API (mostly GET-ish, mixed payloads)

| Path | Handler | Consumer | Method | Auth | R/W | Response | Hardness / why |
|---|---|---:|---:|---:|---:|---:|---|
| `/api/rate_limit` | `UserApi.rate_limit` | public/web | GET | none | read | json | **L** |
| `/api/active_runs` | `UserApi.active_runs` | public/web | GET | none | read | json | **L** |
| `/api/finished_runs` | `UserApi.finished_runs` | public/web | GET | none | read | json | **L** |
| `/api/get_run/{id}` | `UserApi.get_run` | public/web | GET | none | read | json | **M**: CORS headers; externally consumed shape |
| `/api/get_task/{id}/{task_id}` | `UserApi.get_task` | public/web | GET | none | read | json | **M**: redactions/special cases |
| `/api/actions` | `UserApi.actions` | public/web | POST-ish | none | read | json | **M** |
| `/api/get_elo/{id}` | `UserApi.get_elo` | public/web | GET | none | read | json | **M** |
| `/api/calc_elo` | `UserApi.calc_elo` | public/web | GET | none | read | json | **L/M** |
| `/api/pgn/{id}` | `UserApi.download_pgn` | public/web | GET | none | read | streaming | **M**: streaming gzip response + headers |
| `/api/run_pgns/{id}` | `UserApi.download_run_pgns` | public/web | GET | none | read | streaming | **M**: iterator + headers |
| `/api/nn/{id}` | `UserApi.download_nn` | public/web | GET | none | read | redirect | **L/M**: redirect semantics |

---

## Appendix B — UI views inventory checklist (route-by-route)

Source of truth:

- Routes: `server/fishtest/routes.py`
- View configs + renderers: `server/fishtest/views.py`
- Templates: `server/fishtest/templates/*.mak`

Legend:

- **Auth**: `public` vs `login` vs `approver` (requires `approve_run` permission)
- **CSRF**: matches `require_csrf=...` on the Pyramid view

| Route name | Path | View callable | Renderer/template | Methods | CSRF | Auth | Notes / migration gotchas |
|---|---|---|---|---|---:|---|---|
| *(notfound)* | *(any 404)* | `notfound_view` | `notfound.mak` | GET | no | public | HTML 404 with JSON fallback behavior (keep JSON errors for `/api/...`). |
| *(forbidden)* | *(any 403)* | `login` (forbidden view) | `login.mak` | GET/POST | yes | public | Forbidden maps to login renderer; preserves `next` flow. |
| `home` | `/` | `home` | *(redirect)* | GET | no | public | Redirects to `/tests`. |
| `login` | `/login` | `login` | `login.mak` | GET/POST | yes | public | Uses `remember()`; redirects to `next`/`came_from`. |
| `logout` | `/logout` | `logout` | *(redirect)* | POST | yes | login | Calls `forget()` + session invalidation. |
| `signup` | `/signup` | `signup` | `signup.mak` | GET/POST | yes | public | reCAPTCHA flow; flashes. |
| `profile` | `/user` | `user` | `user.mak` | GET/POST | no | login | Updates profile; flashes. |
| `user` | `/user/{username}` | `user` | `user.mak` | GET/POST | no | login/approver | Viewing other users requires `approve_run`. |
| `user_management` | `/user_management` | `user_management` | `user_management.mak` | GET | no | approver | Permission-gated. |
| `contributors` | `/contributors` | `contributors` | `contributors.mak` | GET | no | public | Read-only. |
| `contributors_monthly` | `/contributors/monthly` | `contributors_monthly` | `contributors.mak` | GET | no | public | Read-only. |
| `actions` | `/actions` | `actions` | `actions.mak` | GET | no | public | Read-only, pagination. |
| `rate_limits` | `/rate_limits` | `rate_limits` | `rate_limits.mak` | GET | no | public | Low-risk informational page. |
| `sprt_calc` | `/sprt_calc` | `sprt_calc` | `sprt_calc.mak` | GET | no | public | Read-only tool. |
| `nns` | `/nns` | `nns` | `nns.mak` | GET | no | public | Read-only with filters/cookies. |
| `nn_upload` | `/upload` | `upload` | `nn_upload.mak` | GET/POST | yes | login | File upload + DB writes + flashes. |
| `workers` | `/workers/{worker_name}` | `workers` | `workers.mak` | GET/POST | yes | login/approver | Admin actions; CSRF; access control. |
| `tests` | `/tests` | `tests` | `tests.mak` | GET | no | public | Sets no-store headers and has pagination quirks. |
| `tests_finished` | `/tests/finished` | `tests_finished` | `tests_finished.mak` | GET | no | public | Read-only listing. |
| `tests_user` | `/tests/user/{username}` | `tests_user` | `tests_user.mak` | GET | no | public | no-store headers; page-dependent behavior. |
| `tests_machines` | `/tests/machines` | `tests_machines` | `machines.mak` | GET | no | public | Uses http_cache semantics. |
| `tests_view` | `/tests/view/{id}` | `tests_view` | `tests_view.mak` | GET | no | public | Complex read-only page; migrate very late if ever. |
| `tests_tasks` | `/tests/tasks/{id}` | `tests_tasks` | `tasks.mak` | GET | no | public | Read-only task list. |
| `tests_stats` | `/tests/stats/{id}` | `tests_stats` | `tests_stats.mak` | GET | no | public | 404 when run missing. |
| `tests_live_elo` | `/tests/live_elo/{id}` | `tests_live_elo` | `tests_live_elo.mak` | GET | no | public | SPRT-only; else 404. |
| `tests_run` | `/tests/run` | `tests_run` | `tests_run.mak` | GET/POST | yes | login | Run creation form; side effects; CSRF required. |
| `tests_modify` | `/tests/modify` | `tests_modify` | *(redirect)* | POST | yes | login | Modifies run state; locking + permissions. |
| `tests_stop` | `/tests/stop` | `tests_stop` | *(redirect)* | POST | yes | login | Stops run; permissions. |
| `tests_approve` | `/tests/approve` | `tests_approve` | *(redirect)* | POST | yes | approver | High side effects. |
| `tests_purge` | `/tests/purge` | `tests_purge` | *(redirect)* | POST | yes | login/approver | High side effects. |
| `tests_delete` | `/tests/delete` | `tests_delete` | *(redirect)* | POST | yes | login | Deletes run; side effects. |

Notes:

- Treat response headers (`Cache-Control`, `http_cache`, redirects) as behavioral contracts if migrating UI.
- High-risk UI: any POST route with side effects; migrate read-only pages first if you ever migrate UI.

---

## Appendix C — Worker API contract test matrix (validate_request + request_task/update_task/stop_run)

Ground truth:

- `WorkerApi.validate_username_password()` / `WorkerApi.validate_request()` in `server/fishtest/api.py`
- `api_access_schema`, `api_schema`, `worker_info_schema_api` in `server/fishtest/schemas.py`
- Endpoint logic in `WorkerApi.request_task`, `WorkerApi.update_task`, `WorkerApi.stop_run`

### C.0 Minimal valid worker request payload (schema)

All worker endpoints use `POST` + JSON and require:

- `password: str`
- `worker_info` with all required keys from `worker_info_schema_api` (notably: `username`, `unique_key`, `concurrency`, `version`, `near_github_api_limit`, etc.)

Schema rule to contract-test:

- If `task_id` is present but `run_id` is missing ⇒ `400`.

### C.1 Common contract cases (apply to every worker endpoint)

- Success response includes `duration` (float, >= 0).
- Error response includes `error` (string) and `duration`, with `error` prefixed by the request path.
- Non-JSON / invalid JSON ⇒ `400` with an error indicating the request is not JSON.
- Wrong password ⇒ `401`.
- `run_id` unknown ⇒ `400`.
- `task_id` invalid ⇒ `400`.
- `worker_info.unique_key` mismatch ⇒ `400`.
- `worker_info.username` mismatch ⇒ `400`.

### C.2 Endpoint-specific contract cases (minimum set)

#### C.2.1 `/api/request_task`

- Always returns `200` on logical outcomes (assigned/busy/blocked/rejected).
- Assigned response includes run/task fields and `duration`.

#### C.2.2 `/api/update_task`

- Normal update ⇒ `200`, includes `task_alive` + `duration`.
- Inactive task ⇒ `200`, `task_alive: false`, includes `info`.
- Rejected stats ⇒ `200`, `task_alive: false`, includes `error`.
- Invalid stats schema ⇒ `400`.

#### C.2.3 `/api/stop_run`

- Authorized ⇒ `200`.
- Not authorized (too few games) ⇒ `401`.
- Already finished ⇒ `401`.

### C.3 Sequence-level contracts (recommended)

- `request_task` then `update_task` with matching `(run_id, task_id, worker_info.username, worker_info.unique_key)` succeeds.
- Same `(run_id, task_id)` with different `unique_key` fails with `400`.

---

## Appendix D — Pyramid-only files isolated under `server/fishtest_pyr/`

After the package merge, the FastAPI service lives in `server/fishtest/` and the
Pyramid-only entrypoints were moved into `server/fishtest_pyr/`.

If you still need to run the legacy Pyramid server (WSGI), it now comes from:

- `server/fishtest_pyr/__init__.py` (Pyramid app factory; `paste.app_factory` entry point)
- `server/fishtest_pyr/routes.py`
- `server/fishtest_pyr/api.py`
- `server/fishtest_pyr/models.py`
- `server/fishtest_pyr/views.py`

The FastAPI app import path for local/dev runs is:

- `uvicorn fishtest.app:app --host 0.0.0.0 --port 8000`
