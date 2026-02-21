# Starlette references (for this project)

Date: 2026-02-11

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

### 8) Templates (Jinja2Templates)
- `Jinja2Templates` accepts either `directory` or `env`, not both.
- `TemplateResponse(request, name, context=...)` sets `request` in context and applies `context_processors`.
- `url_for` is injected via `pass_context` and uses the request in context.
- Context processors must be sync functions.
- Template responses expose `.template` and `.context` in tests.
## Session middleware internals (M11-critical)

### `SessionMiddleware` source analysis (from `___starlette/starlette/middleware/sessions.py`)

Starlette's `SessionMiddleware` is a pure ASGI middleware (~80 lines) that:
1. Reads the session cookie on request, unsigns it with `itsdangerous.TimestampSigner`
2. Stores session data as `scope["session"]` (a plain dict)
3. On response, re-signs and sets the cookie if the session dict is non-empty

**Cookie format**: `base64(json(session_data)).timestamp.hmac_signature`
- Uses `itsdangerous.TimestampSigner` (not `URLSafeTimedSerializer`)
- The middleware does its own `base64.b64encode(json.dumps(...))` before signing
- `TimestampSigner.unsign(data, max_age=...)` handles expiration
- Default `max_age` is 14 days (1,209,600 seconds)

**Key parameters**:
```python
SessionMiddleware(
    app,
    secret_key="...",           # Used to derive HMAC key
    session_cookie="session",   # Cookie name (configurable)
    max_age=14 * 24 * 60 * 60,  # 14 days, or None for browser-session
    path="/",
    same_site="lax",            # "lax" | "strict" | "none"
    https_only=False,           # Sets Secure flag
    domain=None,                # Optional domain for cross-subdomain
)
```

**Session commit behavior**:
- Session is always committed when `scope["session"]` is non-empty (truthy dict)
- Session is cleared (cookie deleted via expired Set-Cookie) when dict was non-empty on load but is now empty
- No "dirty" tracking — every response with session data sends a Set-Cookie header
- This means slightly more cookie traffic than the current `CookieSession.dirty` approach

**`request.session` access**: Available via `HTTPConnection.session` property which reads `scope["session"]`. Raises `AssertionError` if `SessionMiddleware` is not installed.

**Per-request max-age limitation**: The `max_age` is set once at middleware construction time. There is NO per-request max-age override. For "remember me" behavior:
- Option 1: Store a `remember_me: true` flag in the session; use a long default max-age; add a wrapper that checks the flag and overrides cookie max-age in the `send_wrapper`
- Option 2: Use two cookies (short-lived session + persistent "remember" token) — more complex
- Option 3: Fork/subclass SessionMiddleware to support per-request max-age via `scope["session_max_age"]`

**Recommended approach for fishtest**: Option 3 — a thin subclass (~20 lines) that checks `scope.get("session_max_age")` in the `send_wrapper` and uses it instead of `self.max_age` when set. The endpoint sets `request.scope["session_max_age"] = 365 * 24 * 3600` when "remember me" is checked.

### `itsdangerous` internals (session signing)

Starlette's `SessionMiddleware` uses `itsdangerous.TimestampSigner`:

**Signing format**: `value.base64(timestamp).base64(HMAC-SHA1(derived_key, value.base64(timestamp)))`
- Three dot-separated segments: payload, timestamp, signature
- Default algorithm: HMAC-SHA1 (secure for HMAC use; SHA-1 collision attacks don't apply)
- Default key derivation: `django-concat` = `sha1(salt + b"signer" + secret_key).digest()`
- Default salt: `b"itsdangerous.Signer"`
- Separator: `.` (period)
- Constant-time comparison via `hmac.compare_digest`

**`unsign(data, max_age=N)`**: Verifies HMAC, extracts timestamp, rejects if `age > max_age` or `age < 0` (clock skew protection).

**Key rotation**: `secret_key` can be a list — signs with newest, verifies trying all keys newest-to-oldest.

**vs. `URLSafeTimedSerializer`** (used by bloat branch):
- `TimestampSigner`: Signs raw bytes. Caller handles serialization.
- `URLSafeTimedSerializer`: Handles JSON serialization + optional zlib compression + signing in one call. Overkill when Starlette already does its own base64+JSON.

### Pure ASGI middleware pattern (M11 Phase 5)

The recommended pattern from Starlette docs for middleware that doesn't need `BaseHTTPMiddleware`:

```python
class MyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Pre-processing: inspect/modify scope
        # ...

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Modify response headers here
                headers = MutableHeaders(scope=message)
                headers.append("X-Custom", "value")
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

**Key rules**:
- State must be scoped to `__call__`, never `__init__` (stateless per request)
- Use `MutableHeaders(scope=message)` to modify response headers
- Guard on `scope["type"]` to skip non-HTTP connections
- For eager responses (redirects, 503s), create a `Response` and `await response(scope, receive, send)`

### `BaseHTTPMiddleware` limitations

From Starlette docs:
- Prevents `contextvars.ContextVar` changes from propagating upward
- Disrupts `contextvars` propagation for subsequent pure ASGI middleware
- No streaming response support (buffers entire response body)

For fishtest's middleware (M11 outcome):
- `ShutdownGuardMiddleware`: Pure ASGI `__call__` — **done** (M11 Phase 5)
- `RejectNonPrimaryWorkerApiMiddleware`: Pure ASGI `__call__` — **done** (M11 Phase 5)
- `AttachRequestStateMiddleware`: Pure ASGI `__call__` — **done** (M11 Phase 5)
- `RedirectBlockedUiUsersMiddleware`: Kept as `BaseHTTPMiddleware` — needs `Response` object for redirect logic; convert to pure ASGI is a future opportunity
- `FishtestSessionMiddleware`: Pure ASGI — **done** (M11 Phase 4), thin subclass pattern per Option 3 above
## Quick “use this when…” cheatsheet

- Cross-cutting guards: middleware (ASGI-first).
- Safe form parsing: request form limits → Requests.
- URL generation: `request.url_for` → Routing.
- Static URL building: `StaticFiles(name="static")` → StaticFiles.
- HTML error pages: exception handlers → Exceptions.
- Lifecycle correctness: lifespan + TestClient context manager → Lifespan + TestClient.
- Avoid event-loop blocking: use threadpool for sync work → Thread pool.

## Lessons from the bloated draft (what to adopt vs avoid)

What worked (small, safe patterns):
- Session migration plan: dual-read → dual-write → flip, with explicit tests for cookie name + flash/CSRF behavior.
- Keep middleware order explicit; run session middleware early so `request.session` is available everywhere.
- Keep `app.py` as the single assembly point and `include_router(...)` calls explicit and documented.

What to avoid:
- Removing template/session shims before templates are updated or covered by tests (this created regressions and extra indirection).
- Centralizing too much UI behavior in generic pipeline helpers when it makes per-route flow unreadable.
- Adding new middleware layers for concerns that can be expressed as dependencies.
- Spreading one route flow across many tiny modules purely for structure; prefer grouping by feature.
- Duplicating dependency helpers across modules; centralize dependency aliases to avoid naming drift.
