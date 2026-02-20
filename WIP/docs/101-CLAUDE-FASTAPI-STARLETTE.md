# FastAPI vs Starlette: Framework Analysis for Fishtest

## Scope

This report analyzes the actual framework dependency in the fishtest
server codebase. It was produced by examining:

- `___fastapi/fastapi/` source (18,576 lines)
- `___starlette/starlette/` source (6,536 lines)
- `___jinja/src/jinja2/` source (14,351 lines)
- `server/fishtest/` project source (12,959 lines)
- `docs/` (10 documentation files)
- `WIP/docs/` (30+ refactoring notes and iteration records)

Goal: determine what this project actually uses from FastAPI, what it
uses from Starlette directly, and whether the dependency on FastAPI
is justified.

---

## 1. What Starlette provides

Starlette is a **complete ASGI web framework** in ~6,500 lines. It
provides everything needed to build a production web application with
no additional framework layer.

### Core features (all usable without FastAPI)

| Category | Feature |
|----------|---------|
| Application | `Starlette()` ASGI app, lifespan, `app.state` |
| Routing | `Route`, `Router`, `Mount`, `Host`, path convertors (`{id:int}`) |
| HTTP methods | Auto HEAD for GET routes, 405 with `Allow` header |
| Class views | `HTTPEndpoint` with per-method dispatch (HEAD→GET fallback) |
| Request | `Request` — headers, cookies, query params, JSON, form, streaming |
| Response | `Response`, `HTMLResponse`, `JSONResponse`, `RedirectResponse`, `StreamingResponse`, `FileResponse` |
| Templates | `Jinja2Templates` with `url_for` and context processors |
| Static files | `StaticFiles` with ETag, Range, conditional 304 |
| Sessions | `SessionMiddleware` with `itsdangerous` signing |
| Auth | `AuthenticationMiddleware` + `AuthenticationBackend` + `@requires` |
| CORS | `CORSMiddleware` (preflight, origin regex, credentials) |
| Compression | `GZipMiddleware` |
| Security | `TrustedHostMiddleware`, `HTTPSRedirectMiddleware` |
| Error handling | `ServerErrorMiddleware` (debug), `ExceptionMiddleware` (status handlers) |
| Concurrency | `run_in_threadpool()`, `iterate_in_threadpool()` |
| Testing | `TestClient(httpx.Client)` with lifespan support |
| WebSockets | `WebSocket`, `WebSocketRoute`, `WebSocketEndpoint` |
| Config | `Config`/`Environ` for env var / `.env` file reading |
| OpenAPI | `SchemaGenerator` (lightweight, docstring-based) |
| Types | `ASGIApp`, `Scope`, `Receive`, `Send`, `Message` |
| Middleware | Pure ASGI middleware pattern (preferred over `BaseHTTPMiddleware`) |

### Key differentiators

- **HEAD auto-handling**: `Route.__init__` adds HEAD to methods when
  GET is present. `HTTPEndpoint` falls back HEAD→`get()`.
- **Per-route middleware**: `Route(..., middleware=[...])` — not
  available in FastAPI's `APIRoute`.
- **No Pydantic dependency**: zero runtime dependency on Pydantic.
- **Minimal codebase**: 6,536 lines vs FastAPI's 18,576 lines (2.8×
  smaller).

---

## 2. What FastAPI adds on top of Starlette

FastAPI is a **layer over Starlette** that adds developer ergonomics
for API-first projects. The `FastAPI` class inherits from
`starlette.applications.Starlette`.

### FastAPI-exclusive features (not in Starlette)

| Feature | Lines | Purpose |
|---------|-------|---------|
| Dependency injection | ~1,000 | `Depends()` with recursive resolution, caching, generator scoping |
| Pydantic validation | ~2,000 | Automatic request param extraction + validation via type hints |
| OpenAPI generation | ~2,500 | Full OpenAPI 3.1.0 schema from code, Swagger UI + ReDoc |
| `response_model` | ~500 | Pydantic-based response filtering and serialization |
| `Body/Query/Path/Header/Cookie` | ~400 | Parameter declaration with OpenAPI metadata |
| Security schemes | ~600 | OAuth2, API Key, HTTP Basic/Bearer — as callable DI dependencies |
| `jsonable_encoder()` | ~200 | Universal Python→JSON conversion via Pydantic |
| `APIRouter` | ~800 | Router with prefix merging, tags, dependency inheritance |

