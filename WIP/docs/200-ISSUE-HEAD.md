# BADGE-FIX: Website Status Badge for GitHub README

## Scope of this review

This report was validated against:

- `WIP/docs/**` and `WIP/tools/**`
- `___fastapi` source/docs
- `___starlette` source/docs
- current workspace `git diff`

Goal: reliable up/down badge with minimal code, minimal ops burden, minimal server load.

## Problem

Badge URL:
`https://img.shields.io/website?url=https://tests.stockfishchess.org`

shows `down` while the site is live.

## Root cause (framework-level)

### 1. Shields behavior

Shields `website` badge uses `HEAD` and marks up only when status `< 310`.

Observed chain in production:

1. `HEAD /` -> nginx `308` to `/tests`
2. `HEAD /tests` -> `405 Method Not Allowed` (`Allow: POST, GET`)

So Shields reports `down`.

### 2. Why this changed with FastAPI migration

Pyramid historically treated HEAD as GET-without-body for GET endpoints.

Starlette `Route` still does this auto-add:

```python
# ___starlette/starlette/routing.py
if "GET" in self.methods:
    self.methods.add("HEAD")
```

FastAPI `APIRoute` does not:

```python
# ___fastapi/fastapi/routing.py
if methods is None:
    methods = ["GET"]
self.methods: set[str] = {method.upper() for method in methods}
```

No `HEAD` insertion is performed.

Net effect: function-based FastAPI routes respond 405 to HEAD unless explicitly handled.

## Additional framework evidence

### Starlette class-based endpoint fallback

`___starlette/starlette/endpoints.py` (`HTTPEndpoint`) maps HEAD to GET handler when `head()` is not defined.

This confirms Starlette supports spec-compliant HEAD behavior in class-based endpoints, but FastAPI function routes using `APIRoute` do not inherit that fallback.

## WIP docs/tools audit

### WIP docs

`WIP/docs/**` contains route inventories (`GET/POST`) and migration notes, but no explicit documented contract for HEAD parity on GET endpoints.

### WIP tools

`WIP/tools/**` includes route parity scripts and lint/test runners.

- `WIP/tools/lint_http.sh` exists and is authoritative for HTTP lint/type checks.
- parity tools normalize request methods, but do not assert HEAD behavior parity.

Conclusion: no existing tooling was enforcing GET->HEAD behavior, so regression escaped until badge checks exposed it.

## Code changes reviewed (git diff)

Changed runtime file in this iteration:

- `server/fishtest/http/middleware.py`

Key improvement from review:

- tightened ASGI typing by using `Message` in `send_no_body()`
- removed `type: ignore` use
- kept behavior minimal and explicit (drop body on HEAD path only)

No behavioral regressions found in reviewed patch logic.

## Implemented fix

Add `HeadMethodMiddleware` that:

1. intercepts HTTP `HEAD`
2. forwards internally as `GET`
3. strips only response body bytes on `http.response.body`

This restores RFC-compatible HEAD semantics for GET routes without touching every endpoint.

## Why this is the lowest-burden solution

1. No external uptime provider account/API key
2. No nginx-only fake health target
3. No route-by-route `@router.head(...)` boilerplate
4. No additional significant load

## Validation results

### Lint/type

Executed:

- `bash WIP/tools/lint_http.sh`
- `uv run ruff check fishtest/app.py --select ALL --fix`
- `uv run ty check fishtest/app.py`

Status:

- HTTP lint/type script: **pass**
- `app.py` ruff/ty: **pass**

### Tests

Executed:

- targeted middleware tests: `python -m unittest test_http_middleware -v`
- full suite discover: `python -m unittest discover -vb -s tests`

Results:

- middleware suite: **9/9 pass**
- full suite: **164 run, 0 failures (OK)**

## Direct runtime verification (no nginx)

Local uvicorn run (app-only path):

- command: `python -m uvicorn fishtest.app:app --host 127.0.0.1 --port 8765`
- env: `FISHTEST_INSECURE_DEV=1 FISHTEST_PORT=8765 FISHTEST_PRIMARY_PORT=8765`

Observed HEAD statuses:

- `HEAD http://127.0.0.1:8765/tests` -> `200`
- `HEAD http://127.0.0.1:8765/tests/finished` -> `200`
- `HEAD http://127.0.0.1:8765/api/active_runs` -> `200`
- `HEAD http://127.0.0.1:8765/api/calc_elo?W=1&D=0&L=0` -> `200`
- `HEAD http://127.0.0.1:8765/api/update_task` -> `405` (expected: POST-only endpoint)

Conclusion: middleware fix works in application code without nginx involvement.

## Deployment diagnosis for your dev host

Observed on `https://dfts-0.pigazzini.it`:

- `HEAD /` -> `308`
- `HEAD /tests` -> `405`
- `HEAD /api/calc_elo?W=1&D=0&L=0` -> `405`
- `HEAD /api/active_runs` -> `405`

Badge check:

- `https://img.shields.io/website?url=https%3A%2F%2Fdfts-0.pigazzini.it/tests`
- returns SVG label `website: down`

This mismatch (local 200 vs dev 405) means the deployed process is not running the middleware-enabled code yet (or is serving from a different instance/revision).

## Badge recommendation

After deploying HEAD middleware, keep simple Shields badge:

```markdown
[![Website](https://img.shields.io/website?url=https%3A%2F%2Ftests.stockfishchess.org)](https://tests.stockfishchess.org/tests)
```

This monitors live app reachability through your canonical URL, with minimal operational burden.

## Residual risk / open item

No code-level blocker remains for HEAD behavior in this branch. Remaining work is deployment alignment on the dev host.
