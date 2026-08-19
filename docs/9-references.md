# Developer references

Curated web references and project-specific patterns for the libraries that
make up the fishtest stack: FastAPI, Starlette, Jinja2, htmx, the front-end
CDN assets, and the Python, MongoDB and tooling layer. Each section pairs
upstream documentation links with the conventions this repository actually
follows, named by file and symbol.

For server architecture and request flow, see
[1-architecture.md](1-architecture.md). For the threading model and async/sync
boundaries, see [2-threading-model.md](2-threading-model.md). For local setup,
lint and test commands, see [7-development.md](7-development.md).

## FastAPI

### Canonical references

| Topic | URL |
|-------|-----|
| Bigger applications (APIRouter) | https://fastapi.tiangolo.com/tutorial/bigger-applications/ |
| Dependencies overview | https://fastapi.tiangolo.com/tutorial/dependencies/ |
| Dependencies in decorators | https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-in-path-operation-decorators/ |
| Handling errors | https://fastapi.tiangolo.com/tutorial/handling-errors/ |
| Middleware | https://fastapi.tiangolo.com/tutorial/middleware/ |
| Request forms | https://fastapi.tiangolo.com/tutorial/request-forms/ |
| Request files / UploadFile | https://fastapi.tiangolo.com/tutorial/request-files/ |
| Lifespan events | https://fastapi.tiangolo.com/advanced/events/ |
| Behind a proxy / root_path | https://fastapi.tiangolo.com/advanced/behind-a-proxy/ |
| Templates | https://fastapi.tiangolo.com/advanced/templates/ |
| Testing | https://fastapi.tiangolo.com/tutorial/testing/ |

### Project patterns

**Router structure**: exactly two routers exist.
`server/fishtest/api.py` defines `APIRouter(tags=["api"])` and
`server/fishtest/views.py` defines `APIRouter(tags=["ui"])`. The other
`views_*.py` modules export handlers, not routers.
`server/fishtest/app.py` -> `create_app` assembles them.

```python
# server/fishtest/app.py (shape)
app = FastAPI(lifespan=lifespan, openapi_url=openapi_url)
install_error_handlers(app)
# ... add_middleware calls ...
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(views_router)
app.include_router(api_router)
```

`openapi_url` comes from `AppSettings.from_env` and is `None` unless
`OPENAPI_URL` is set, which is what disables `/docs`, `/redoc` and
`/openapi.json` in production.

**No dependency injection**: fishtest does not use FastAPI's `Depends()` /
`Annotated` dependency system anywhere. Authentication, CSRF, and session
access are enforced centrally -- in `_dispatch_view` for UI routes and
per-handler in the API router -- not through injected dependencies. Despite its
name, `server/fishtest/http/dependencies.py` contains plain accessor functions
(`get_rundb`, `get_userdb`, `get_actiondb`, `get_workerdb`,
`get_request_context`) that read `request.state` with an `app.state` fallback;
they are called directly, never through `Depends`.

