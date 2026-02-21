> [!IMPORTANT]
> **Authoritative architecture baseline:** This document defines the current implemented architecture.
> The **authoritative migration/cutover contract** is [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md).

# Fishtest architecture (authoritative baseline)

Date: **2026-02-12**

(Last updated: **2026-02-12** — M12 Phase 6 parity remediation completed; SIGUSR1 support and parity tooling updates recorded)

This document describes the **current architecture of this repository**: what the major components are, how the server and worker interact, and where the data lives.

Scope:

- This is the **authoritative description of the implemented runtime architecture**.
- It is intentionally **implementation-oriented** (it names key modules and responsibilities).
- It does **not** duplicate migration planning (see [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md) for cutover contract details).

Note on sources: this doc paraphrases common FastAPI concepts (routers, lifespan, ASGI) in the repository’s own words; it does not reproduce any third‑party documentation verbatim.

---

## 1) Big picture

Fishtest is a distributed testing system:

- A **server** assigns “tasks” for a chess-engine test run and ingests results.
- Many **workers** request tasks, run games, and report results.
- The server computes statistics and exposes both:
  - a machine-facing **API** (`/api/...`) and
  - a human-facing **UI** (HTML rendered from Jinja2 templates).

### The invariants that matter (and mostly stayed unchanged)

> [!NOTE]
> **Protocol contracts** (worker API behavior, UI session semantics) are canonically documented in [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md).

1. **MongoDB is the system of record.**
   The data model and the `RunDb`/`UserDb`/… adapters remain central.

2. **Scheduling and "primary instance" behavior exists.**
   Only one server instance should run background scheduling and cache‑mutating tasks.

---

## 2) Repository structure

### Top-level layout (human map)

```
.
├─ server/                # Server code + server-side tests
│  ├─ fishtest/           # Current FastAPI service + shared logic (core)
│  ├─ tests/              # Server unit tests (unittest)
│  ├─ utils/              # One-off maintenance/ops scripts
│  ├─ pyproject.toml       # Server dependency manifest
│  └─ uv.lock              # Locked dependency set
├─ fishtest-pyramid/       # Upstream Pyramid code (reference)
├─ worker/                # Worker implementation that talks to /api/*
├─ testing/               # Worker-side tests (separate harness)
├─ utils/                 # Repo-level helper scripts
└─ WIP/                   # Work-in-progress docs (this file lives here)
```

### Server package layout (core)

```
server/fishtest/
├─ app.py                 # FastAPI app factory + lifespan wiring
├─ http/                  # FastAPI HTTP layer (mechanical port hotspots)
│  ├─ api.py              # ALL /api/... endpoints (worker + public)
│  ├─ views.py            # ALL UI endpoints (Jinja2 HTML)
│  ├─ errors.py           # Error shaping: API JSON vs UI HTML
│  ├─ boundary.py         # API request adapter + session commit helpers
│  ├─ middleware.py        # Pure ASGI middleware (shutdown guard, request.state, blocked-user redirect)
│  ├─ session_middleware.py# Pure ASGI session cookie middleware (itsdangerous TimestampSigner)
│  ├─ settings.py         # Env parsing + derived runtime settings
│  ├─ dependencies.py     # Typed dependency getters (RunDb/UserDb/etc)
│  ├─ cookie_session.py   # Dict-backed session wrapper (CSRF, flash, auth helpers)
│  ├─ csrf.py             # Shared CSRF validation helpers for UI POSTs
│  ├─ ui_errors.py        # UI error rendering helpers (404/403)
│  ├─ ui_context.py       # UI template context assembly helpers
│  ├─ ui_pipeline.py      # Cache-Control header helper
│  ├─ template_renderer.py# TemplateResponse adapter (template/context debug)
│  └─ jinja.py            # Jinja2 environment + static_url + render helpers
├─ templates/      # Jinja2 templates (*.html.j2) used at runtime
├─ static/                # CSS/JS/images served at /static
├─ rundb.py               # Core DB adapter and scheduling
├─ userdb.py              # Users, groups, auth decisions
├─ workerdb.py            # Worker registry, blocklists, etc
├─ actiondb.py            # Action logs and audit-style events
├─ kvstore.py             # Key/value “small data” store backed by Mongo
├─ stats/                 # SPRT, Elo, and statistical utilities
├─ schemas.py             # Validation schemas + helper computations
└─ util.py                # Shared utility helpers
```

---

