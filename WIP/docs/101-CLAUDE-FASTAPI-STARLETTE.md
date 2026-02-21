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

| # | Action | Why | Status |
|---|--------|-----|--------|
| 1 | Remove dead `Depends()` declarations from `boundary.py` | Misleading — suggests DI is used when it is not | **DONE** — see §10.1 |
| 2 | Normalize imports: prefer `from starlette` for re-exported classes | Honest about where the code actually comes from | **DONE** — see §10.2 |
| 3 | Document the FastAPI-as-thin-wrapper pattern in `docs/1-architecture.md` | Prevents contributors from expecting Pydantic/DI patterns | **DONE** — see §10.3 |
| 4 | Keep `HeadMethodMiddleware` until FastAPI fixes the HEAD gap upstream | Track [fastapi/fastapi#5765](https://github.com/fastapi/fastapi/discussions/5765) | **DONE** — see §10.4 |

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

## 8. Why fishtest does not use FastAPI's key features

FastAPI's three headline features — security schemes, dependency
injection, and Pydantic validation — are all unused or vestigial in
this project. Each was evaluated during the migration from Pyramid
and rejected for specific, documented reasons.

### 8.1 Security schemes: not used

FastAPI provides ~1,560 lines of security infrastructure across
12 classes: `APIKeyQuery`, `APIKeyHeader`, `APIKeyCookie`,
`HTTPBasic`, `HTTPBearer`, `HTTPDigest` (stub), `OAuth2`,
`OAuth2PasswordBearer`, `OAuth2AuthorizationCodeBearer`,
`OpenIdConnect` (stub), `OAuth2PasswordRequestForm`, and
`SecurityScopes`.

**What these classes actually do:** They are async callables that
extract credentials from HTTP headers, query parameters, or cookies.
They do **not** perform authentication — they only parse and return
the raw credential string. The developer must still verify
passwords, validate JWTs, or check API keys in their own code.

**Why fishtest doesn't use them:**

| Reason | Detail |
|--------|--------|
| **Wrong transport** | FastAPI's security classes expect credentials in the `Authorization` header (RFC 7235). Fishtest's UI uses signed cookie sessions — identity lives in a tamper-proof cookie, not a header. FastAPI has no session-cookie security class. |
| **Worker protocol is body-based** | Workers send `username` + `password` inside the JSON request body alongside the data payload. FastAPI's `HTTPBasic` reads `Authorization: Basic <b64>`, `OAuth2PasswordBearer` reads `Authorization: Bearer <token>`. Neither can parse credentials from a JSON body. |
| **Custom session already exists** | `FishtestSessionMiddleware` provides signed sessions via `itsdangerous.TimestampSigner` with: httponly/secure/samesite cookie flags, per-request max\_age overrides (remember-me), automatic size-limit enforcement, forced session clearing for blocked users. Starlette's built-in `SessionMiddleware` and FastAPI's security classes lack all four of these. |
| **CSRF is not covered** | FastAPI has no CSRF protection. Fishtest implements synchronizer-token CSRF with `secrets.compare_digest()` (timing-safe) — a requirement for cookie-based UI auth that no FastAPI feature addresses. |
| **DI coupling** | FastAPI's security classes are designed as `Depends()` callables — they integrate with the DI system. Since fishtest doesn't use DI in route handlers (see §8.2), the integration point doesn't exist. |
| **No OpenAPI benefit** | Security classes auto-populate the OpenAPI `securitySchemes` section, enabling the Authorize button in Swagger UI. With OpenAPI disabled in production and workers not using the docs, this has zero value. |

**What fishtest built instead:**

| Component | Module | Mechanism |
|-----------|--------|-----------|
| Signed cookie sessions | `http/session_middleware.py` | `itsdangerous.TimestampSigner` + HMAC-SHA1; httponly/secure/samesite |
| CSRF protection | `http/csrf.py` | Synchronizer token; `secrets.compare_digest()` timing-safe comparison |
| Session helpers | `http/cookie_session.py` | `load_session()`, `authenticated_user()`, `invalidate()`, token rotation |
| Blocked user eviction | `http/middleware.py` | ASGI middleware with 2s-TTL cached DB lookup; session clear + redirect |
| Worker API auth | `api.py` `WorkerApi` | Two-phase: `api_access_schema` (vtjson) → `userdb.authenticate()` |
| Public API | `api.py` `UserApi` | No auth — read-only public data with explicit CORS headers |

### 8.2 Dependency injection: declared but unused

FastAPI's DI system is ~1,220 lines of core code
(`dependencies/models.py` + `dependencies/utils.py`) providing:
recursive dependency resolution, per-request caching, generator-based
teardown with two-phase scoping (function vs request lifetime), and
`app.dependency_overrides` for testing.

**Current state:** Three `Depends()` declarations previously existed
in `http/boundary.py` — they have been **removed** (see §10.1):

```
# REMOVED — were dead code, never referenced by any route handler
RequestShimDep = Annotated[ApiRequestShim, Depends(get_request_shim)]
JsonBodyDep    = Annotated[JsonBodyResult,  Depends(get_json_body)]
UIContextDep   = Annotated[UIRequestContext, Depends(get_ui_context)]
```

No route handler signature ever used any of them. They were dead code.

**Why fishtest doesn't use DI:**

| Reason | Detail |
|--------|--------|
| **Pyramid heritage** | The codebase was mechanically ported from Pyramid, where every view takes a single `request` object carrying all context. The `_ViewContext` shim replicates this pattern — a monolithic context object passed to every view function. |
| **`_dispatch_view()` replaces DI** | The centralized UI dispatch pipeline (`views.py`) already performs everything DI would: session loading, DB handle injection, CSRF validation, form parsing, template rendering, session commit, cache headers. Rewriting 29 UI views to accept decomposed `Depends()` parameters would be a large refactor for no new capability. |
| **Middleware handles DB injection** | `AttachRequestStateMiddleware` stamps `rundb`, `userdb`, `actiondb`, `workerdb` on `request.state` for every request. This is simpler than a DI tree for a fixed set of 4 database handles. |
| **DI requires Pydantic internally** | FastAPI's DI resolver uses `ModelField` (Pydantic's `TypeAdapter`) for parameter extraction and validation. This creates a hidden coupling — even simple `Depends()` usage activates Pydantic's type analysis. Since the project explicitly avoided Pydantic (§8.3), activating DI would conflate the two decisions. |
| **Refactor risk** | Session commit timing and flash message ordering in `_dispatch_view()` are precise sequences. Decomposing them into DI generators would require careful testing of the teardown order. The current sequential pipeline is explicit and auditable. |
| **All 22 API handlers follow the same pattern** | Each API endpoint calls `await get_request_shim(request)` explicitly, then runs `WorkerApi` validation. This 2-line boilerplate is simpler than wiring DI for a uniform pattern. |