### Pure re-exports from Starlette (no added value)

FastAPI re-exports these Starlette classes unchanged:

`StaticFiles`, `Jinja2Templates`, `TestClient`, `Request`,
`HTTPConnection`, `WebSocket`, `WebSocketDisconnect`, `Response`,
`JSONResponse`, `HTMLResponse`, `PlainTextResponse`,
`RedirectResponse`, `StreamingResponse`, `FileResponse`, `Middleware`,
`Mount`, `run_in_threadpool`, `iterate_in_threadpool`, `URL`,
`Address`, `FormData`, `Headers`, `QueryParams`, `State`, `status`
module (all HTTP codes).

Importing these from `fastapi` or `starlette` yields the
**identical class**.

### Known behavioral gaps introduced by FastAPI

| Issue | Starlette behavior | FastAPI behavior |
|-------|-------------------|-----------------|
| HEAD on GET routes | Auto-added by `Route.__init__` | **Not added** by `APIRoute.__init__` |
| Per-route middleware | `Route(..., middleware=[...])` | **Not available** on `APIRoute` |
| `super().__init__()` | N/A | `APIRoute` and `FastAPI` do not call `super().__init__()` — manually replicate parent logic |

The HEAD gap required the `HeadMethodMiddleware` workaround
documented in `200-ISSUE-HEAD.md`.

---

## 3. What fishtest actually uses

### Import analysis (runtime, excluding TYPE_CHECKING)

| Source | Import count | Examples |
|--------|-------------|---------|
| `from fastapi` | 17 | `FastAPI`, `APIRouter`, `HTTPException`, `Request`, `Depends`, `JSONResponse`, `HTMLResponse`, `RedirectResponse`, `StreamingResponse`, `StaticFiles`, `RequestValidationError` |
| `from starlette` | 27 | `run_in_threadpool`, `iterate_in_threadpool`, `Request`, `HTTPConnection`, `MutableHeaders`, `JSONResponse`, `PlainTextResponse`, `RedirectResponse`, `Jinja2Templates`, `Route`, `ASGIApp`, `Scope`, `Message`, `Receive`, `Send` |

### FastAPI-exclusive feature usage

| Feature | Used? | Evidence |
|---------|-------|---------|
| `FastAPI()` class | **Yes** | `app.py`: `FastAPI(lifespan=..., openapi_url=...)` |
| `APIRouter` | **Yes** | 2 routers: `api.py` (22 routes), `views.py` (29 routes) |
| `Depends()` | **Declared only** | `boundary.py` defines 3 dep aliases; no route signature injects them |
| `response_model` | **No** | 0 occurrences |
| `Body/Query/Path` | **No** | 0 occurrences |
| `BaseModel` (Pydantic) | **No** | 0 occurrences; vtjson is the sole validator (19 schemas) |
| OpenAPI docs | **Disabled** | `openapi_url` defaults to `None`; dev-only via env var |
| `jsonable_encoder` | **No** | 0 occurrences |
| Security schemes | **No** | Custom auth via session cookies + vtjson |
| `RequestValidationError` | **Handler only** | Registered as fallback; no route triggers it (no Pydantic models) |

### What fishtest actually depends on at runtime

**From FastAPI** (truly exclusive):
1. `FastAPI()` app class (vs `Starlette()`)
2. `APIRouter` with `.get()` / `.post()` decorators and `.add_api_route()`
3. Two fallback exception handlers from `fastapi.exception_handlers`

**From Starlette** (used directly):
1. `run_in_threadpool`, `iterate_in_threadpool` — async/sync bridge
2. `Request`, `HTTPConnection` — request objects
3. `JSONResponse`, `HTMLResponse`, `PlainTextResponse`, `RedirectResponse`,
   `StreamingResponse` — all response types
4. `Jinja2Templates` — template engine integration
5. `MutableHeaders` — response header manipulation
6. `Route` — used in error handler routing logic
7. ASGI types: `ASGIApp`, `Scope`, `Receive`, `Send`, `Message`
8. Pure ASGI middleware pattern (6 middleware classes)
9. `StaticFiles` (imported through FastAPI namespace)

**From neither** (project-original):
- `FishtestSessionMiddleware` — custom cookie session (itsdangerous)
- vtjson validation (19 schemas, no Pydantic)
- `_dispatch_view()` — centralized UI dispatch pipeline
- All domain logic (`rundb`, `userdb`, `actiondb`, `workerdb`)

