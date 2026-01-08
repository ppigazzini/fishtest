
# Fishtest architecture (repo snapshot)

Date: **2026-01-09**

This document describes the **current architecture of this repository**: what the major components are, how the server and worker interact, where the data lives, and what has changed (and *has not changed*) after the Pyramid → FastAPI replacement.

Scope:

- This is a **“what exists today”** snapshot.
- It is intentionally **implementation-oriented** (it names key modules and responsibilities).
- It does **not** attempt to be a migration plan (see `WIP/FASTAPI.md` for that).

Note on sources: this doc paraphrases common FastAPI concepts (routers, lifespan, ASGI) in the repository’s own words; it does not reproduce any third‑party documentation verbatim.

---

## 1) Big picture

Fishtest is a distributed testing system:

- A **server** assigns “tasks” for a chess-engine test run and ingests results.
- Many **workers** request tasks, run games, and report results.
- The server computes statistics and exposes both:
  - a machine-facing **API** (`/api/...`) and
  - a human-facing **UI** (HTML rendered from Mako templates).

### The invariants that matter (and mostly stayed unchanged)

1. **MongoDB is the system of record.**
	The data model and the `RunDb`/`UserDb`/… adapters remain central.

2. **Worker protocol correctness is sacred.**
	Workers are strict about payload shape and response shape.
	In particular, worker endpoints are “application-error tolerant”: they often return HTTP `200` with an `{"error": ...}` payload.

3. **UI is still Mako-template driven.**
	Most pages are rendered from `server/fishtest/templates/*.mak` with existing JS and CSS assets under `server/fishtest/static/`.

4. **Scheduling and “primary instance” behavior exists.**
	Only one server instance should run background scheduling and cache‑mutating tasks.

---

## 2) Repository structure

### Top-level layout (human map)

```
.
├─ server/                # Server code + server-side tests
│  ├─ fishtest/           # Current FastAPI service + shared logic (core)
│  ├─ fishtest_pyr/       # Legacy Pyramid code (historical; not deployed)
│  ├─ tests/              # Server unit tests (unittest)
│  ├─ utils/              # One-off maintenance/ops scripts
│  └─ development.ini     # Historical config (may no longer be used)
├─ worker/                # Worker implementation that talks to /api/*
├─ testing/               # Worker-side tests (separate harness)
├─ utils/                 # Repo-level helper scripts
└─ WIP/                   # Work-in-progress docs (this file lives here)
```

### Server package layout (core)

```
server/fishtest/
├─ app.py                 # FastAPI app factory + lifespan wiring
├─ settings.py            # Env parsing + derived runtime settings
├─ router.py              # Central router registration (order matters)
├─ errors.py              # Error handlers (API JSON vs UI HTML 404)
├─ run_form.py             # Pyramid-free run-creation helpers used by FastAPI UI
├─ api/
│  ├─ public.py           # Public/web API endpoints used by UI
│  └─ worker.py           # Worker protocol endpoints (/api/request_task, ...)
├─ views/                 # UI routes (FastAPI) rendering existing Mako templates
├─ templates/             # Mako templates (*.mak)
├─ static/                # CSS/JS/images served at /static
├─ rundb.py               # Core DB adapter and scheduling
├─ userdb.py              # Users, groups, auth decisions
├─ workerdb.py            # Worker registry, blocklists, etc
├─ actiondb.py            # Action logs and audit-style events
├─ kvstore.py             # Key/value “small data” store backed by Mongo
├─ stats/                 # SPRT, Elo, and statistical utilities
├─ schemas.py             # Validation schemas + helper computations
├─ cookie_session.py      # Cookie session + CSRF + flash for UI
└─ mako.py                # Template lookup and rendering helpers
```

### Legacy Pyramid layout (retired; not part of runtime)

```
server/fishtest_pyr/
├─ __init__.py            # Pyramid WSGI app factory (legacy)
├─ routes.py              # Pyramid route table (legacy)
├─ api.py                 # Pyramid API handlers (legacy)
├─ views.py               # Pyramid UI handlers (legacy)
└─ models.py              # RootFactory ACL setup (legacy)
```

These files are **retired**: they are not used by the running server. They are kept only as historical reference while the FastAPI implementation is validated and documented.

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