### 8.3 Pydantic: rejected at Milestone 12

FastAPI's Pydantic integration (~2,200 lines) provides automatic
request body parsing, response filtering via `response_model`, and
OpenAPI schema generation from `BaseModel` subclasses.

**The M12 assessment** (documented in `WIP/docs/94-CLAUDE-M12-REPORT.md`
and `WIP/docs/3-MILESTONES.md`) formally evaluated Pydantic adoption
and decided against it. The decision is recorded as "deferred, not
rejected."

**Why fishtest uses vtjson instead:**

| Reason | Detail |
|--------|--------|
| **Scope mismatch** | Most validation is in the domain layer (MongoDB document integrity), not at the HTTP boundary. Only 3 of ~15+ `validate()` sites are in the API handler. Pydantic is designed for request/response boundaries; vtjson validates anywhere. |
| **Cross-field schemas don't translate** | `runs_schema` (~100 lines) uses `ifthen`, `intersect`, `lax`, `cond` for conditional cross-field invariants like "if `sprt` is present then `num_games` must be absent." In Pydantic, these require chains of `@model_validator` methods. `action_schema` dispatches across 16 action types via `cond()` — this would need 16 model classes + a discriminated union. |
| **Error format conflict** | Workers expect HTTP 400 + `{"error": "...", "duration": N}`. Pydantic defaults to HTTP 422 with `[{"loc": [...], "msg": "...", "type": "..."}]`. Overriding this adds complexity rather than removing it. |
| **Dual validation is worse** | Using Pydantic for simple schemas + vtjson for complex ones means two validation systems to maintain. A single system is simpler. |
| **Dict-native workflow** | Fishtest passes plain Python dicts from MongoDB through validation and back. vtjson validates dicts in place — no serialization round-trip. Pydantic would create typed model instances that must be converted back to dicts for MongoDB insertion. |
| **No OpenAPI need** | The API serves internal workers with a fixed protocol. There are no external consumers, no API marketplace, no generated client libraries. Auto-generated OpenAPI schemas provide no operational value. |
| **vtjson is more expressive for this domain** | Combinators like `intersect(int, ge(0))`, `div(2)`, `close_to()`, `magic("application/gzip")`, `ifthen(condition, then, else)` are first-class. Pydantic requires custom validators for each. |