## 3) Runtime architecture

### Deployment view

At runtime, the important pieces look like this:

```
                                         +-------------------+
                                         |      Browser      |
                                         |  HTML + JS + CSS  |
                                         +---------+---------+
                                                  |
                                                  | GET /tests/... (HTML)
                                                  | GET /static/... (assets)
                                                  | GET/POST /api/... (JSON/streams)
                                                  v
                                         +-------------------+
                                         |  FastAPI Server   |
                                         |  (ASGI process)   |
                                         +---------+---------+
                                                  |
                                                  | pymongo
                                                  v
                                         +-------------------+
                                         |      MongoDB      |
                                         |  runs/users/pgns  |
                                         +-------------------+

Workers (many processes) call the same FastAPI Server:

  +-------------------+         POST /api/request_task
  |      Worker       |  <----> POST /api/update_task
  | (worker/worker.py)|         POST /api/beat, ...
  +-------------------+
```

Typical production layouts put a reverse proxy (nginx) in front, but this repo keeps the “proxy layer” out of tree.

### Inside the server process

> [!NOTE]
> **Async/blocking boundaries** (event loop vs threadpool) are canonically documented in [2.1-ASYNC-INVENTORY.md](2.1-ASYNC-INVENTORY.md).

Key concepts used:

- **ASGI app**: `server/fishtest/app.py` exports `app = create_app()`.
- **Routers**: endpoints are grouped into routers and mounted via `app.include_router(...)`.
- **Router registration**: `server/fishtest/app.py` includes the routers in explicit order to preserve upstream behavior.
- **Lifespan**: startup/shutdown is handled via a FastAPI lifespan context manager and offloaded to a threadpool for blocking work.

Data and service wiring:

- On startup, the server constructs a `RunDb` in a threadpool and attaches it to `app.state`:
  - `app.state.rundb`, `app.state.userdb`, `app.state.actiondb`, `app.state.workerdb`.
- Environment-derived runtime settings are parsed once and stored on `app.state.settings`.

Request-time wiring:

- Middleware attaches commonly used objects onto `request.state`:
  - DB adapters (`request.state.rundb`/`userdb`/`actiondb`/`workerdb`)
  - request start time (`request.state.request_started_at`) used for worker `duration`
  - (UI only) blocked-user redirect checks use a short TTL cache and a threadpool lookup
- Route handlers should prefer typed FastAPI dependencies from `server/fishtest/http/dependencies.py` rather than reaching into `app.state` directly.

Static files:

- `server/fishtest/app.py` mounts `server/fishtest/static/` at `/static`.

Static URL cache-busting:

- UI templates use a legacy-style helper (`static_url('fishtest:static/...')`).
- The Jinja2 helper appends a stable query-string token:
  - `/static/<path>?x=<base64(sha384(file-bytes))>`
- The token is URL-safe and only computed for safe paths under `static/` (path traversal is rejected).
- Token computation is cached with bounded eviction to avoid repeated hashing.
- This allows production deployments to set long-lived cache headers for `/static/` safely.

Reverse proxy note:

- If nginx serves static files from disk, it should serve the `/static/` namespace.
- Browsers also request some assets at the site root (not under `/static/`), notably `/robots.txt` and `/favicon.ico`; handle these explicitly in nginx if the app does not route them.

Error handling:

- Implemented in `server/fishtest/http/errors.py`.
- Installed by the app factory via `install_error_handlers(app)`.
- The worker-endpoint set used for worker-style validation errors is derived from the HTTP API router to avoid drift.
- `/api/...` returns JSON errors (404 as JSON).
- UI 404/403 rendering is implemented in `server/fishtest/http/ui_errors.py` (called by `errors.py`).
- UI routes return an HTML 404 page rendered from `notfound.html.j2` (and commit the cookie session).

### Request flows (clean path)

UI HTML render flow:

1. FastAPI UI route enters `views.py` and calls `_dispatch_view()`.
2. View logic runs in a threadpool (sync upstream logic preserved).
3. `build_template_context()` assembles the shared base context.
4. `render_template_to_response()` returns a Starlette `TemplateResponse`.
5. Session cookies, cache headers, and response headers are applied.

Worker API JSON flow:

1. FastAPI API route parses JSON and builds the request shim.
2. `WorkerApi`/`UserApi` handler runs in a threadpool.
3. Responses are returned as JSON or streaming responses; error shaping stays in `http/errors.py`.

UI error flow:

1. Exception handler in `http/errors.py` chooses UI rendering for non-API paths.
2. `http/ui_errors.py` renders `notfound.html.j2` or `login.html.j2` via the same threadpool path.
3. Session cookies are committed before the response returns.

Signals and shutdown:

- `SIGUSR1` thread-dump is installed in the active server via `faulthandler.register(signal.SIGUSR1, all_threads=True)` with safe fallback logging when unavailable/already registered.
- `SIGINT`/`SIGTERM` are handled by Uvicorn. Cleanup steps are executed
  from FastAPI's lifespan shutdown and offloaded to a threadpool (stop scheduler, flush/save on primary, log a stop event).
- Once shutdown begins, the app rejects new requests with HTTP `503` (via the
  `rundb._shutdown` request guard).

Operational constraint enforcement:

- The primary server process is expected to run as a **single OS process**.
- On startup, the primary instance enforces single-worker mode: if `UVICORN_WORKERS`/`WEB_CONCURRENCY`
  is set to a value other than `1`, the app raises `RuntimeError` (multi-process configs are not safe
  for in-process locks/caches/scheduler semantics).

UI form POST behavior (CSRF + flash + redirects):

- Implemented in `server/fishtest/views.py` using helpers from `server/fishtest/http/cookie_session.py` and `server/fishtest/http/csrf.py`.
- Session data is loaded via `load_session()` (dict-backed, persisted by `session_middleware.py`).

---

## 4) The “primary instance” concept

Fishtest has behaviors that must run on exactly one instance (background scheduling, cache mutation, periodic jobs).

In the FastAPI app (`server/fishtest/app.py`):

- `FISHTEST_PORT` and `FISHTEST_PRIMARY_PORT` are parsed in `server/fishtest/http/settings.py` and compared.
- If the ports cannot be determined from the environment, the instance defaults to “primary” (fail-open).
- If the instance is considered “primary”, it runs:
  - GitHub API initialization
  - `RunDb.update_aggregated_data()`
  - `RunDb.schedule_tasks()`

In worker endpoints (`server/fishtest/api.py`):

- Several worker endpoints and UI mutation endpoints rely on `RunDb.buffer(...)`, which is only
  wired on the primary instance (see `RunDb.__init__`).
- In the FastAPI HTTP layer, primary-only worker endpoints are guarded in-process by
  `RejectNonPrimaryWorkerApiMiddleware`, which returns a stable HTTP `503` JSON error on
  misrouting. UI mutation routes marked `require_primary` are rejected with HTTP `503` by the
  shared UI dispatch pipeline.

Why this exists:

- The server has caches and background jobs that are **not safe** to run concurrently across multiple independent instances.

---

## 5) The core domain: runs, tasks, workers, results

### Data model (high level)

The data lives in MongoDB collections (names may be accessed through `RunDb`). The main conceptual entities:

- **Run**: a test definition with arguments (engine tags, time controls, book, throughput, SPRT/SPSA settings, etc.).
- **Task**: a unit of work assigned to a worker for a run; tracks progress, stats, last_updated, worker identity.
- **User**: account and permissions/groups (e.g., approvers).
- **Worker**: an agent machine identity, with optional block state.

### Where the logic lives

- `server/fishtest/rundb.py`: run lifecycle, task assignment, stats ingestion, scheduling, locks.
- `server/fishtest/userdb.py`: auth decisions, groups, blocked users.
- `server/fishtest/workerdb.py`: worker state and block/unblock actions.
- `server/fishtest/actiondb.py`: event logging (stop run, messages, system events).

Most of this logic is independent of the HTTP framework: the domain layer operates
on MongoDB documents and in-memory state, not on HTTP request/response objects.

---

## 6) API surface

There are two families of endpoints, with different “contracts”.

### 6.1 Worker protocol API (`/api/...`)

> [!NOTE]
> **Worker protocol contracts** (status codes, JSON shape, error semantics) are canonically documented in [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md).

Location: `server/fishtest/api.py`

Endpoints implemented here include:

- `/api/request_version`
- `/api/request_task`
- `/api/update_task`
- `/api/beat`
- `/api/request_spsa`
- `/api/failed_task`
- `/api/stop_run`
- `/api/upload_pgn`
- `/api/worker_log`

### 6.2 Public/web API (`/api/...`)

Location: `server/fishtest/api.py`

This is the API the UI uses for reading state and downloading artifacts.

