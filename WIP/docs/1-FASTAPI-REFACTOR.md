# Restart refactor plan: Pyramid master → FastAPI (minimal-diff)

Date: 2026-02-12

This is the authoritative migration plan for the FastAPI cutover.

Notes:

- Files under `WIP/` are a mix of:
	- **Docs**: `WIP/docs/` (this plan is `WIP/docs/1-FASTAPI-REFACTOR.md`).
	- **Tools**: `WIP/tools/` (parity checks and other helper scripts).

**2026-02-12 implementation baseline:** runtime modules live under `server/fishtest/http/`; this document is maintained as the authoritative migration/cutover contract for the active codebase.

## Strategy (the “mechanical port” approach)

Treat upstream Pyramid code as the behavioral spec, and mechanically port the HTTP surface to FastAPI while keeping diffs small and localized.

Rules:

- Upstream Pyramid master behavior is the source of truth.
- No redesigns and no cleanup refactors.
- Prefer cohesive modules for endpoints; avoid extra handler layers that hide the flow.
- Keep merge hotspots limited to:
		- [server/fishtest/app.py](../../server/fishtest/app.py) (ASGI wiring)
		- [server/fishtest/api.py](../../server/fishtest/api.py) (ALL `/api/...` endpoints, in upstream order)
		- [server/fishtest/views.py](../../server/fishtest/views.py) (ALL UI endpoints, in upstream order)

Mechanical port means:

- Preserve URL paths, request/response shapes, and error messages.
- Preserve worker protocol invariants (notably `duration`, error prefixing with the path, and auth/validation behavior).
- Only change what the framework forces (decorators, request parsing, response types).

Additional rule (ASGI reality, still mechanical):

- Treat the FastAPI/Starlette event loop as a thin HTTP wrapper only.
	- Keep upstream logic synchronous where it already is.
	- Do not “async refactor” the upstream code.
	- Any blocking work (DB, file I/O, CPU-heavy rendering, network calls like `requests`) must run off the event loop (threadpool).

Parity rules that have proven important in practice:

- File-like downloads must stream correctly.
	- If upstream returns a file-like/app_iter, FastAPI must convert it to an iterator of bytes and stream it.
	- Reads must not happen on the event loop.
- CORS behavior must match what clients expect.
	- Some endpoints intentionally set CORS headers in upstream; browsers may require `OPTIONS` preflight responses.
	- Implementing explicit `OPTIONS` handlers is allowed when needed for parity.

## Protocols (parity gate checklist)

Date: 2026-01-21

This section enumerates the two external protocols that define Milestone 2 parity.
It is a checklist for contract tests and HTTP parity work.

### Protocol A — Worker API (/api/*)

**Scope:** endpoints used by `worker/` and other worker-like clients.
**Invariant:** every response is a JSON object and includes `duration` (float).
**Error rules:**
- **Application errors** return HTTP 200 with `{ "error": "...", "duration": ... }`.
- **Validation/transport errors** return non-200 with JSON error payloads (worker-compatible strings).

#### Endpoints to gate

1) `POST /api/request_version`
- Required status: 200
- Required shape: JSON object with `duration` and server version fields (as in spec).

2) `POST /api/request_task`
- Required status: 200 on success; 200 with `{error: ...}` for application-level issues
- Required shape: JSON object with `duration`, task payload fields as in spec.

3) `POST /api/update_task`
- Required status: 200 on success; 200 with `{error: ...}` for application-level issues
- Required shape: JSON object with `duration` and update status fields as in spec.

4) `POST /api/beat`
- Required status: 200 on success; 200 with `{error: ...}` for application-level issues
- Required shape: JSON object with `duration` and heartbeat response fields as in spec.

5) `POST /api/request_spsa`
- Required status: 200 on success; 200 with `{error: ...}` for application-level issues
- Required shape: JSON object with `duration` and SPSA params as in spec.

6) `POST /api/failed_task`
- Required status: 200 on success; 200 with `{error: ...}` for application-level issues
- Required shape: JSON object with `duration` and failure handling fields as in spec.

7) `POST /api/stop_run`
- Required status: 200 on success; 200 with `{error: ...}` for application-level issues
- Required shape: JSON object with `duration` and stop confirmation fields as in spec.