---

## 4. Fishtest as a framework — feature inventory

### What this project built (beyond framework defaults)

| # | Feature | Module | Pattern |
|---|---------|--------|---------|
| 1 | 6-layer pure ASGI middleware stack | `http/middleware.py`, `http/session_middleware.py` | Starlette ASGI |
| 2 | HEAD→GET middleware (RFC 9110 §9.3.2) | `http/middleware.py` | Starlette ASGI |
| 3 | itsdangerous signed cookie sessions | `http/cookie_session.py`, `http/session_middleware.py` | Custom (Starlette pattern) |
| 4 | CSRF protection (timing-safe) | `http/csrf.py` | Custom |
| 5 | vtjson schema validation (19 schemas) | `schemas.py` | Custom (no Pydantic) |
| 6 | Centralized UI dispatch pipeline | `views.py` `_dispatch_view()` | Custom |
| 7 | Data-driven route registration | `views.py` `_VIEW_ROUTES` | Custom |
| 8 | Dual error routing (JSON API vs HTML UI) | `http/errors.py` | Custom |
| 9 | Run cache with dirty-page flush | `run_cache.py` | Custom |
| 10 | Periodic task scheduler (primary-only) | `scheduler.py` | Custom |
| 11 | Primary/secondary instance routing | `http/middleware.py` | Custom |
| 12 | Worker protocol (9 POST + duration field) | `api.py` | Custom |
| 13 | Template context contracts (26 templates) | `http/boundary.py`, `http/template_helpers.py` | Custom |
| 14 | Cache-busting static URLs (SHA-384) | `http/jinja.py` | Custom |
| 15 | Blocked-user redirect middleware | `http/middleware.py` | Starlette ASGI |
| 16 | Shutdown guard middleware | `http/middleware.py` | Starlette ASGI |
| 17 | Flash message system (session-based) | `http/cookie_session.py` | Custom |
| 18 | HTTP cache header pipeline | `http/ui_pipeline.py` | Custom |
| 19 | PGN streaming (threadpool iterator) | `api.py` | Starlette `iterate_in_threadpool` |
| 20 | GitHub API integration | `github_api.py` | Custom |

### Patterns that are Starlette, not FastAPI

The project's architecture is fundamentally a Starlette application:

- All middleware is pure ASGI (`__call__(scope, receive, send)`)
- Session handling is custom (not FastAPI's DI)
- Auth is custom (not FastAPI's security schemes)
- Validation is vtjson (not Pydantic)
- Templates use `Jinja2Templates` directly (Starlette's class)
- Streaming uses `iterate_in_threadpool` (Starlette's function)
- All blocking work uses `run_in_threadpool` (Starlette's function)
- Error handlers use `starlette.routing.Route` for path matching

---

## 5. Two perspectives

### Perspective A: Keep FastAPI (status quo)

**Pros:**

1. **Familiar name recognition.** FastAPI has 80k+ GitHub stars and
   wide community adoption. Contributors joining the project
   recognize it immediately.
2. **`APIRouter` convenience.** Decorator-style route registration
   (`@router.get("/path")`) is more concise than Starlette's
   `Route("/path", handler, methods=["GET"])` list.
3. **`add_api_route()` for data-driven registration.** The
   `_VIEW_ROUTES` table uses this method to register 29 UI routes
   programmatically — cleaner than building `Route()` objects
   manually.
4. **Future optionality.** If the project later adopts Pydantic
   validation, OpenAPI docs, or dependency injection, FastAPI is
   already wired in. No migration needed.
5. **Exception handler integration.** The two fallback handlers from
   `fastapi.exception_handlers` provide a safety net for unexpected
   validation errors.
6. **OpenAPI in development.** Setting `OPENAPI_URL=/openapi.json`
   instantly gives contributors Swagger UI for exploring the API
   during development, at zero code cost.
7. **Ecosystem compatibility.** FastAPI is tested against Starlette
   releases. Using both prevents version-skew surprises that could
   occur with a manual Starlette-only setup.
8. **Low migration risk.** The status quo works. 164 tests pass.
   9,400+ concurrent workers proven in production. Changing the
   framework layer introduces risk for no user-facing benefit.

**Cons:**

1. **Unused complexity.** FastAPI's 18,576 lines exist in the
   dependency tree but ~80% is unused (DI engine, Pydantic
   integration, OpenAPI generator, security schemes).
2. **Behavioral regressions.** The HEAD method gap was a direct
   consequence of using FastAPI's `APIRoute` instead of Starlette's
   `Route`. This required a custom middleware fix.
3. **Pydantic runtime dependency.** FastAPI pulls in Pydantic
   (~35,000 lines) as a transitive dependency even though the
   project uses vtjson exclusively.
4. **Abstraction leak.** 27 `from starlette` imports vs 17
   `from fastapi` imports show the project already reaches through
   FastAPI to use Starlette directly — the abstraction is not
   encapsulating well.
5. **`Depends()` is dead code.** Three `Depends()` declarations
   exist in `boundary.py` but no route handler signature uses them.
   This is misleading — it implies DI is used when it is not.
6. **Version coupling.** FastAPI pins specific Starlette versions
   and has historically lagged Starlette releases. Starlette
   features and fixes arrive faster without the FastAPI intermediary.
7. **No `super().__init__()`.** FastAPI's `APIRoute` and `FastAPI`
   do not call their Starlette parent constructors. This means
   Starlette improvements (like per-route middleware) are silently
   unavailable.
8. **Cognitive overhead.** Contributors must understand which
   features come from FastAPI vs Starlette, and which FastAPI
   imports are re-exports vs exclusive features.

### Perspective B: Migrate to pure Starlette

**Pros:**

1. **Remove ~19,000 lines of unused framework code** (FastAPI) and
   ~35,000 lines of unused transitive dependency (Pydantic) from
   the stack.
2. **Auto HEAD on GET routes.** Starlette's `Route()` adds HEAD
   automatically — no custom middleware needed. The HEAD regression
   would never have occurred.
3. **Per-route middleware.** Starlette's `Route(..., middleware=[...])`
   enables route-scoped middleware — not available with FastAPI's
   `APIRoute`.
4. **Direct Starlette tracking.** No intermediary version pinning.
   Starlette bugfixes and features land immediately.
5. **Honest dependency graph.** The project would declare what it
   actually uses. No phantom Pydantic import. No misleading
   `Depends()` declarations.
6. **Simpler mental model.** One framework to learn, one set of
   docs to reference, one source to read when debugging.
7. **Smaller attack surface.** Fewer dependencies means fewer
   potential vulnerability vectors.

**Cons:**

1. **Route registration refactor.** ~51 routes (22 API + 29 UI)
   need converting from `@router.get()` / `.add_api_route()` to
   `Route()` lists. Mechanical but non-trivial.
2. **Loss of name recognition.** "Built with Starlette" carries
   less weight than "Built with FastAPI" for attracting
   contributors or communicating technical direction.
3. **No dev-time OpenAPI.** The free Swagger UI at `/docs` would
   require manual setup (Starlette's `SchemaGenerator` is
   docstring-based, not automatic).
4. **Closed future door.** If the project ever needs Pydantic
   validation or DI, re-adopting FastAPI later would be another
   migration effort.
5. **Risk of regression.** Any refactoring touching all 51 routes
   carries risk. The current stack is proven at production scale.
6. **Constructor pattern change.** Starlette takes routes/middleware
   as constructor arguments vs FastAPI's imperative
   `.include_router()` / `.add_middleware()` pattern. The app factory
   would need restructuring.

---

## 6. Quantitative summary

| Metric | FastAPI | Starlette | Project |
|--------|---------|-----------|---------|
| Source lines | 18,576 | 6,536 | 12,959 |
| Runtime modules loaded | ~35 | ~27 | ~30 |
| Features used by fishtest | 3 | 15+ | 20 |
| Transitive deps pulled | Pydantic, Starlette, anyio | anyio | — |
| Known behavioral gaps | HEAD not auto-added, no per-route middleware | None | Mitigated by HeadMethodMiddleware |

### Feature usage ratio

```
FastAPI features available:     ~8 major (DI, Pydantic, OpenAPI, ...)
FastAPI features used:           3 (FastAPI class, APIRouter, exception handlers)
Usage ratio:                    ~37%

Starlette features available:  ~25 major
Starlette features used:        15+ (responses, concurrency, templates, ...)
Usage ratio:                   ~60%
```

---

## 7. Recommendation

**Keep FastAPI.** The cost-benefit does not justify a migration.

### Rationale

1. **The project works.** 164 tests pass. 9,400+ concurrent workers
   in production. The HEAD regression is resolved. There is no
   operational problem to solve.

2. **Migration effort is real, benefit is marginal.** Converting 51
   routes and restructuring the app factory is mechanical but
   carries regression risk. The savings (removing unused code from
   the dependency tree) do not translate to measurable runtime,
   security, or maintainability improvements.

3. **`APIRouter` is genuine convenience.** Decorator-style route
   registration is more concise than `Route()` lists, especially
   for the 22 API endpoints that use `@router.get()` /
   `@router.post()` directly. Data-driven `add_api_route()` is
   cleaner than constructing `Route()` objects.

4. **Future optionality has value.** The vtjson-only decision is
   documented as "deferred, not rejected." If Pydantic adoption
   happens later (even for a subset of API routes), FastAPI is
   already in place.

5. **Dev-time OpenAPI is free.** `OPENAPI_URL=/openapi.json` gives
   contributors Swagger UI with zero code — valuable for a project
   with 22 API endpoints and a distributed worker fleet.

### Recommended hygiene

| # | Action | Why |
|---|--------|-----|
| 1 | Remove dead `Depends()` declarations from `boundary.py` | Misleading — suggests DI is used when it is not |
| 2 | Normalize imports: prefer `from starlette` for re-exported classes | Honest about where the code actually comes from |
| 3 | Document the FastAPI-as-thin-wrapper pattern in `docs/1-architecture.md` | Prevents contributors from expecting Pydantic/DI patterns |
| 4 | Keep `HeadMethodMiddleware` until FastAPI fixes the HEAD gap upstream | Track [fastapi#11messages](https://github.com/fastapi/fastapi/issues/) |

### If reconsideration is needed

The trigger for revisiting this decision would be:

- FastAPI introduces a breaking change that requires significant
  adaptation work.
- The Pydantic transitive dependency causes a real security or
  compatibility issue.
- Starlette gains a feature (e.g., built-in DI) that makes the
  FastAPI layer redundant even for its convenience features.

None of these conditions currently hold.

---

## Appendix A: Class hierarchy

```
starlette.applications.Starlette
  └── fastapi.applications.FastAPI     ← fishtest uses this

starlette.routing.Router
  └── fastapi.routing.APIRouter        ← fishtest uses this

starlette.routing.Route                ← fishtest uses indirectly
  └── fastapi.routing.APIRoute         ← fishtest's routes are this

starlette.middleware.sessions.SessionMiddleware
  └── FishtestSessionMiddleware        ← fishtest custom subclass
```

## Appendix B: Import flow diagram

```
fishtest code
  ├── from fastapi:     FastAPI, APIRouter, HTTPException, Request,
  │                     Depends (dead), JSONResponse, HTMLResponse,
  │                     RedirectResponse, StreamingResponse, StaticFiles,
  │                     RequestValidationError, exception_handlers
  │     └── re-exports: Request, HTTPException, JSONResponse, HTMLResponse,
  │                     RedirectResponse, StreamingResponse, StaticFiles
  │                     (all are starlette classes)
  │
  └── from starlette:   run_in_threadpool, iterate_in_threadpool,
                        Request, HTTPConnection, MutableHeaders,
                        JSONResponse, PlainTextResponse, RedirectResponse,
                        Jinja2Templates, Route, ASGIApp, Scope, Send,
                        Receive, Message, BackgroundTask, Response
```

## Appendix C: Migration effort estimate (if ever needed)

| Change | Files | Complexity |
|--------|-------|-----------|
| `FastAPI()` → `Starlette()` | 1 (app.py) | Medium |
| `APIRouter` → `Route()` lists | 2 (api.py, views.py) | Medium |
| `include_router()` → constructor routes | 1 (app.py) | Low |
| `add_middleware()` → constructor middleware | 1 (app.py) | Low |
| Remove `Depends` imports | 1 (boundary.py) | Trivial |
| Swap `from fastapi` → `from starlette` | ~10 files | Trivial |
| Remove `RequestValidationError` handler | 1 (errors.py) | Trivial |
| Remove `openapi_url` config | 2 (app.py, settings.py) | Trivial |
| Remove `include_in_schema=False` | 1 (views.py) | Trivial |
| Drop `fastapi` from dependencies | 1 (pyproject.toml) | Trivial |
| Remove `HeadMethodMiddleware` | 2 (middleware.py, app.py) | Low (Starlette handles HEAD natively) |
| **Total** | **~15 files** | **~2 days of focused work + full regression test** |