**Lifespan**: Manages MongoDB client, scheduler, and caches. One
`@asynccontextmanager` in `app.py`.

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown
```

**Error shaping**: UI errors return HTML via exception handlers. Worker API
errors return JSON with `{"error": "...", "duration": N}`.

**Sync handlers**: Sync view/API functions are automatically run via
`run_in_threadpool` by Starlette/FastAPI. Most fishtest handlers use
`async def` with explicit `run_in_threadpool` calls for blocking DB work.

**Testing**: `server/tests/test_support.py` -> `build_test_app` assembles a
`FastAPI` instance with the production error handlers and the same middleware
in the same order, minus the lifespan and minus
`RejectNonPrimaryWorkerApiMiddleware`, and exercises it against a dedicated
`fishtest_tests` MongoDB. Because the app uses no FastAPI dependencies, there
are no `app.dependency_overrides`.

## Starlette

### Canonical references

| Topic | URL |
|-------|-----|
| Middleware | https://www.starlette.dev/middleware/ |
| Requests | https://www.starlette.dev/requests/ |
| Responses | https://www.starlette.dev/responses/ |
| Routing / url_for | https://www.starlette.dev/routing/ |
| StaticFiles | https://www.starlette.dev/staticfiles/ |
| Exceptions | https://www.starlette.dev/exceptions/ |
| Lifespan | https://www.starlette.dev/lifespan/ |
| TestClient | https://www.starlette.dev/testclient/ |
| Thread pool | https://www.starlette.dev/threadpool/ |

### Project patterns

**Session middleware**: `FishtestSessionMiddleware` is a pure ASGI middleware
class; it does not subclass Starlette's `SessionMiddleware`. It signs and
verifies the `fishtest_session` cookie directly with
`itsdangerous.TimestampSigner`. Per-request cookie lifetime, the `Secure` flag,
and forced expiry are driven through `scope["session_max_age"]`,
`scope["session_secure"]`, and `scope["session_force_clear"]`. It follows the
pure ASGI middleware shape shown below.

**Pure ASGI middleware** (preferred over `BaseHTTPMiddleware`):

```python
class MyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # pre-processing
        await self.app(scope, receive, send)
```

Current middleware stack (all are pure ASGI):
- Installation order in `app.add_middleware(...)`:
    `HeadMethodMiddleware` -> `ShutdownGuardMiddleware` ->
    `AttachRequestStateMiddleware` ->
    `RejectNonPrimaryWorkerApiMiddleware` -> `RedirectBlockedUiUsersMiddleware` ->
    `FishtestSessionMiddleware`
- Runtime order (outermost -> innermost):
    `FishtestSessionMiddleware` -> `RedirectBlockedUiUsersMiddleware` ->
    `RejectNonPrimaryWorkerApiMiddleware` -> `AttachRequestStateMiddleware` ->
    `ShutdownGuardMiddleware` -> `HeadMethodMiddleware`

**Request form limits** (DOS protection): `server/fishtest/views.py` parses UI
form bodies with explicit caps taken from
`server/fishtest/http/settings.py`.

```python
post = await request.form(
    max_files=FORM_MAX_FILES,          # UI_FORM_MAX_FILES
    max_fields=FORM_MAX_FIELDS,        # UI_FORM_MAX_FIELDS
    max_part_size=FORM_MAX_PART_SIZE,  # UI_FORM_MAX_PART_SIZE_BYTES
)
```

These caps apply to UI form routes only. Worker API bodies are JSON and are
bounded by nginx `client_max_body_size`; see
[8-deployment.md](8-deployment.md).

**URL generation**: this codebase does not use `url_for`. UI routes are
registered by `server/fishtest/views.py` -> `_register_view_routes()` with
`router.add_api_route(path, endpoint, methods=..., include_in_schema=False)`
and no `name=`, so no route is addressable by name. Templates build URLs from
the `urls` mapping supplied by `server/fishtest/http/boundary.py` ->
`build_template_context()`, or from literal paths.

**Response classes**:
- `HTMLResponse` for UI endpoints
- `JSONResponse` for API endpoints
- `RedirectResponse` for redirects
- `StreamingResponse` for PGN downloads

**Thread pool**: Sync functions and file I/O consume threadpool tokens. Keep
blocking DB and filesystem work off the event loop via `run_in_threadpool`.

## Jinja2

### Canonical references

| Topic | URL |
|-------|-----|
| Template designer docs | https://jinja.palletsprojects.com/en/latest/templates/ |
| API (Environment, autoescape) | https://jinja.palletsprojects.com/en/latest/api/ |
| Starlette templates integration | https://www.starlette.dev/templates/ |
| FastAPI templates integration | https://fastapi.tiangolo.com/advanced/templates/ |

### Project patterns

**Environment setup**: `server/fishtest/http/jinja.py` ->
`default_environment` builds the `jinja2.Environment` with
`FileSystemLoader(templates_dir())`, `select_autoescape(["html", "xml", "j2"])`,
`undefined=StrictUndefined` and the `jinja2.ext.do` extension. Custom globals
and filters are registered there, before any template renders.
`templates_dir()` honours `FISHTEST_JINJA_TEMPLATES_DIR` and otherwise resolves
the package `templates/` directory.

**Template rendering**: Synchronous, always off the event loop.

```python
from starlette.templating import Jinja2Templates