**What vtjson cannot do (and the project consciously forgoes):**

| Capability | Pydantic | vtjson |
|------------|----------|--------|
| JSON Schema generation | Yes — powers OpenAPI docs | No |
| IDE autocomplete on validated data | Yes — `item.name` | No — remains `data["name"]` |
| Serialization / response filtering | Yes — `model.model_dump(exclude=...)` | No — validation only |
| Type coercion (string→int) | Yes — configurable strict mode | No |
| Data transformation (aliases, defaults) | Yes | No |

The project treats these as acceptable tradeoffs — the data stays as
dicts throughout, MongoDB is the system of record, and the worker
protocol is fixed.

---

## 9. FastAPI features that could improve the project

While the current architecture is production-proven, several FastAPI
features offer concrete improvements if conditions change.

### 9.1 Dependency injection for testing (high value, low effort)

**What it provides:** `app.dependency_overrides[original] = mock`
replaces any dependency in the entire app without monkeypatching.
The DI resolver checks overrides before calling any dependency.

**How it helps fishtest:**

| Benefit | Current state | With DI |
|---------|--------------|---------|
| Test isolation | Tests mock `request.state.rundb` by patching `app.state` or constructing fake request objects | `app.dependency_overrides[get_rundb] = lambda: mock_rundb` — clean, localized, no patching |
| Selective mocking | Replacing one DB handle requires understanding the middleware chain | Override one dependency function; the rest continue working |
| Parallel testing | Shared `app.state` makes parallel test runs fragile | Per-test overrides on test client instances |

**Migration path:** Define dependency functions for DB handles
(already scaffolded in `boundary.py`). Add `Depends(get_rundb)` to
route signatures. Leave `AttachRequestStateMiddleware` in place as
a fallback during transition.

**Estimated effort:** ~2 days for the 22 API routes (uniform
pattern). UI routes are harder due to `_dispatch_view()` coupling.

### 9.2 Pydantic for new API endpoints (medium value, targeted)

**What it provides:** Automatic request body parsing + response
filtering + OpenAPI documentation from a single `BaseModel` class.

**When it makes sense for fishtest:**

- **New public API endpoints** that serve external consumers
  (e.g., a future public leaderboard API, webhook notifications).
  These would benefit from generated OpenAPI docs and typed
  request/response contracts.
- **SPSA parameter validation** where type coercion is useful
  (floats from form strings) and the schema is simple enough for
  a flat `BaseModel`.
- **Admin endpoints** added in future iterations that don't touch
  the legacy worker protocol.

**How to adopt incrementally:**

