# Starlette references (for this project)

Date: 2026-01-28

Curated **web-only** references and a short project-focused synthesis for the fishtest FastAPI refactor.

## Canonical web references (Starlette)

- Middleware: https://www.starlette.dev/middleware/
- Requests (including form parsing limits): https://www.starlette.dev/requests/
- Responses: https://www.starlette.dev/responses/
- Routing + url_for: https://www.starlette.dev/routing/
- StaticFiles: https://www.starlette.dev/staticfiles/
- Exceptions: https://www.starlette.dev/exceptions/
- Lifespan: https://www.starlette.dev/lifespan/
- TestClient: https://www.starlette.dev/testclient/
- Thread pool: https://www.starlette.dev/threadpool/

## Synthetic report — what matters for this project

### 1) Middleware ordering and correctness
- Keep middleware order explicit and justified.
- Prefer pure ASGI middleware when no response inspection is needed.

Snippet (ASGI middleware pattern):
```python
class MyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)
```

### 2) Session cookies and response plumbing
- Session cookies are set in middleware based on request state.
- Cookie attributes (samesite, secure, max_age) must remain stable for UI parity.

### 3) Request parsing limits (DOS protection)
- Use `request.form(max_files=..., max_fields=..., max_part_size=...)` for upload endpoints.

Snippet (Request.form limits):
```python
form = await request.form(max_files=1, max_fields=20, max_part_size=200 * 1024 * 1024)
```

### 4) URL generation and route naming
- Use `request.url_for(...)` for stable URL generation.
- Ensure all routes used by templates/helpers have explicit names.

### 5) Response classes
- Prefer `HTMLResponse` for UI endpoints and `JSONResponse` for API endpoints.
- Use `RedirectResponse` for redirect semantics so headers are explicit.

### 6) Lifespan and state
- Keep long-lived resources in `app.state` and initialize them in lifespan.
- TestClient should be used as a context manager to ensure lifespan runs.

### 7) Thread pool limits
- Sync functions and file I/O consume threadpool tokens; keep blocking DB and filesystem work off the event loop.

## Quick “use this when…” cheatsheet

- Cross-cutting guards: middleware (ASGI-first).
- Safe form parsing: request form limits → Requests.
- URL generation: `request.url_for` → Routing.
- Static URL building: `StaticFiles(name="static")` → StaticFiles.
- HTML error pages: exception handlers → Exceptions.
- Lifecycle correctness: lifespan + TestClient context manager → Lifespan + TestClient.
- Avoid event-loop blocking: use threadpool for sync work → Thread pool.