8) `POST /api/upload_pgn`
- Required status: 200 on success; 200 with `{error: ...}` for application-level issues
- Required shape: JSON object with `duration` and upload confirmation fields as in spec.

9) `POST /api/worker_log`
- Required status: 200
- Required shape: JSON object with `duration` and log-ack fields as in spec.

### Protocol B — UI (browser-visible behavior)

**Scope:** HTML UI flows and UI error behavior.
**Invariant:** UI routes return HTML templates (not JSON) for 404/403.

#### Flows to gate

1) **Login / Logout**
- Login GET renders HTML with CSRF token available in meta tag and form field.
- Login POST enforces CSRF and sets the session cookie on success.
- Logout clears the session cookie and redirects appropriately.

2) **403 / 404 rendering**
- UI route forbidden returns HTML (template-rendered) with session cookie committed.
- UI route not found returns HTML 404 template (not JSON).

3) **Representative read-only pages**
- One list page (e.g., `/tests` or `/contributors`) renders HTML and loads static assets.
- One detail-ish page (e.g., `/tests/view/{id}`) renders HTML with expected template vars.

#### Cookie / CSRF / redirect expectations

- Session cookie uses the FastAPI cookie-session shim and persists across UI requests.
- CSRF token must be validated for UI POSTs that require it.
- Redirect status codes and `Location` headers match the Pyramid-era behavior.

## Read-only reference trees

These folders are reference-only and MUST NOT be modified:

- [fishtest-pyramid/](../../fishtest-pyramid) — upstream Pyramid master snapshot (read-only)
- [fishtest-fastapi-draft/](../../fishtest-fastapi-draft) — first FastAPI draft snapshot (read-only)
- [fishtest-fastapi-draft/WIP/](../../fishtest-fastapi-draft/WIP) contains docs from the first draft.

## Current status (authoritative)

Completed:

- Single ASGI entrypoint at [server/fishtest/app.py](../../server/fishtest/app.py).
- Mechanical port hotspots are in place and mounted:
	- [server/fishtest/api.py](../../server/fishtest/api.py)
	- [server/fishtest/views.py](../../server/fishtest/views.py)
- UI error rendering parity is centralized in [server/fishtest/http/ui_errors.py](../../server/fishtest/http/ui_errors.py):
	- `render_notfound_response()`
	- `render_forbidden_response()`
- Central error handler delegates UI 404/401/403 rendering via [server/fishtest/http/errors.py](../../server/fishtest/http/errors.py), which now calls into [server/fishtest/http/ui_errors.py](../../server/fishtest/http/ui_errors.py) instead of UI views.
- UI rendering attaches response metadata (`template`, `context`) for debug/test parity via the unified response adapter.
- UI rendering now runs Jinja2-only at runtime (TemplateResponse path). Mako templates and parity tooling have been retired.
- Production deployment scaffolding exists (systemd + nginx), see [4-VPS.md](4-VPS.md).
- Milestone 3 async/blocking boundaries are complete; see 2.1-ASYNC-INVENTORY.md for the inventory and invariants.
- Lifespan startup/shutdown work and blocked-user lookup are offloaded to the threadpool.
- Milestone 12 Phase 6 parity remediation is complete: dead API helpers/assertions removed, `InternalApi` parity stub restored, `ensure_logged_in()` contract documented, SIGUSR1 thread dump support installed, and parity tooling now checks required API class presence.
- Milestone 13: Pyramid spec modules (`api.py`, `views.py`, `models.py`) and test stubs (`tests/pyramid/`) deleted. Route modules moved from `http/` to top-level. Mako templates deleted. Parity tooling retired to `WIP/tools/retired`.

CI/testing status:

- Unit tests run without any Pyramid dependency. The Pyramid-era spec modules and test stubs have been removed.
- There is intentionally no runtime `pyramid` package in the server app.

## Goals

- One-step deployment switch to FastAPI (single server process).
- Preserve worker protocol correctness.
- Preserve UI behavior and templates with minimal or zero template edits.

Operational goal (what “switch” means):

- Replace Pyramid as the HTTP server, not the domain logic.
	- Same DB layer (`RunDb` et al), same templates/static, same URLs.
	- The risk is isolated to HTTP adaptation + request/session/CSRF plumbing.

## Non-goals