templates = Jinja2Templates(env=default_environment())  # custom Environment

@router.get("/page")
async def page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="page.html",
        context={"title": "Page"},
    )
```

**Key rules**:
- `Jinja2Templates.TemplateResponse()` injects `request` into the context if it
    is missing, but repository code always passes it explicitly:
    `http/jinja.py` -> `render_template_response` raises `ValueError` when
    `request` is absent from the context.
- `Jinja2Templates` accepts `directory=` or `env=`, not both.
- `TemplateResponse` exposes `.template` and `.context` for test assertions.
- Context processors must be sync functions.
- JS data is passed via `{{ value|tojson }}`.

**Registered globals** (repository-specific):

All globals are registered in `server/fishtest/http/jinja.py` ->
`default_environment` unless noted.

| Global | Contents |
|--------|----------|
| `urls` | Named UI paths built by `http/boundary.py` -> `build_template_context()`; templates use this instead of `url_for` |
| `static_url` | Maps a `fishtest:static/...` spec to `/static/...` plus a content-hash cache buster |
| `poll` | Polling intervals in seconds, keyed by page (`tasks_detail`, `machines_homepage`, `live_elo`, `tests_run_tables`, `tests_stats`, `tests_view_detail`, `pending_users_nav`, `rate_limits_github`, `rate_limits_server`) |
| `htmx` | `input_changed_delay_ms`, the shared search debounce baseline, from `http/settings.py` -> `HTMX_INPUT_CHANGED_DELAY_MS` |
| `finished` | `filter_max_count_anon`, `filter_max_count_auth` |
| `cookies` | `contributors_findme_max_age`, `ui_state_max_age` |
| `gh`, `fishtest` | The `fishtest.github_api` and `fishtest` modules |
| Formatting helpers | Re-exported from `server/fishtest/http/template_helpers.py` (`format_date`, `format_results`, `format_bounds`, `diff_url`, `worker_name`, and others) |
| `copy`, `datetime`, `math`, `urllib`, `float` | Standard-library escape hatches used by templates |

Every value under `poll`, `htmx`, `finished` and `cookies` is defined in
`server/fishtest/http/settings.py` (`POLL_*_S`,
`HTMX_INPUT_CHANGED_DELAY_MS`, `FINISHED_FILTER_MAX_COUNT_ANON`,
`FINISHED_FILTER_MAX_COUNT_AUTH`, `UI_STATE_COOKIE_MAX_AGE_SECONDS`) and only
re-exported by `jinja.py`. Change it in the settings module, not in a
template.

Custom filters: `urlencode`, `split`, `string`.

**Autoescaping**: Enabled for `.html`, `.xml`, `.j2` extensions. Raw HTML
must use `{{ value|safe }}` or `{% autoescape false %}`.

## htmx

### Canonical references

| Topic | URL |
|-------|-----|
| Documentation | https://htmx.org/docs/ |
| Attributes reference | https://htmx.org/reference/ |
| Events reference | https://htmx.org/events/ |
| Request/response headers | https://htmx.org/reference/#headers |
| Configuration | https://htmx.org/docs/#config |
| Polling | https://htmx.org/docs/#polling |
| OOB swaps | https://htmx.org/docs/#oob_swaps |
| OOB troublesome tables | https://htmx.org/attributes/hx-swap-oob/#troublesome-tables-and-lists |
| Push URL | https://htmx.org/attributes/hx-push-url/ |
| Indicator | https://htmx.org/attributes/hx-indicator/ |
| Sync / request coordination | https://htmx.org/attributes/hx-sync/ |
| Inheritance control | https://htmx.org/attributes/hx-disinherit/ |
| Parameter filtering | https://htmx.org/attributes/hx-params/ |
| Multiple triggers | https://htmx.org/attributes/hx-trigger/ |
| Template fragments essay | https://htmx.org/essays/template-fragments/ |
| Hypermedia Systems (book) | https://hypermedia.systems/ |
| Web security with htmx | https://htmx.org/essays/web-security-basics-with-htmx/ |
| `Vary` (MDN) | https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Vary |
| Search inputs (MDN) | https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input/search |
| Search clear pseudo-element (MDN) | https://developer.mozilla.org/en-US/docs/Web/CSS/::-webkit-search-cancel-button |
| Search event (MDN) | https://developer.mozilla.org/en-US/docs/Web/API/HTMLInputElement/search_event |
| `aria-sort` (MDN) | https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-sort |
| `visibilitychange` (MDN) | https://developer.mozilla.org/en-US/docs/Web/API/Document/visibilitychange_event |

### Project patterns

**CDN loading**: htmx 2.0.10 is loaded from `cdn.jsdelivr.net` in
`base.html.j2` with an SRI integrity hash. No npm build step.

```html
<script src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.10/dist/htmx.min.js"
    integrity="sha256-cepnGFv6jJjDnTFxfG/OXYUjcPzf0SnbRUN3TTFFwN4="
    crossorigin="anonymous"
    referrerpolicy="no-referrer"></script>