```python
# New endpoint — Pydantic at the boundary, vtjson for domain logic
class EloRequest(BaseModel):
    run_id: str
    confidence: float = Field(ge=0.9, le=0.999)

@router.post("/api/v2/calc_elo")
async def calc_elo_v2(body: EloRequest, request: Request):
    # Pydantic validated the shape; vtjson validates domain invariants
    run = await run_in_threadpool(request.state.rundb.get_run, body.run_id)
    validate(runs_schema, run)
    ...
```

**Key constraint:** Existing worker API endpoints must NOT change —
they use the `{"error": "...", "duration": N}` format at HTTP 400.
New Pydantic endpoints can use the standard 422 format since they
serve different consumers.

**Estimated effort:** Per-endpoint, not a bulk migration. Each new
endpoint that wants Pydantic gets it; existing endpoints keep vtjson.

### 9.3 OpenAPI for development and contributor onboarding (medium value, zero code)

**What it provides:** Setting `OPENAPI_URL=/openapi.json` enables
Swagger UI at `/docs` and ReDoc at `/redoc` — interactive API
explorers with try-it-out functionality.

**How it helps fishtest:**

| Benefit | Detail |
|---------|--------|
| **New contributor ramp-up** | Swagger UI documents all 22 API endpoints with their paths, methods, and response codes — no need to read source code |
| **Worker protocol documentation** | The API contract is currently implicit (defined by `api_schema` in Python). With OpenAPI, it becomes a browsable, testable spec |
| **Debugging** | The "Try it out" button lets developers test endpoints directly in the browser during development |
| **Contract testing** | Generated OpenAPI schema can be versioned and diffed to catch unintentional API changes |

**Current state:** Already implemented but disabled. `app.py` reads
`OPENAPI_URL` from environment — setting it is a deployment config
change, not a code change.

**Limitation:** Without Pydantic models on route signatures, the
auto-generated schema will show minimal parameter detail. Adding
`response_model` or Pydantic body types to specific endpoints
enriches the docs incrementally.

### 9.4 `response_model` for sensitive data filtering (medium value)

**What it provides:** Automatic removal of fields from response
bodies based on a Pydantic model. This is a **security feature** —
it prevents accidental exposure of internal fields.

**Where fishtest could benefit:**

| Endpoint | Risk | `response_model` fix |
|----------|------|---------------------|
| `/api/get_run/{id}` | Returns full run dict from MongoDB, which may contain internal scheduling fields | `RunPublicResponse` model strips `_id`, `tasks[].worker.password`, internal counters |
| `/api/active_runs` | Returns list of runs with worker info | `ActiveRunResponse` excludes worker credentials |
| User profile endpoints | Could expose hashed passwords or email addresses | `UserPublicResponse` with explicit field whitelist |

**How it works:** FastAPI calls `model.model_dump(include=...,
exclude_unset=...)` on the response before JSON serialization. Fields
not in the model are silently dropped — defense in depth against data
leakage.

**Migration path:** Define response models for public API endpoints.
Set `response_model=RunPublicResponse` on the route decorator. The
endpoint function continues returning a plain dict — FastAPI filters
it.

### 9.5 Dependency injection for auth guards (medium value)

**What it provides:** Declarative route-level authentication and
authorization, replacing manual `ensure_logged_in()` calls.

**How it would work:**

```python
# Dependency function
async def require_auth(request: Request) -> str:
    user = authenticated_user(request)
    if not user:
        raise HTTPException(status_code=401)
    return user

async def require_approver(user: str = Depends(require_auth)) -> str:
    if not has_permission(user, "approve_run"):
        raise HTTPException(status_code=403)
    return user

# Route declaration — auth enforced by the framework
@router.post("/api/approve_run", dependencies=[Depends(require_approver)])
async def approve_run(request: Request):
    ...
```

**Benefits:**

| Benefit | Detail |
|---------|--------|
| **Declarative security** | Auth requirements visible at the route definition, not buried inside the handler |
| **Composable** | `require_approver` depends on `require_auth` — the chain is explicit and cacheable |
| **No forgotten checks** | A route without `Depends(require_auth)` is visibly public; current pattern relies on developers remembering to call `ensure_logged_in()` |
| **Test swappable** | `dependency_overrides[require_auth] = lambda: "test_user"` eliminates auth in tests |