- No MongoDB/schema redesign.
- No scheduling/model refactors.
- No dependency changes unless explicitly approved.

## Tooling rules (strict)

- Python version target: **Python 3.14+**.
- `ruff`/`ty` apply only to HTTP support modules (middleware/errors/session/template shims) and any new tests.
- [server/fishtest/api.py](../../server/fishtest/api.py) and [server/fishtest/views.py](../../server/fishtest/views.py) are treated as mechanical ports of upstream HTTP-layer code:
	- Do not add type hints.
	- Do not run `ruff`/`ty` on them (and do not reformat them) unless explicitly decided later.
- Prefer `uv run ...` for checks and tests.

Note: Pyramid-era spec modules and test stubs have been removed. The `api.py` and `views.py` at the top level are now the active FastAPI modules.

## What stays stable (constraints)

- The deployed entrypoint is `uvicorn fishtest.app:app`.
- All UI routes come from [server/fishtest/views.py](../../server/fishtest/views.py).
- All `/api/...` routes come from [server/fishtest/api.py](../../server/fishtest/api.py).
- `fishtest-fastapi-draft/` and `fishtest-pyramid/` are read-only.

## Cutover architecture (big picture)

Target runtime shape:

- Single ASGI app: [server/fishtest/app.py](../../server/fishtest/app.py)
	- Installs middleware + error handlers.
	- Mounts UI router ([server/fishtest/views.py](../../server/fishtest/views.py)) and API router ([server/fishtest/api.py](../../server/fishtest/api.py)).
	- Serves `/static` from the existing static tree.

Sync/async boundary:

- UI view bodies are synchronous upstream code and run in a threadpool.
- API handler bodies are synchronous upstream code and run in a threadpool.
- Only the FastAPI route wrappers + minimal request parsing are async.

Deployment (already in place):

- systemd service template and nginx reverse-proxy routing are already defined in [4-VPS.md](4-VPS.md).
	- This includes `uvicorn fishtest.app:app` behind nginx with forwarded/proxy headers enabled.
	- It also documents a primary/secondary split implemented purely at the reverse-proxy layer (nginx `map` routing).
		- Treat this as a deployment optimization / traffic-shaping detail.
		- It is not a requirement of the application architecture: the codebase still builds one ASGI app with one HTTP surface; “primary vs secondary” is driven by runtime settings.

Local dev (still flexible):

- Run in a way that makes the `fishtest` package importable from `server/`.
	- Example patterns: editable install of `server/`, or running with `PYTHONPATH=server`.

## Signals and shutdown (runtime behavior)

- `SIGINT`/`SIGTERM`: handled by Uvicorn; Fishtest cleanup runs from FastAPI lifespan shutdown in [server/fishtest/app.py](../../server/fishtest/app.py) via `_shutdown_rundb()` (mirrors Pyramid-era shutdown semantics).
- Shutdown guard: once `rundb._shutdown` is set, [server/fishtest/http/middleware.py](../../server/fishtest/http/middleware.py) `ShutdownGuardMiddleware` rejects new requests with HTTP `503`.
- Single-worker primary: [server/fishtest/app.py](../../server/fishtest/app.py) `_require_single_worker_on_primary()` raises `RuntimeError` if `UVICORN_WORKERS`/`WEB_CONCURRENCY` indicates workers != `1` on the primary instance.
- `SIGUSR1` thread-dump: installed in the active server via `faulthandler.register(signal.SIGUSR1, all_threads=True)` with safe fallback logging when unavailable/already registered.

## Acceptance criteria (definition of “done”)

- FastAPI serves the expected route surface.
- Worker endpoints always return JSON dicts with `duration` (including errors), matching the Pyramid behavior.
- API/UI behavior matches Pyramid for the migrated surface (paths, status codes, response shapes, templates).
- Diffs to upstream/master logic remain minimal and intentional (no formatting-only churn).

## Ongoing practice

- Keep parity checks green for routes, AST, HTML output, and response metadata.
- Preferred one-command parity gate (default informational mode for known template response diffs):
	- `WIP/tools/run_parity_all.sh`
- Strict mode (fail on any normalized response-template mismatch):
	- `WIP/tools/run_parity_all.sh --strict`
- Prefer changes in the HTTP hotspots over adding new endpoint modules.