```

**Detail-page assets**: `/tests/view/{id}` additionally loads jsdiff (inline
Diff panel) and highlight.js (source highlighting) from `cdn.jsdelivr.net` in
`server/fishtest/templates/tests_view.html.j2`. Both are pinned and protected
with SRI.

```html
<script src="https://cdn.jsdelivr.net/npm/diff@9.0.0/dist/diff.min.js"
    integrity="sha256-tRqdKIXywJDcl7mBAnOV9+fmVYpGx1rjdH2yZ5E6ias="
    crossorigin="anonymous"
    referrerpolicy="no-referrer"></script>
```

**Fragment detection in Starlette/FastAPI**: htmx sends `HX-Request: true`
on every AJAX request. The server detects this header to decide between
full-page and fragment rendering. A `Sec-Fetch-Mode` guard prevents
htmx-boosted full-page navigations from being treated as fragment requests:

`server/fishtest/views_helpers.py` -> `_is_hx_request`:

```python
def _is_hx_request(request: Any) -> bool:
    headers = getattr(request, "headers", None)
    if headers is None:
        return False
    if (headers.get("HX-Request") or "").lower() != "true":
        return False
    # Never treat top-level document navigations as fragment requests,
    # even if HX-Request appears in transit.
    return (headers.get("Sec-Fetch-Mode") or "").lower() != "navigate"
```

**Dual-mode rendering with Jinja2**: the view handler renders either a
fragment template or the full-page template from the same URL.
`server/fishtest/views.py` -> `_render_hx_fragment` encapsulates the
check-and-render pattern, and `_render_hx_or_context` falls back to the
page context when the request is not a fragment request:

```python
def _render_hx_fragment(request, template_name, context):
    if not _is_hx_request(request):
        return None
    return render_template_to_response(
        request=request.raw_request,
        template_name=template_name,
        context=build_template_context(
            request.raw_request, request.session, context
        ),
    )