Key concepts used:

- **ASGI app**: `server/fishtest/app.py` exports `app = create_app()`.
- **Routers**: endpoints are grouped into routers and mounted via `app.include_router(...)`.
- **Router registration**: `server/fishtest/router.py` centralizes router inclusion and preserves the intended ordering.
- **Lifespan**: startup/shutdown is handled via a FastAPI lifespan context manager.

Data and service wiring:

- On startup, the server constructs a `RunDb` and attaches it to `app.state`:
  - `app.state.rundb`, `app.state.userdb`, `app.state.actiondb`, `app.state.workerdb`.
- Environment-derived runtime settings are parsed once and stored on `app.state.settings`.

Static files:

- `server/fishtest/app.py` mounts `server/fishtest/static/` at `/static`.

Static URL cache-busting:

- UI templates use a Pyramid-style helper (`request.static_url('fishtest:static/...')`).
- The FastAPI template request shim preserves Pyramid behavior by appending a stable query-string token:
	- `/static/<path>?x=<base64(sha384(file-bytes))>`
- This allows production deployments to set long-lived cache headers for `/static/` safely.

Reverse proxy note:

- If nginx serves static files from disk, it should serve the `/static/` namespace.
- Browsers also request some assets at the site root (not under `/static/`), notably `/robots.txt` and `/favicon.ico`; handle these explicitly in nginx if the app does not route them.

Error handling:

- Implemented in `server/fishtest/errors.py`.
- Installed by the app factory via `install_error_handlers(app)`.
- `/api/...` returns JSON errors (404 as JSON).
- UI routes return an HTML 404 page rendered from `notfound.mak` (and commit the cookie session).

Run creation form logic (FastAPI migration “glue”):

- Implemented in `server/fishtest/run_form.py`.
- This module keeps the historical run-validation and setup logic **out of the HTTP layer** so it can be reused while Pyramid endpoints are replaced with FastAPI endpoints.
- It operates on a small, duck-typed request contract (POST fields, `authenticated_userid`, `session.flash`, and DB handles like `userdb`/`rundb`).
- FastAPI UI handlers provide this contract via a tiny adapter (see the `_CompatRequest` / `_CompatSession` pattern in `server/fishtest/views/tests_manage.py`).

---

## 4) The “primary instance” concept

Fishtest has behaviors that must run on exactly one instance (background scheduling, cache mutation, periodic jobs).

In the FastAPI app (`server/fishtest/app.py`):

- `FISHTEST_PORT` and `FISHTEST_PRIMARY_PORT` are parsed in `server/fishtest/settings.py` and compared.
- If the instance is considered “primary”, it runs:
  - GitHub API initialization
  - `RunDb.update_aggregated_data()`
  - `RunDb.schedule_tasks()`

In worker endpoints (`server/fishtest/api/worker.py`):

- Mutating endpoints enforce “primary instance only” via `RunDb.is_primary_instance()`.
- When not primary, these endpoints typically return HTTP `503` with a JSON error payload.

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

Most of this logic is **carried over from the Pyramid era**: the migration is primarily replacing the HTTP layer while keeping the database-backed invariants intact.

---

## 6) API surface

There are two families of endpoints, with different “contracts”.

### 6.1 Worker protocol API (`/api/...`)

Location: `server/fishtest/api/worker.py`

Properties:

- **POST + JSON**.
- **Authenticates** via `userdb.authenticate(username, password)`.
- **Schema validation** via `vtjson` schemas in `server/fishtest/schemas.py`.
- **Response is always a JSON object** (dict), and includes a `duration` float.
- **Application-level errors** typically return **HTTP 200** with an `error` field in the JSON payload.
- **Transport/validation errors** return non-200 (e.g., 400 for invalid JSON/schema, 401 for wrong password).

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

Location: `server/fishtest/api/public.py`

This is the API the UI uses for reading state and downloading artifacts.
Examples include run listing, run detail, Elo calculations, downloading PGNs, etc.

---

## 7) UI surface (HTML)

Location:

- Routes: `server/fishtest/views/*`
- Templates: `server/fishtest/templates/*.mak`
- Static: `server/fishtest/static/*`

The UI has two layers:

1. **Route handlers** (FastAPI) that:
	- load a cookie session
	- gather data from `RunDb`/`UserDb`/…
	- render a Mako template
	- commit the session cookie

2. **Templates** (Mako) that expect a request-like object offering:
	- `request.session.get_csrf_token()`
	- `request.session.flash()` / `pop_flash()`
	- `request.authenticated_userid`
	- `request.static_url(...)`

The FastAPI implementation provides a small compatibility layer via:

- `server/fishtest/views/auth.py:TemplateRequest`
- `server/fishtest/cookie_session.py` (CSRF + flash support)

### 7.1 FastAPI “glue code” (Pyramid template compatibility)

Fishtest’s UI templates were originally written for Pyramid and expect a Pyramid-style request object and session API. The FastAPI server keeps the existing templates by providing a small set of compatibility shims.

Why these shims exist:

- The templates (notably `base.mak`) depend on Pyramid-style **flash messages** and **CSRF token** access (`request.session.*`).
- The templates also use Pyramid’s `request.static_url("fishtest:static/...")` asset notation.
- Rewriting all templates at once would be high-risk and provides little user value, so the shims preserve the existing template contract while the HTTP framework is FastAPI.

#### `server/fishtest/cookie_session.py`

Purpose: provide a minimal session implementation that satisfies template expectations without depending on Pyramid.

What it does:

- Stores session data client-side in a cookie named `fishtest_session`.
- Signs the cookie payload using HMAC (key derived from `FISHTEST_AUTHENTICATION_SECRET`).
- Implements:
	- CSRF token generation/rotation (`get_csrf_token()`, `new_csrf_token()`)
	- flash message queues (`flash()`, `peek_flash()`, `pop_flash()`)
- Provides helpers used by FastAPI routes:
	- `load_session(request)`
	- `commit_session(response=..., session=..., remember=..., secure=...)`
	- `clear_session_cookie(response=..., secure=...)`

Notes:

- Templates read the CSRF token both from the HTML meta tag in `base.mak` and from hidden form fields.
- Routes decide whether cookies are `Secure` using `fishtest.views.common.is_https()`.

#### `server/fishtest/mako.py`

Purpose: render the existing `server/fishtest/templates/*.mak` templates from FastAPI.

What it does:

- Builds a `TemplateLookup` rooted at the repository’s `server/fishtest/templates` directory.
- Uses `strict_undefined=False` to match Pyramid’s historical template behavior.
- Provides `render_template(lookup=..., template_name=..., context=...) -> RenderedTemplate`.

#### `server/fishtest/views/auth.py:TemplateRequest`

Purpose: provide a small “request-like” object passed to templates as `request`.

This object deliberately implements only the subset that templates currently rely on:

- `request.session`: a `CookieSession` (CSRF + flashes)
- `request.authenticated_userid`: current user name (or `None`)
- `request.cookies`, `request.headers`, `request.query_params`
- `request.GET`: Pyramid-compatible alias for query params
- `request.static_url("fishtest:static/...")`: maps old Pyramid asset specs to `/static/...`

The key idea: UI route handlers can stay small and predictable, while the templates remain largely unchanged.

---

## 8) What is mostly unchanged from Pyramid fishtest

It helps to separate “architecture” from “framework”:

### Unchanged (core architecture)

- The **database schema and collections** (MongoDB) and the surrounding adapters.
- Task assignment and ingestion logic in `RunDb`.
- The **worker protocol** (payload fields, error semantics, and response shape).
- Most templates, JS, and CSS assets.
- Statistical computations (SPRT/Elo utilities under `server/fishtest/stats/`).

### Changed (HTTP and app wiring)

- The server now has a first-class **FastAPI (ASGI)** entrypoint in `server/fishtest/app.py`.
- The UI and API are implemented as **routers** grouped by concern.
- Sessions for UI routes are now implemented without Pyramid (cookie session helper).

---

## 9) Tests and local development constraints

Server tests live in `server/tests/` and currently use `unittest`.

Important constraints:

- Many tests expect a working **MongoDB** on `localhost:27017`.
- FastAPI `TestClient` requires `httpx` (test-time dependency).

There is a lightweight test app factory in `server/tests/util.py` that wires routers directly onto a small `FastAPI()` instance, bypassing production lifespan side effects.

---