**Constraint:** UI routes use `_dispatch_view()` which centralizes
auth. DI auth guards are most valuable for the 22 API routes where
each handler currently calls `worker_api.validate_username_password()`
inline.

### 9.6 `Security()` with scopes for role-based API access (future value)

**What it provides:** FastAPI's `Security(dependency, scopes=[...])`
propagates OAuth2-style scopes through the DI tree. `SecurityScopes`
collects the required scopes from the entire dependency chain.

**When it's relevant for fishtest:**

- If the project introduces **API tokens** (replacing password-in-body
  for workers), scopes could differentiate read-only tokens from
  write tokens, machine tokens from human tokens.
- If **external API consumers** need different access levels (e.g.,
  a "view results" token vs a "submit tasks" token).

**Current relevance:** Low. The worker protocol is fixed. All workers
authenticate with the same password and have the same permissions.
There is no token-based access today.

**Trigger for adoption:** A decision to introduce bearer token
authentication for workers or a public API with tiered access levels.

### 9.7 Feature adoption roadmap

The following sequence prioritizes impact and minimizes risk:

| Phase | Feature | Trigger | Effort |
|-------|---------|---------|--------|
| **Now** | OpenAPI in dev/staging (`OPENAPI_URL`) | Already implemented | Config change only |
| **Next worker protocol revision** | Pydantic for new API v2 endpoints | New public-facing endpoints | Per-endpoint |
| **Test infrastructure improvement** | DI for DB handles (`dependency_overrides`) | Test suite growth or parallelization need | ~2 days for API routes |
| **Auth hardening** | DI auth guards on API routes | Security audit or incident | ~1 day for API routes |
| **Response safety net** | `response_model` on public endpoints | Data leakage concern or external API consumers | Per-endpoint |
| **Token-based auth** | `Security()` with scopes | Worker protocol v2 or public API launch | Significant design work |

Each phase is independent. None requires the others as a
prerequisite. The existing vtjson + custom session + `_dispatch_view()`
architecture continues working alongside any of these additions.

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
fishtest code (after hygiene actions — see §10)
  ├── from fastapi:     FastAPI, APIRouter, HTTPException,
  │                     RequestValidationError, exception_handlers
  │     └── FastAPI-exclusive: HTTPException (subclass, not re-export),
  │                            RequestValidationError (no starlette equivalent)
  │
  └── from starlette:   Request, HTMLResponse, JSONResponse,
                        RedirectResponse, StreamingResponse,
                        PlainTextResponse, StaticFiles, Response,
                        run_in_threadpool, iterate_in_threadpool,
                        HTTPConnection, MutableHeaders, FormData,
                        Jinja2Templates, Route, ASGIApp, Scope, Send,
                        Receive, Message, BackgroundTask