```

Fragment templates are standalone `.html.j2` files that do not extend
`base.html.j2`. This avoids partial-block rendering complexity and keeps
fragments self-contained.

**Content fragments for stateful tables**: stateful list pages do not return only
row fragments. They return content fragments (`contributors_content_fragment`,
`user_management_content_fragment`, `workers_content_fragment`) so that view
toggles, truncation banners, pagination, and sort state remain synchronized
with the table body.

**Vary header for HTTP caching**: when the same URL can return either a
full page or a fragment, `Vary: HX-Request` must be set on the response so
that HTTP caches (nginx, CDNs) store separate representations.
`server/fishtest/views_helpers.py` -> `_append_vary_header` appends the token
without duplicating an existing entry:

```python
_append_vary_header(response, "HX-Request")
```

Use the same `Vary: HX-Request` value on the full-page response, the fragment
response, and any `304 Not Modified` response for that URL.

**OOB swaps with Jinja2**: out-of-band elements carry `hx-swap-oob`
attributes directly in the template markup. Multiple elements can be updated
in a single response. For table rows, `<template>` wrappers are required
because the HTML parser rejects `<tbody>` inside `<div>`:

```jinja
{# OOB span -- works directly #}
<span id="count" hx-swap-oob="innerHTML">{{ count }}</span>

{# OOB table body -- requires template wrapper #}
<template>
  <tbody id="my-table" hx-swap-oob="innerHTML">
    {% for row in rows %}
      <tr>...</tr>
    {% endfor %}
  </tbody>
</template>
```

**Polling lifecycle codes**: polled endpoints use HTTP status codes to
control client behavior:
- **200** -- swap the response content, continue polling.
- **204** -- no content change; htmx skips the swap, continues polling.
- **286** -- swap the response and stop polling (terminal state).

**Request coordination**: `hx-sync` is used where user actions and polling can
target the same fragment. The repo patterns are
`hx-sync="#machines-filters:abort"` and `hx-sync="#tasks-filters:abort"`, so
user-initiated sort and page changes beat the background poll. Pagination links
receive their value through the `pagination_hx_sync` context key
(`server/fishtest/templates/pagination.html.j2`).

**Inherited attribute control**: inside filter forms that use inherited
`hx-include`, sort and pagination links may opt out with
`hx-disinherit="hx-include"` and `hx-params="none"` so only the explicit URL
state is sent.

**Search portability**: `input changed delay:{{ htmx.input_changed_delay_ms }}ms`
is the portable search trigger baseline. Native `search` events and
`::-webkit-search-cancel-button` styling are browser-specific enhancements,
not the correctness contract. Search inputs keep visible labels or another
valid accessible name.

**Conditional polling with visibility**: polls are gated on tab visibility
to avoid unnecessary server load:

```html
<div hx-get="/endpoint"
     hx-trigger="every 30s [document.visibilityState === 'visible']"
     hx-swap="none">
</div>
```

Treat the transition to `hidden` as the point to stop background UI updates.

**Focus-return immediate refresh**: every visibility-gated poller also
triggers on `visibilitychange` so that returning to the tab produces an
immediate update instead of waiting for the next poll cycle:

```html
<div hx-get="/endpoint"
     hx-trigger="every 30s [document.visibilityState === 'visible'],
                 visibilitychange[document.visibilityState === 'visible'] from:document"
     hx-swap="none">
</div>
```

Section-scoped pollers (machines, tasks) combine both the tab visibility and
section expanded state in a single filter expression:

```html
<div hx-get="/endpoint"
     hx-trigger="every 120s [document.visibilityState === 'visible'
                 && document.getElementById('section').classList.contains('show')],
                 visibilitychange[document.visibilityState === 'visible'
                 && document.getElementById('section').classList.contains('show')] from:document"
     hx-swap="innerHTML">
</div>
```

**Error recovery in JavaScript**: retry buttons in htmx error handlers
must be constructed with DOM API (`createElement`, `textContent`,
`setAttribute`) rather than string concatenation with `innerHTML`, to
prevent XSS from error messages and to keep htmx attributes functional
(via `htmx.process()`).

## Front-end CDN assets

There is no npm build step. Every third-party front-end asset is loaded from
`cdn.jsdelivr.net` with a pinned version, an SRI `integrity` hash,
`crossorigin="anonymous"` and `referrerpolicy="no-referrer"`. Bumping a version
requires recomputing the hash in the same commit.

| Asset | Version | Loaded from |
|-------|---------|-------------|
| Bootstrap CSS and bundle JS | 5.3.8 | `templates/base.html.j2` |
| Font Awesome Free | 7.3.1 | `templates/base.html.j2` |
| htmx | 2.0.10 | `templates/base.html.j2` |
| jsdiff | 9.0.0 | `templates/tests_view.html.j2` |
| highlight.js CDN assets | 11.11.1 | `templates/tests_view.html.j2` |

Paths are relative to `server/fishtest/`. First-party CSS and JS are served
from `/static` through the `static_url` global, never from a CDN.

| Topic | URL |
|-------|-----|
| Bootstrap 5.3 | https://getbootstrap.com/docs/5.3/getting-started/introduction/ |
| Bootstrap 5.3 tables | https://getbootstrap.com/docs/5.3/content/tables/ |
| Bootstrap 5.3 forms | https://getbootstrap.com/docs/5.3/forms/overview/ |
| Font Awesome | https://docs.fontawesome.com/ |
| highlight.js | https://highlightjs.readthedocs.io/en/latest/ |
| Subresource Integrity (MDN) | https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity |

## Python, MongoDB, and tooling

### Canonical references

| Topic | URL |
|------|-----|
| Python 3.14 docs | https://docs.python.org/3.14/ |
| Python 3.14 `unittest` | https://docs.python.org/3.14/library/unittest.html |
| MongoDB manual | https://www.mongodb.com/docs/manual/ |
| MongoDB indexes | https://www.mongodb.com/docs/manual/core/indexes/ |
| PyMongo | https://www.mongodb.com/docs/languages/python/pymongo-driver/current/ |
| vtjson schema format | https://www.cantate.be/vtjson |
| Uvicorn | https://www.uvicorn.org/ |
| itsdangerous | https://itsdangerous.palletsprojects.com/en/stable/ |
| uv | https://docs.astral.sh/uv/ |
| Ruff | https://docs.astral.sh/ruff/ |
| ty | https://docs.astral.sh/ty/ |
| pre-commit | https://pre-commit.com/ |
| Prettier | https://prettier.io/docs/en/ |
| nginx | https://nginx.org/en/docs/ |

Use [7-development.md](7-development.md) for local lint and test workflows.

## Repository configuration map

| File or directory | Purpose |
|-------------------|---------|
| `pyproject.toml` | Root virtual project: `dev` group (pre-commit, ruff, ty) and the shared `[tool.ruff]` config CI passes with `--config` |
| `uv.lock` | Lock file for the root dev group only |
| `server/pyproject.toml`, `server/uv.lock` | Server runtime dependencies and the `test` group |
| `worker/pyproject.toml`, `worker/uv.lock` | Worker runtime dependencies (Python >= 3.8) |
| `.pre-commit-config.yaml` | Hygiene hooks, ruff check and format, `uv lock` |
| `.editorconfig` | 2-space indent everywhere, 4 spaces for `*.py`, UTF-8, final newline |
| `.github/workflows/` | `lint.yaml`, `server.yaml`, `worker_posix.yaml`, `worker_msys2.yaml` |
| `server/fishtest/http/settings.py` | Every tunable UI, polling, form and threadpool constant |
| `server/fishtest/constants.py` | Shared literals used by schemas and views |
| `server/tests/` | Unit and HTTP contract tests |
| `server/utils/` | Operational scripts; inventory in [8-deployment.md](8-deployment.md) |

## Testing patterns

### Test structure

Server tests live in `server/tests/`. All tests use `unittest.TestCase`.
MongoDB is required for most tests; `.github/workflows/server.yaml` starts
`mongod` before running the suite. Tests use the `fishtest_tests` database, not
`fishtest_new`. User-facing HTTP route tests are split by route family or one
focused UI motif instead of accumulating in one omnibus module.

### Fixtures

Most test files import `test_support` (`server/tests/test_support.py`), which
provides:

- `get_rundb()`: returns a `RunDb` bound to `db_name="fishtest_tests"`.
- `require_fastapi()`: returns `(FastAPI, TestClient)` or raises
  `unittest.SkipTest` when the server test dependencies are missing.
- `build_test_app(*, rundb, include_api, include_views)`: returns a `FastAPI`
  instance with the production error handlers and middleware order, minus the
  lifespan and minus `RejectNonPrimaryWorkerApiMiddleware`.
- `make_test_client(*, rundb, include_api, include_views)`: wraps
  `build_test_app` in `fastapi.testclient.TestClient`.
- `cleanup_test_rundb(...)`: clears named usernames and runs, optionally drops
  the runs collection and closes the connection.
- `find_run(...)`: retrieves a run from the database by field match.
- `extract_csrf_token(html)`: parses the `csrf-token` meta tag from rendered
  HTML.
- `extract_meta_content(...)`: reads an arbitrary meta tag from rendered HTML.

User-facing UI route modules also reuse `server/tests/ui_user_test_case.py`
for shared client setup, login helpers, run creation, and DB cleanup. Its
`UiUserTestCase` creates the fixture user with `UserDb.create_user`, then
clears `pending` and `blocked` through `UserDb.save_user`; `_set_approver_state`
adds `group:approvers`. Use the same sequence when a new test needs a
privileged user.

Worker-related fixtures must match the `short_worker_name` pattern
(`.*-[\d]+cores-[a-zA-Z0-9]{2,8}`) or `WorkerDb.update_worker()` schema
validation fails.

### Key test modules

| Module | Coverage |
|--------|----------|
| `test_app.py` | Application startup, middleware, lifespan |
| `test_api.py` | Worker API protocol (request_task, update_task, beat) |
| `test_users.py` | Login, signup, remember-me, userdb auth flags, basic UI smoke |
| `test_views_admin.py` | Workers, user-management, rate-limits, and shared admin UI contracts |
| `test_views_tests.py` | `/tests` and `/tests/user/{username}` filter state and live-table polling |
| `test_views_actions.py` | Actions search, pagination, sorting |
| `test_views_contributors.py` | Contributors search, rank jump, sorting, and HTMX state sync |
| `test_views_detail.py` | `/tests/view/{id}` detail polling, `/tests/tasks/{id}`, and task UI assets |
| `test_views_finished.py` | Finished-runs search and pagination |
| `test_views_helpers.py` | Shared pagination, parameter, and merge helpers |
| `test_views_machines.py` | Machines helper behavior plus `/tests/machines` HTTP contracts |
| `test_views_run.py` | Run-form validation, SPSA parsing, permissions, and run action routes |
| `test_spsa_workflow.py` | Shared classic SPSA form, worker update, and chart-payload helper contracts |
| `test_http_boundary.py` | HTTP boundary invariants and template contracts |
| `test_http_dependencies.py` | Request-state dependency wiring |
| `test_http_errors.py` | API vs UI error shaping |
| `test_http_helpers.py` | Jinja/static helpers and shared HTTP utilities |
| `test_http_middleware.py` | Middleware behavior and blocked-user flow |
| `test_http_settings.py` | Runtime settings and environment parsing |
| `test_http_ui_session_semantics.py` | Session commit and UI CSRF semantics |
| `test_http_ui_cookies.py` | Shared browser-readable UI cookie helpers |
| `test_http_open_graph.py` | Open Graph metadata helpers |
| `test_nn.py` | Neural network upload and listing |
| `test_rundb.py` | `RunDb` persistence and run lifecycle behavior |
| `test_kvstore.py` | Key-value store behavior |
| `test_lru_cache.py` | LRU cache storage, eviction, and decorator behavior |
| `test_github_api.py` | GitHub API client, rate-limit guard, and retry policy |
| `test_delta_update_users.py` | Monthly contributor stats rebuild helpers from `server/utils/delta_update_users.py` |
| `test_views_routes.py` | UI route HTTP method contracts |
| `test_views_stats.py` | `/tests/stats` page and fragment contracts |
| `test_support.py` | The shared fixtures themselves |