Endpoints implemented here include:

- `GET /api/rate_limit`
- `GET /api/active_runs`
- `GET /api/finished_runs`
- `POST /api/actions` (+ `OPTIONS /api/actions`)
- `GET /api/get_run/{id}` (+ `OPTIONS /api/get_run/{id}`)
- `GET /api/get_task/{id}/{task_id}`
- `GET /api/get_elo/{id}`
- `GET /api/calc_elo`
- `GET /api/pgn/{id}`
- `GET /api/run_pgns/{id}`
- `GET /api/nn/{id}`

### 6.3 Endpoint routing matrix (multi-instance / multi-process)

> [!NOTE]
> **Deployment routing** (primary-only endpoints, nginx config) is canonically documented in [4-VPS.md](4-VPS.md).

This repo supports running **multiple FastAPI server processes** behind a reverse proxy, but not
all endpoints are safe to serve from any process. The main source of unsafety is **in-process state**
in `RunDb` (scheduler, in-memory maps/locks, and the primary instance `run_cache.buffer(...)` write path).

Routing criteria (for primary vs sticky vs default):

- **Primary-only**: endpoints that rely on `run_cache.buffer(...)`, the scheduler, or other in-process
  mutable state that is only safe on the primary instance.
- **Sticky (single backend)**: endpoints that must be consistent with local filesystem state or cache
  identity, but do not require primary.
- **Default**: everything else should fall through to the reverse proxy default.

Brief summary (see [4-VPS.md](4-VPS.md) for full routing matrix and nginx config):

- **Primary-only**: worker mutation endpoints that rely on primary-only state (`/api/request_task`, `/api/update_task`, etc.)
  and UI mutation endpoints.
  - Exception: `/api/upload_pgn` is routed to a non-primary backend for single-instance handling.
- **Single-instance (not necessarily primary)**: network upload (`/upload`), user management (for cache consistency)
  - `/upload` only writes network metadata to `nndb` and writes the file to `/var/www/fishtest/nn`.
    It does not touch `run_cache` or scheduler state, so it does not require primary.
- **Load-balance safe**: read-only endpoints

### 6.4 How `api.py` preserves worker-protocol behavior

The FastAPI server keeps `/api/...` behavior stable by adapting handler expectations inside `server/fishtest/api.py`.

Implementation highlights:

- **Request shim**: endpoints build a small request adapter object (shim) so handler code can use familiar fields such as `request.json_body`, `request.matchdict`, and a response object for `status_code`/headers.
- **JSON parsing parity**: invalid/missing JSON is detected in a way that preserves the worker-visible error semantics (workers often key off exact strings like “request is not json encoded”).
- **Threadpool boundary**: the "real work" (MongoDB access, locks, semaphores) stays synchronous. The FastAPI endpoint wrapper runs that synchronous handler in a threadpool so the event loop is not blocked.
- **Error shaping**: API exceptions and application errors are converted to the same JSON shapes the existing clients expect (worker endpoints often use HTTP `200` with an `error` field for application-level failures).
- **Streaming downloads**: Starlette `StreamingResponse` with file-like iteration performed via a threadpool iterator bridge so streaming does not block the event loop. Download headers (`Content-Disposition`, `Content-Length` when known) are preserved.
- **CORS/preflight**: where required, explicit `OPTIONS` handlers exist so browsers can preflight selected endpoints.

---

## 7) UI surface (HTML)

Location:

- Routes: `server/fishtest/views.py`
- Templates: `server/fishtest/templates/*.html.j2`
- Static: `server/fishtest/static/*`

The UI has two layers:

1. **Route handlers** (FastAPI) that:
   - load a cookie session
   - gather data from `RunDb`/`UserDb`/…
   - render a Jinja2 template
   - commit the session cookie

2. **Templates** (Jinja2) that expect a shared context offering:
  - `csrf_token`
  - `flash` queues (`error`/`warning`/`info`)
  - `current_user`
  - `static_url(...)`
  - `request` (for `url_for`, `query_params`, and cookies)

The FastAPI implementation provides a small compatibility layer via:

- `server/fishtest/http/cookie_session.py` (dict-backed CSRF + flash helpers)
- `server/fishtest/http/jinja.py` (`static_url` global)
- `server/fishtest/http/session_middleware.py` (cookie persistence)

### 7.1 FastAPI HTTP helpers (template context compatibility)

