- conventional commit
- autoritative message
- no history, no meta-message
- split at 80 chars
```
feat(server): replace Pyramid/Mako with FastAPI/Jinja2
Replace the Pyramid WSGI framework and Mako template engine with
FastAPI/Starlette (ASGI) and Jinja2. Deploy via Uvicorn instead
of Waitress.

Runtime stack:
- FastAPI + Starlette + Uvicorn (ASGI).
- Jinja2 templates (.html.j2, StrictUndefined).
- itsdangerous TimestampSigner cookie sessions.
- Pure ASGI middleware stack (6 layers: HEAD method, shutdown
  guard, request state, worker routing, blocked-user redirect,
  session).
- vtjson validation layer (19 schemas).

Server implementation:
- fishtest/api.py: 22 API routes (9 worker POST, 1 actions POST,
  10 read-only GET, 2 CORS OPTIONS).
- fishtest/views.py: 29 UI routes (data-driven _VIEW_ROUTES +
  centralized _dispatch_view pipeline).
- fishtest/http/: 15 support modules (session, CSRF, middleware,
  errors, dependencies, template helpers, boundary, settings).
- 26 Jinja2 templates with shared base context construction
  (build_template_context + template_helpers).
- Worker API: JSON responses with duration field, HTTP 200 for
  application errors, CORS for /api/actions and /api/get_run.
- UI pipeline: cookie sessions, CSRF enforcement, flash messages,
  per-route primary-instance guard, HTTP cache headers.

Build system:
- hatchling build backend for both server and worker.
- uv dependency management; single uv.lock at repo root.
- Pre-commit hooks: ruff lint + format, uv-lock check.
- CI workflows: lint, server tests (MongoDB), worker tests
  (POSIX + MSYS2).

Dependencies:
- Added: fastapi, uvicorn, itsdangerous, jinja2,
  python-multipart, hatchling, httpx (test).
- Removed: pyramid, pyramid-debugtoolbar, pyramid-mako, waitress,
  setuptools, mako.

Documentation:
- 10 docs in docs/ (architecture, threading model, API reference,
  UI reference, templates, worker, development guide, deployment
  with systemd + nginx configs, references).

Test suite:
- 164 tests (unittest discover) covering worker API, UI flows,
  HTTP boundary, middleware, session semantics, domain layer.
- Test helpers consolidated in test_support.py.

Breaking changes:
- Deployment entrypoint: `uvicorn fishtest.app:app` (was
  `pserve production.ini`).
- Session cookies invalidated on first deploy (itsdangerous
  TimestampSigner format); users re-authenticate once.
- Python >= 3.14 required (server); >= 3.8 (worker).
```