```

## 10. Hygiene actions executed

All four recommended hygiene actions from §7 have been implemented,
linted clean, and verified against the full 164-test suite.

### 10.1 Removed dead `Depends()` declarations

**Files changed:** `server/fishtest/http/boundary.py`

Removed:
- `from typing import Annotated`
- `from fastapi import Depends` (replaced `from fastapi import Depends, Request`
  with `from starlette.requests import Request`)
- Three type aliases: `RequestShimDep`, `JsonBodyDep`, `UIContextDep`
- Corresponding `__all__` entries
- Updated `get_ui_context` docstring ("Dependency that builds..." →
  "Build the UI request context...")

The underlying functions (`get_request_shim()`, `get_json_body()`,
`get_ui_context()`) remain — they are called directly by route
handlers, not via FastAPI's DI system.

### 10.2 Normalized imports

**Runtime imports normalized (6 files):**

| File | Before | After |
|------|--------|-------|
| `views.py` | `from fastapi import Request`, `from fastapi.responses import HTMLResponse, RedirectResponse` | `from starlette.requests import Request`, `from starlette.responses import ...` |
| `api.py` | `from fastapi import Request`, `from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse` | `from starlette.requests import Request`, `from starlette.responses import ...` |
| `app.py` | `from fastapi.staticfiles import StaticFiles` | `from starlette.staticfiles import StaticFiles` |
| `http/boundary.py` | `from fastapi import Depends, Request` | `from starlette.requests import Request` |
| `http/csrf.py` | `from fastapi import Request` | `from starlette.requests import Request` (moved to `TYPE_CHECKING`) |
| `http/errors.py` | `from fastapi.responses import JSONResponse` | `from starlette.responses import JSONResponse` (merged with existing starlette import) |

**TYPE_CHECKING imports normalized (6 files):**

`from fastapi import Request` → `from starlette.requests import Request` in:
`http/ui_context.py`, `http/template_renderer.py`, `http/ui_errors.py`,
`http/jinja.py`, `http/dependencies.py`, `http/errors.py`

**Kept as `from fastapi`** (not re-exports):
- `HTTPException` — FastAPI subclass of `starlette.exceptions.HTTPException`
  (verified: `fastapi.HTTPException is not starlette.exceptions.HTTPException`)
- `RequestValidationError` — FastAPI-exclusive (`fastapi.exceptions` module)
- `FastAPI`, `APIRouter` — FastAPI-exclusive classes

After normalization, the `from fastapi` surface is:
`FastAPI`, `APIRouter`, `HTTPException`, `RequestValidationError`.

### 10.3 Documented thin-wrapper pattern

**File changed:** `docs/1-architecture.md`

Added section "Framework usage: FastAPI as a thin wrapper" between
"Validation" and "Error handling". Content:
- Lists 3 FastAPI-exclusive features in use
- Lists 6 FastAPI features NOT used (and why)
- Instructs contributors to prefer `from starlette` for re-exported classes
- References this report for full analysis

### 10.4 Updated `HeadMethodMiddleware` tracking

**File changed:** `server/fishtest/http/middleware.py`

Expanded `HeadMethodMiddleware` docstring with:
- Root cause explanation: `APIRoute.__init__` does not call
  `super().__init__()`, skipping Starlette's automatic HEAD method
  generation
- Upstream tracking: `https://github.com/fastapi/fastapi/discussions/5765`
- Local analysis reference: `WIP/docs/200-ISSUE-HEAD.md`
- Explicit instruction: "Remove only after FastAPI fixes the gap"

### 10.5 Verification

- **Lint:** `WIP/tools/lint_http.sh` (ruff `--select ALL` + ty) — passes clean
- **Tests:** 164/164 pass (`python -m unittest discover`)
- **No out-of-scope changes:** only files listed above were touched

---

## Appendix C: Migration effort estimate (if ever needed)

| Change | Files | Complexity |
|--------|-------|-----------|
| `FastAPI()` → `Starlette()` | 1 (app.py) | Medium |
| `APIRouter` → `Route()` lists | 2 (api.py, views.py) | Medium |
| `include_router()` → constructor routes | 1 (app.py) | Low |
| `add_middleware()` → constructor middleware | 1 (app.py) | Low |
| ~~Remove `Depends` imports~~ | ~~1 (boundary.py)~~ | ~~Trivial~~ — **already done (§10.1)** |
| ~~Swap `from fastapi` → `from starlette`~~ | ~~\~10 files~~ | ~~Trivial~~ — **partially done (§10.2)** |
| Remove `RequestValidationError` handler | 1 (errors.py) | Trivial |
| Remove `openapi_url` config | 2 (app.py, settings.py) | Trivial |
| Remove `include_in_schema=False` | 1 (views.py) | Trivial |
| Drop `fastapi` from dependencies | 1 (pyproject.toml) | Trivial |
| Remove `HeadMethodMiddleware` | 2 (middleware.py, app.py) | Low (Starlette handles HEAD natively) |
| **Total** | **~15 files** | **~2 days of focused work + full regression test** |