Fishtest's UI templates expect stable session and helper surfaces. The FastAPI server provides a small set of compatibility helpers to keep the existing templates unchanged.

Why these helpers exist:

- The templates (notably `base.html.j2`) depend on **flash messages** and **CSRF token** access.
- The templates also use the `static_url("fishtest:static/...")` asset notation.
- Rewriting all templates at once would be high-risk and provides little user value, so the helpers preserve the existing template contract.

### 7.2 How `views.py` implements the UI dispatch

UI routes use a data-driven dispatch pattern implemented in `server/fishtest/views.py`.

Mechanics:

- **Data-driven route registration**: UI routes are defined in a `_VIEW_ROUTES` list of `(function, path, config)` tuples. A registration function walks this list and calls `router.add_api_route()` for each entry.
- **Central dispatch pipeline**: instead of re-implementing session/CSRF/template logic per route, UI requests flow through a single dispatcher that:
  - loads the cookie session (dict-backed, persisted by `session_middleware.py`) and constructs the shared template context,
  - parses form data for POST,
  - enforces CSRF only where required,
  - runs the synchronous view handler in a threadpool,
  - handles `RedirectResponse` returns and `HTTPException` raises for control flow,
  - renders Jinja2 templates off the event loop (threadpool) and returns HTML,
  - applies response headers, cache controls, and session flags.

This is why UI routes return HTML error pages (404/login), redirects, and flash messages instead of FastAPI's default JSON validation errors.

### 7.4 M12 architectural decisions (explicit)

- Route registration model**: keep `_VIEW_ROUTES` + `_dispatch_view()` for UI endpoints in `views.py`.
  - Rationale: it centralizes 11 cross-cutting concerns (session, CSRF, template rendering, cache, response headers, etc.) and avoids repeating boilerplate across 28+ routes.
  - This is intentionally asymmetric with `api.py`, where endpoints are decorator-registered and do not require the same UI dispatch concerns.

- **Validation model**: keep vtjson as the protocol-validation source of truth.
  - Rationale: worker-protocol schemas rely on complex conditional/intersection validators, and broad Pydantic adoption would create dual-validation complexity without clear safety gain.
  - Pydantic remains optional for narrowly scoped future cases (Milestone N-1) only when it provides measurable net benefit.

### 7.5 Deployment note: session cutover impact

- M11 switched runtime session persistence to `itsdangerous.TimestampSigner` cookie format.
- Operational impact: pre-M11 session cookies are invalid after deployment, so users are logged out once and must re-authenticate.
- This is an accepted one-time cutover; no dual-read migration layer is required.

#### `server/fishtest/http/cookie_session.py`

Purpose: provide a minimal session implementation that satisfies template expectations.

What it does:

- Wraps a dict stored in `request.scope["session"]`.
- Implements:

  - CSRF token generation/rotation (`get_csrf_token()`, `new_csrf_token()`)
  - flash message queues (`flash()`, `peek_flash()`, `pop_flash()`)
- Provides helpers used by FastAPI routes:

  - `load_session(request)`
  - `mark_session_max_age(request, max_age)`
  - `mark_session_force_clear(request)`

Notes:

- Templates read the CSRF token both from the HTML meta tag in `base.html.j2` and from hidden form fields.
- Routes decide whether cookies are `Secure` using `fishtest.http.cookie_session.is_https()`.
- `FISHTEST_AUTHENTICATION_SECRET` must be set in production; an insecure dev fallback is only enabled via explicit opt-in (e.g. `FISHTEST_INSECURE_DEV=1`).
- Session cookie growth is capped; flash queues are trimmed deterministically to stay within cookie limits.

#### `server/fishtest/http/session_middleware.py`

Purpose: persist the session dict to a signed cookie using `itsdangerous.TimestampSigner`.

What it does:

- Pure ASGI middleware (`FishtestSessionMiddleware`) that reads/writes the `fishtest_session` cookie.
- On request: decodes the cookie, populates `scope["session"]` as a mutable dict.
- On response: re-encodes the session dict if non-empty, sets the `Set-Cookie` header.
- Supports per-request overrides via scope flags:
  - `session_max_age` — per-request `Max-Age` (for "remember me" login)
  - `session_secure` — per-request `Secure` flag
  - `session_force_clear` — force cookie deletion (logout)
- Enforces a max cookie size (`MAX_COOKIE_BYTES`); trims flash queues if the session exceeds the limit.

Notes:

- Cookie format: `base64(json(session)).timestamp.HMAC-SHA1-signature` (via `itsdangerous.TimestampSigner`).
- Cookie name is configurable (default: `fishtest_session`).
- `itsdangerous` is listed as an explicit dependency in `server/pyproject.toml`.
- Secret key supports lazy initialization via a callable.
- The per-request `max_age` override is the main divergence from Starlette's built-in `SessionMiddleware` (which only supports a single `max_age` at construction).

There is no runtime Mako renderer in the server package. Mako templates have been removed.

#### `server/fishtest/http/jinja.py`

Purpose: runtime renderer for `server/fishtest/templates`.

Notes:

- Jinja2 rendering uses Starlette `Jinja2Templates` with a custom `Environment` and autoescape enabled for `.html.j2`.
- UI rendering uses a unified response adapter that attaches `template` and `context` to responses for test/debug parity.

#### `server/fishtest/http/csrf.py`

Purpose: provide shared CSRF validation helpers for UI POST endpoints.

What it does:

- Centralizes the “extract token from request + compare to session token” logic.
- Keeps UI POST routes consistent and reduces per-endpoint boilerplate.

### 7.3 HTTP layer design

The HTTP layer is deliberately minimal: it provides the adapter surfaces that
handlers and templates rely on, nothing more.

- **Registration** — explicit router registration; UI uses a data-driven `_VIEW_ROUTES` list + `router.add_api_route()` calls.
- **Request/response objects** — two thin adapters: `_ViewContext` (UI views, provides session/DB/auth/URL access) and `ApiRequestShim` (API endpoints, provides `rundb`/`json_body`/`matchdict`/`params`), plus a shared template context (`csrf_token`, `flash`, `current_user`, `static_url`).
- **Exceptions and control flow** — uses native Starlette/FastAPI patterns: `RedirectResponse` for redirects, `raise HTTPException(status_code=404)` for not-found, `raise HTTPException(status_code=403)` for forbidden.
- **Streaming** — ASGI streaming (`StreamingResponse`) with iteration performed via a threadpool bridge.
- **Threading model** — handlers are invoked in a threadpool explicitly so blocking MongoDB/locking code runs without blocking the event loop. The async event loop accepts thousands of concurrent connections simultaneously, dispatching them as threadpool slots become available. The connection capacity is decoupled from the thread count.

Do **not** use Uvicorn's `--limit-concurrency` flag — it rejects excess connections with
HTTP 503 instead of queuing them (see [4-VPS.md](4-VPS.md) for the correct systemd template).

Production scaling configuration (see `docs/6-deployment.md` for full details):

- `--backlog 8192` absorbs reconnection bursts during restarts.
- `LimitNOFILE=65536` (systemd override) prevents fd exhaustion at high worker counts.
- `THREADPOOL_TOKENS = 200` (`app.py`) sizes the anyio threadpool for 9,400+ workers.
- nginx `keepalive 256` per upstream + `worker_connections 16384` match production capacity.

---

## 8) Architecture layers

It helps to separate "core architecture" from "HTTP wiring":

### Core (stable)

- The **database schema and collections** (MongoDB) and the surrounding adapters.
- Task assignment and ingestion logic in `RunDb`.
- The **worker protocol** (payload fields, error semantics, and response shape).
- Templates, JS, and CSS assets.
- Statistical computations (SPRT/Elo utilities under `server/fishtest/stats/`).

### HTTP wiring

- FastAPI (ASGI) entrypoint in `server/fishtest/app.py`.
- UI and API implemented as FastAPI routers in `views.py` and `api.py`.
- Cookie-based sessions (`fishtest_session`) via itsdangerous signing.

---

## 9) Tests and local development constraints

Server tests live in `server/tests/` and currently use `unittest`.

Important constraints:

- Many tests expect a working **MongoDB** on `localhost:27017`.
- FastAPI `TestClient` requires `httpx` (test-time dependency).

No `pyramid.*` imports exist in the codebase. All tests use the FastAPI `TestClient`
or domain-level unit tests.

There is a lightweight test app factory in `server/tests/test_support.py` that wires routers directly onto a small `FastAPI()` instance, bypassing production lifespan side effects.

### 9.1 Verification gates (no-tests workflow)

When making refactors in the FastAPI server code (especially routers/middleware/errors), the minimum verification bar used in this repo is:

- `cd server && uv run ruff check .`
- `cd server && uv run ty check fishtest/app.py fishtest/http/`

---
