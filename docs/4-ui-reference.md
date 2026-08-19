# UI Routes and View Dispatch

## Scope

This page is the reference for the HTML user interface: every registered UI
route, the handler and template behind it, the shared view dispatch pipeline,
htmx fragment dispatch, session/CSRF/authentication handling, and the query
parameters and cookies that drive each page. Worker and JSON endpoints are
covered in [3-api-reference.md](3-api-reference.md); per-template context
contracts are covered in [5-templates.md](5-templates.md).

## Where the code lives

| Concern | File |
|---|---|
| ASGI app, middleware install, router include | `server/fishtest/app.py` |
| Route table, dispatch, handler bodies | `server/fishtest/views.py` |
| Run creation, modification, and permission helpers | `server/fishtest/views_run.py` |
| `/actions` query, caps, and row building | `server/fishtest/views_actions.py` |
| Finished-run query, caps, canonical URLs | `server/fishtest/views_finished.py` |
| `/tests/machines` filtering, sorting, cookies | `server/fishtest/views_machines.py` |
| Stateless pagination, parameter, and merge helpers | `server/fishtest/views_helpers.py` |
| Session cookie, CSRF, dependencies, errors, settings | `server/fishtest/http/` |
| Jinja2 templates | `server/fishtest/templates/` |
| Browser-side behavior | `server/fishtest/static/js/` |

Every handler symbol named in the route table is defined in
`server/fishtest/views.py`. Some handlers are thin wrappers whose body lives in
a sibling module:

- `actions()` delegates to `views_actions.py` -> `actions()`
- `tests_machines()` delegates to `views_machines.py` -> `tests_machines()`
- `tests()`, `tests_user()`, and `tests_finished()` build their finished-run
  slice with `views_finished.py` -> `get_paginated_finished_runs()`
- `tests_run()` and `tests_modify()` validate through `views_run.py` ->
  `validate_form()`, `validate_modify()`, and `can_modify_run()`

## Request call chain

Follow this chain when tracing a UI request:

1. `server/fishtest/app.py` -> `create_app()` builds the FastAPI application.
2. ASGI middleware runs, outermost first: `FishtestSessionMiddleware`
   (`http/session_middleware.py`), then `RedirectBlockedUiUsersMiddleware`,
   `RejectNonPrimaryWorkerApiMiddleware`, `AttachRequestStateMiddleware`,
   `ShutdownGuardMiddleware`, and `HeadMethodMiddleware` (all in
   `http/middleware.py`).
3. `server/fishtest/views.py` -> `_register_view_routes()` registered every
   route at import time. The endpoint FastAPI calls is the closure built by
   `_make_endpoint()`.
4. `_dispatch_view()` loads the request context
   (`http/dependencies.py` -> `get_request_context()`), parses POST form data,
   enforces CSRF (`http/csrf.py` -> `csrf_or_403()`), builds `_ViewContext`,
   and runs the sync handler in the threadpool.
5. The handler returns either a `Response` (redirect or already-rendered
   fragment) or a context dict.
6. Context dicts are rendered by `http/template_renderer.py` ->
   `render_template_to_response()`, using the shared context from
   `http/boundary.py` -> `build_template_context()`.
7. `_dispatch_view()` commits the session, applies cache headers, and appends
   `Vary: HX-Request` to GET responses.

The htmx fragment path branches at step 5: the handler calls
`_render_hx_fragment()` (or the `_render_hx_or_context()` wrapper), which
returns a rendered fragment `Response` for htmx requests and `None` otherwise,
letting the caller fall through to full-page rendering.

## Route table

Routes are declared in `_VIEW_ROUTES` as tuples of:

```python
(handler_function, path, config_dict)
```

Config keys:

| Key | Type | Description |
|-----|------|-------------|
| `renderer` | string | Jinja2 template name (e.g., `"tests.html.j2"`) |
| `require_csrf` | bool | Enforce CSRF validation on POST |
| `require_primary` | bool | Reject POST on non-primary instance (503) |
| `request_method` | string or tuple | Allowed HTTP methods (default: GET) |
| `http_cache` | int | `Cache-Control: max-age=` seconds |
| `direct` | bool | Bypass `_dispatch_view` (for pure redirects) |

At module load time, `_register_view_routes()` iterates over `_VIEW_ROUTES`,
wraps each handler with `_dispatch_view()` (unless `direct=True`), and
registers it on the FastAPI router with `include_in_schema=False`. Routes are
registered without an explicit `name=`, so templates build URLs from the shared
`urls` dict and literal paths rather than from `url_for()`.

When `request_method` is omitted, the route is read-only and registered as
`GET` only. The small set of endpoints that render a form on `GET` and process
it on `POST` opt into `GET, POST` explicitly, while pure mutations stay
`POST` only. `HEAD` works on `GET` routes through middleware compatibility,
but generic `OPTIONS` is not part of the UI route contract and returns `405`
with `Allow: GET`.

## Registered routes

Handler symbols are in `server/fishtest/views.py`; template files are in
`server/fishtest/templates/`.

| Path | Method(s) | Handler | Template | Notes |
|------|-----------|---------|----------|-------|
| `/` | GET | `home()` | -- | `direct=True`: 302 to `/tests` without session or DB access |
| `/login` | GET, POST | `login()` | `login.html.j2` | CSRF; `no-store`; already-authenticated visitors are redirected to `/tests` |
| `/logout` | POST | `logout()` | -- | CSRF; clears the session and redirects to `/tests` |
| `/signup` | GET, POST | `signup()` | `signup.html.j2` | CSRF; `no-store`; reCAPTCHA verified server-side; success redirects to `/login` |
| `/tests` | GET | `tests()` | `tests.html.j2` | Main dashboard; `no-store`; page 1 live run tables poll the same route via `?live=run_tables`; `page=2` and beyond render finished results only |
| `/tests/run` | GET, POST | `tests_run()` | `tests_run.html.j2` | CSRF, primary; login required; success redirects to `/tests/view/{id}?follow=1` |
| `/tests/modify` | POST | `tests_modify()` | -- | CSRF, primary; redirects to `/tests` |
| `/tests/stop` | POST | `tests_stop()` | -- | CSRF, primary; redirects to `/tests` |
| `/tests/approve` | POST | `tests_approve()` | -- | CSRF, primary; redirects to `/tests` |
| `/tests/purge` | POST | `tests_purge()` | -- | CSRF, primary; redirects to `/tests` |
| `/tests/delete` | POST | `tests_delete()` | -- | CSRF, primary; redirects to `/tests` |
| `/tests/view/{id}` | GET | `tests_view()` | `tests_view.html.j2` | Full detail page; 404 for unknown run; renders server-owned Open Graph metadata with a query-free canonical URL and a Discord-oriented multi-line `og:description`; unfinished runs poll the merged detail fragment endpoint and the dedicated tasks endpoint |
| `/tests/view/{id}/detail` | GET | `tests_view_detail()` | `tests_view_detail_fragment.html.j2` | Fragment-only; no page-head metadata; OOB refresh for ELO, run status, active-worker totals, detail, time, chi-square, the retained SPSA data payload, and the hidden `expected` input |
| `/tests/live_elo/{id}` | GET | `tests_live_elo()` | `tests_live_elo.html.j2` | SPRT runs only; 404 otherwise. Live Elo page plus dual-scale gauge |
| `/tests/live_elo_update/{id}` | GET | `live_elo_update()` | `live_elo_fragment.html.j2` | Fragment-only (OOB into `#live-elo-data`); SPRT runs only; returns `286` once the SPRT reaches a terminal state |
| `/tests/stats/{id}` | GET | `tests_stats()` | `tests_stats.html.j2` | HX: `tests_stats_content_fragment.html.j2`; 404 for unknown run; active runs poll with the dedicated stats-page interval and visibility-aware refresh |
| `/tests/tasks/{id}` | GET | `tests_tasks()` | `tasks_content_fragment.html.j2` | Fragment-only; 404 for unknown run; replaces the scrolling task table body and refreshes fixed controls, hidden state inputs, and pagination out of band |
| `/tests/machines` | GET | `tests_machines()` | `machines_fragment.html.j2` | Fragment-only; route config sets `http_cache: 10`, so responses carry `Cache-Control: max-age=10` |
| `/tests/finished` | GET | `tests_finished()` | `tests_finished.html.j2` | HX: `tests_finished_results_fragment.html.j2` (the full page nests `tests_finished_content_fragment.html.j2`, which nests the results fragment) |
| `/tests/user/{username}` | GET | `tests_user()` | `tests_user.html.j2` | HX: `tests_user_content_fragment.html.j2`; `no-store`; 404 for unknown user; page 1 live run tables poll the same route via `?live=run_tables` |
| `/actions` | GET | `actions()` | `actions.html.j2` | HX: `actions_content_fragment.html.j2`; full-page responses render route-specific Open Graph metadata that preserves the current query string in `og:url` and summarizes the first visible action row |
| `/contributors` | GET | `contributors()` | `contributors.html.j2` | HX: `contributors_content_fragment.html.j2`; paginated with `CONTRIBUTORS_PAGE_SIZE` |
| `/contributors/monthly` | GET | `contributors_monthly()` | `contributors.html.j2` | HX: `contributors_content_fragment.html.j2`; paginated with `CONTRIBUTORS_PAGE_SIZE` |
| `/user/{username}` | GET, POST | `user()` | `user.html.j2` | CSRF; `no-store`; login required; approver-only for other users; 404 for unknown user |
| `/user` | GET, POST | `user()` | `user.html.j2` | CSRF; `no-store`; own profile form |
| `/user_management` | GET | `user_management()` | `user_management.html.j2` | HX: `user_management_content_fragment.html.j2`; `no-store`; approvers only |
| `/user_management/pending_count` | GET | `user_management_pending_count()` | `pending_users_nav_fragment.html.j2` | Sidebar status fragment; the stable wrapper and poll triggers live in `base.html.j2`, and this route returns only the `Users` anchor HTML |
| `/workers/{worker_name}` | GET, POST | `workers()` | `workers.html.j2` | CSRF; HX: `workers_content_fragment.html.j2`; `/workers/show` lists blocked workers, a three-part worker name opens the block/unblock form |
| `/upload` | GET, POST | `upload()` | `nn_upload.html.j2` | CSRF; login required; success redirects to `/nns` |
| `/nns` | GET | `nns()` | `nns.html.j2` | HX: `nns_content_fragment.html.j2` |
| `/sprt_calc` | GET | `sprt_calc()` | `sprt_calc.html.j2` | Client-side calculator; no server state |
| `/rate_limits` | GET | `rate_limits()` | `rate_limits.html.j2` | Server budget is read from the GitHub API at render time |
| `/rate_limits/server` | GET | `rate_limits_server()` | -- | Fragment-only; returns an inline `HTMLResponse` (no renderer) with the remaining count plus an OOB `#server_reset` span |

Static assets are served from the `/static` mount configured in
`server/fishtest/app.py`; they are not part of `_VIEW_ROUTES`.

Route notes:
- **Fragment-only**: endpoint always returns a fragment template (no full page).
- **HX**: dual-mode endpoint; returns the named fragment when the request is an
  htmx request, otherwise renders the full-page template.
- **OOB**: fragment contains `hx-swap-oob` attributes for multi-element updates.
- **`no-store`**: the handler calls `_append_no_store_headers()`, so the
  response carries `Cache-Control: no-store` and `Expires: 0` instead of the
  default `no-cache, private`.

## Access control by route

`_dispatch_view()` does not enforce authentication. Each handler checks access
itself, through `ensure_logged_in()` (login redirect) or
`_ViewContext.has_permission("approve_run")` (approver check).

| Route | Requirement | Behavior when not met |
|---|---|---|
| `/tests/run`, `/upload` | Logged in | 302 to `/login?next=<path>` with a `Please login` flash |
| `/user`, `/user/{username}` | Logged in; approver to view another user | 302 to `/login?next=<path>`, or a flash plus 302 to `/tests` |
| `/user_management` | Approver | Flash `You cannot view user management` plus 302 to `/tests` |
| `/workers/{worker_name}` (block/unblock form) | Logged in, and worker owner or approver | 302 to `/login?next=<path>`, or a flash and read-only rendering |
| `/tests/modify`, `/tests/stop`, `/tests/delete` | Logged in, and run owner or approver | 302 to `/login`, or a flash plus 302 to `/tests` |
| `/tests/approve` | Approver | 302 to `/login` with a `Please login as approver` flash |
| `/tests/purge` | Run owner or approver | 302 to `/login` |
| everything else | None | -- |

Approver status comes from `userdb.get_user_groups(username)` containing
`group:approvers`. Approvers additionally see owner email addresses on
`/workers/show` and can sort that table by email.

## Error and redirect responses

Error handlers are installed by `server/fishtest/http/errors.py` ->
`install_error_handlers()` and render HTML for UI paths.

| Situation | Status | Response |
|---|---|---|
| Unknown UI path, or a handler raising `HTTPException(404)` | 404 | `notfound.html.j2`, rendered by `http/ui_errors.py` -> `render_notfound_response()` |
| CSRF failure, or any UI 401/403 | 403 | `login.html.j2` with a `Please login` flash, rendered by `render_forbidden_response()` |
| Unhandled exception on a UI path | 500 | Plain text `Internal Server Error` |
| POST to a `require_primary` route on a secondary instance | 503 | Inline HTML `Primary instance required`, `Cache-Control: no-store` |
| Any request during shutdown | 503 | Empty plain-text body from `ShutdownGuardMiddleware` |
| Authenticated user who is blocked | 302 | Session cleared, redirect to `/tests`, from `RedirectBlockedUiUsersMiddleware` |
| Method not allowed on a UI route | 405 | `Allow` header lists the registered methods |

UI error pages still commit the session cookie, because rendering
`base.html.j2` touches the CSRF token and makes the session dirty.

## Sidebar status links

The sidebar contains two visibility-aware status links:

- `Users` is rendered inside the stable `#pending-users-nav` wrapper in
   `base.html.j2`. Full pages include the current count server-side, and that
   same wrapper mounts the visibility-aware htmx poll to
   `/user_management/pending_count`; the fragment route itself returns only the
   anchor HTML.
- `GitHub Rate Limits` uses separate cadences: `POLL_RATE_LIMITS_GITHUB_S` for
   the browser-side GitHub rate-limit poll in shared JavaScript, which updates
   the sidebar link on full pages and also updates the `/rate_limits` client
   row when that page is open, and `POLL_RATE_LIMITS_SERVER_S` for the
   `/rate_limits/server` htmx row. The sidebar link itself is rendered directly
   in `base.html.j2`.

All poll cadences are settings-backed constants in
`server/fishtest/http/settings.py`, projected into templates as the Jinja2
global `poll`.

## Tests Repository URL Contract

The `Tests Repository` field used on `/signup`, `/user`, and `/tests/run`
accepts a GitHub repository URL with or without a trailing slash.

Server-side behavior:

- raw form validation accepts either `https://github.com/<user>/<repo>` or
   `https://github.com/<user>/<repo>/`
- user profile data is canonicalized to the slash-free form
- persisted user and run validation requires the slash-free canonical form
- run data uses the slash-free form in `run["args"]["tests_repo"]`

Canonical stored value:

- `https://github.com/<user>/<repo>`

This keeps GitHub compare links and worker download URLs stable even when the
submitted form value includes a trailing slash.

## `/tests/view/{id}/detail` live detail contract

The test detail page keeps its live summary and detail data synchronized
through the fragment-only `/tests/view/{id}/detail` endpoint. This endpoint
is the live detail-page poll contract.

`tests_view.html.j2` mounts the poller only for unfinished runs, with:

- `hx-get="/tests/view/{id}/detail"`
- `hx-include="#tests-view-detail-expected"`
- `hx-swap="none"`
- a visibility-aware `hx-trigger` on the `poll.tests_view_detail` cadence

The response updates these regions out of band:

- `#elo-<run_id>`
- `#run-status-<run_id>`
- `#tasks-totals`
- `#tests-view-details`
- `#tests-view-time`
- `#tests-view-stats` for non-SPSA runs
- `#spsa-data-<run_id>` for SPSA runs
- `#tests-view-detail-expected`, the hidden input carrying the current status

Page-head metadata contract:

- The full `/tests/view/{id}` page owns `<title>`, Open Graph tags, and the
   optional `theme-color` meta tag.
- The `/tests/view/{id}` `og:description` is emitted as plain multi-line text
   so Discord previews preserve the run-summary layout without literal
   backticks.
- The fragment-only `/tests/view/{id}/detail` endpoint never renders or updates
   page-head metadata.
- Shared links and social preview clients must fetch the full page URL, not the
   fragment URL.

For SPSA runs, the detail fragment updates the existing
`#spsa-data-<run_id>` node in place. The page-owned `spsa.js` controller keeps
the chart shell mounted, draws the chart at a fixed 1000x500 size inside the
scrollable container, skips redraws when the embedded JSON payload is unchanged,
and redraws changed payloads in place without replacing the chart shell.
The full `/tests/view/{id}` page also remembers the `% c` checkbox in the
`spsa_percentage` cookie, so the checkbox reopens in its previous mode while
live detail polling continues to update only the embedded SPSA data node.

The inline Diff panel on `/tests/view/{id}` is rendered in the browser by the
`jsdelivr`-hosted `diff` library, loaded with Subresource Integrity from
`tests_view.html.j2`. That script is loaded only by this page and is not part
of the `/tests/view/{id}/detail` fragment contract.

The request submits the page's current canonical `expected` state from a
server-owned hidden input. For an unfinished run the value is one of:

- `active`
- `paused`
- `pending`

Server behavior for htmx polling:

- `286` returns the final fragment and stops polling when the run is terminal
   (`finished` or `failed`).
- `204` keeps the current DOM when the run is not active and the page already
   shows the current status, that is when `expected` is absent or equal to the
   server-side status.
- `200` returns fresh OOB detail content in every other case, including a run
   that is still paused or pending but whose status no longer matches the
   `expected` value the page submitted.

Non-htmx requests to `/tests/view/{id}/detail` render the same fragment
template as a standalone document; the endpoint has no full-page mode.

## `/tests/live_elo/{id}` gauge scale contract

The Live Elo page serves SPRT runs only; any other run id returns `404`. It
renders three Google gauges (LOS, LLR, Elo) and keeps the details table in sync
through `/tests/live_elo_update/{id}`, which swaps `#live-elo-data` out of band
on the `poll.live_elo` cadence. Once the SPRT reaches a terminal state the
update endpoint answers `286` and the poller stops.

The Elo gauge supports two display modes:

- Fixed mode (default): fixed gauge range `[-4, +4]` for visual consistency.
- Dynamic mode: auto range chosen from the smallest symmetric power-of-two
   interval that covers the current Elo value and confidence interval.

Switching modes:

- Clicking the Elo gauge toggles between fixed and dynamic mode.
- Keyboard activation on the Elo gauge (`Enter` or `Space`) also toggles modes.
- The gauge title and `aria-label` state which mode the next activation
   selects.
- The chosen mode is persisted in the `live_elo_mode` cookie by
   `static/js/live_elo.js`, so the page reopens in the last used mode.

Value display rule:

- The gauge reports the real uncapped Elo value from the server.
- In fixed mode, the gauge needle is visually limited by the selected range.

## `/tests/stats/{id}` raw statistics contract

The raw statistics page is dual-mode:

- Full-page navigation renders `tests_stats.html.j2`.
- `HX-Request: true` renders `tests_stats_content_fragment.html.j2`.

The page shell keeps a visibility-aware poller for unfinished non-SPSA runs:

- `every {{ poll.tests_stats }}s [document.visibilityState === 'visible']`
- `visibilitychange[document.visibilityState === 'visible'] from:document`

Server behavior for htmx polling:

- `200` when the run is active, returning the refreshed stats fragment.
- `204` when the run is not active but not terminal, keeping the current DOM.
- `286` when the run is terminal (`finished` or `failed`), returning the final
   fragment and stopping the poller.

Layout contract:

- the shared fragment preserves the original heading-and-table statistics
   presentation from the page shell;
- genuinely tabular data, such as SPRT bounds, remains a table;
- SPSA runs render an informational message instead of raw statistics.

## htmx fragment dispatch

Dual-mode endpoints (marked **HX** in the route table) serve either a full
HTML page or a fragment, from the same URL, based on request headers.

Use this rule to predict which template a request renders:

1. If the route has no dual-mode branch (no `_render_hx_fragment()` call), it
   always renders the template named by its `renderer` config key.
2. Otherwise, if `_is_hx_request(request)` is true, it renders the fragment
   template named in the handler's `_render_hx_fragment()` call.
3. Otherwise it renders the template named by its `renderer` config key.

### Detection: `_is_hx_request(request)`

Defined in `server/fishtest/views_helpers.py`. Returns `True` when both of the
following hold:

1. The request carries `HX-Request: true` (case-insensitive).
2. `Sec-Fetch-Mode` is not `navigate` (blocks full-page navigations that
   carry `HX-Request` due to htmx-boosted links or browser prefetch).

### Rendering: `_render_hx_fragment(request, template_name, context)`

Checks `_is_hx_request()` and, when true, renders the fragment template through
`render_template_to_response()`. Returns `None` for non-htmx requests, so the
caller can fall through to full-page rendering:

```python
response = _render_hx_fragment(request, "my_fragment.html.j2", context)
return response or context
```

`_render_hx_or_context(request, template_name, context, extra_context=None)`
wraps that pattern and optionally merges extra keys into the fragment-only
context:

```python
return _render_hx_or_context(
    request,
    "my_fragment.html.j2",
    context,
    extra_context={"is_hx": True},
)
```

### `Vary: HX-Request` header

`_dispatch_view()` appends `Vary: HX-Request` to every GET response
(both fragment and full-page). This tells HTTP caches (nginx, CDNs,
browsers) that the response body depends on the `HX-Request` header,
preventing a cached fragment from being served as a full page or vice versa.

### Polling status codes

Polled fragment endpoints steer the htmx polling lifecycle with status codes:

- `200` -- swap the response content and keep polling.
- `204` -- empty body; htmx skips the swap and keeps polling.
- `286` -- swap the response and stop polling.

Handlers request an empty `204` by setting `request.response_status = 204`;
`_dispatch_view()` then returns an empty `HTMLResponse` instead of rendering
the template.

## `_dispatch_view()` pipeline

For every UI request (except `direct` routes), `_dispatch_view()` handles the
following steps in order:

1. **Request context assembly** -- calls `get_request_context(request)` to load
   the session and DB handles.
2. **POST body parsing** -- on POST only, `await request.form()` with the
   limits from `http/settings.py` (`UI_FORM_MAX_FILES`, `UI_FORM_MAX_FIELDS`,
   `UI_FORM_MAX_PART_SIZE_BYTES`).
3. **CSRF enforcement** -- on POST only, and only when `require_csrf` is set:
   validates the token from the form field or `X-CSRF-Token` header against
   the session token.
4. **`_ViewContext` construction** -- bundles request, session, POST data,
   path params, and DB handles into a single object passed to the handler.
5. **Primary-instance guard** -- on POST only, when `require_primary` is set
   and the instance is not primary, returns a 503 HTML response.
6. **Handler execution** -- `await run_in_threadpool(fn, shim)` runs the
   sync handler body in the threadpool.
7. **Response passthrough** -- if the handler returned any `Response` object
   (redirect, fragment, or inline HTML), it is used as-is.
8. **Template rendering** -- otherwise, if the handler set
   `response_status = 204` the response is an empty `204`; if `renderer` is
   set the dict result is rendered through `render_template_to_response()`;
   if neither applies the response is an empty `204`.
9. **Session commit** -- `commit_session_response()` applies remember/forget
   flags to the request scope, and the session middleware writes the cookie.
10. **HTTP cache headers** -- `apply_http_cache()` sets `Cache-Control` when
    the route config has `http_cache`.
11. **GET defaults** -- `Vary: HX-Request` is appended and `Cache-Control`
    defaults to `no-cache, private` when nothing else set it.
12. **Response headers** -- headers and raw `Set-Cookie` lines queued by the
    handler are applied last, so handler values such as `no-store` win over
    the GET default.

## Session handling

### Storage

`FishtestSessionMiddleware` (pure ASGI, `http/session_middleware.py`) manages
session persistence. Session data lives in `request.scope["session"]` as a
plain dict, wrapped by `CookieSession` (`http/cookie_session.py`) for helper
access.

### Cookie format

- Signed with `itsdangerous.TimestampSigner`.
- Cookie name: `fishtest_session`.
- Encoding: base64(JSON(session_data)), signed.
- Attributes: `path=/`, `HttpOnly`, `SameSite=Lax`, and `Secure` when the
  original request was HTTPS (`X-Forwarded-Proto` is honored).
- `Max-Age` and `Expires` are emitted only when a persistence override is
  active; otherwise the cookie is a browser-session cookie.
- Signing key comes from `FISHTEST_AUTHENTICATION_SECRET`. Without it the
  server refuses to start unless `FISHTEST_INSECURE_DEV` is set.
- Maximum cookie value size: `SESSION_COOKIE_VALUE_MAX_BYTES` in
  `http/settings.py`. Oversized payloads are shrunk by dropping flash messages
  one at a time, then all flashes, then everything except the identity and
  persistence keys.
- An empty session clears the cookie: the middleware emits an expired
  `Set-Cookie` when the session became empty or `session_force_clear` is set.

Cookie ownership is split intentionally:

- `http/settings.py` owns cookie policy values such as size limits and
   persistence windows.
- `http/cookie_session.py` owns the session cookie transport contract such as
   the cookie name, SameSite default, secret resolution, and request-scope
   override helpers.
- `http/ui_cookies.py` owns browser-readable non-auth UI cookie names, shared
   low-level parsing helpers, and the raw `Set-Cookie` formatting reused by
   UI views.

### Session keys

| Key | Type | Description |
|-----|------|-------------|
| `user` | string | Authenticated username |
| `csrf_token` | string | CSRF token (generated on first access) |
| `flashes` | dict | Flash message queues (`error`, `warning`, default) |
| `created_at` | string | ISO timestamp of session creation |
| `remember_max_age` | int | Persistence window written by "remember me" login |

### Per-request overrides

- `scope["session_max_age"]` -- overrides cookie `Max-Age`; "remember me" login
  sets it to `SESSION_REMEMBER_ME_MAX_AGE_SECONDS`.
- `scope["session_secure"]` -- overrides the `Secure` flag.
- `scope["session_force_clear"]` -- forces an expired cookie on the response
  (used by logout and by the blocked-user middleware).

## Cookie workflow

The UI uses two cookie families with different ownership and lifecycles:

- **Session/auth cookie** -- `fishtest_session`, emitted by
   `FishtestSessionMiddleware`.
- **UI state cookies** -- browser-readable, non-signed preferences such as
   `theme`, `contributors_findme`, `machines_state`, the tasks-table state, and
   the homepage workers filters. They are written with the shared long-lived
   policy from `UI_STATE_COOKIE_MAX_AGE_SECONDS` (`path=/`, `SameSite=Lax`) and
   stay separate from the signed session payload.

### Session cookie lifecycle

1. `FishtestSessionMiddleware` reads `fishtest_session` at request start and
    decodes the signed JSON payload into `scope["session"]`.
2. `load_session()` wraps that dict in `CookieSession`, which lazily creates
    `created_at` and the CSRF token on first access.
3. Rendering most full-page templates touches `session.get_csrf_token()`, so a
    previously empty session becomes dirty during the response.
4. On login, `remember()` writes `session["user"]` and, for "remember me",
    stores `remember_max_age` and marks the request scope for a persistent
    cookie.
5. On logout, `forget()` clears the session and marks the response to emit an
    expired cookie.
6. `commit_session_response()` translates those flags into request-scope
    overrides, and `FishtestSessionMiddleware` finally appends the `Set-Cookie`
    header on the outbound response.

### UI state cookie names

Names are declared once in `server/fishtest/http/ui_cookies.py`.

| Cookie | Written by | Purpose |
|---|---|---|
| `theme` | `static/js/application.js` | Light or dark presentation; also read server-side so the first paint matches |
| `login_remember_me` | `POST /login` and `static/js/application.js` | Remembers the explicit Remember me choice for the login form |
| `master_only` | `static/js/application.js` via `data-ui-cookie-*` on `/nns` | Remembers the Only master filter |
| `contributors_findme` | `static/js/contributors.js` | Remembers rank-jump mode across contributors pages |
| `machines_state` | `static/js/tests_homepage.js` | Whether the homepage Workers panel is expanded |
| `machines_sort`, `machines_order`, `machines_page`, `machines_q`, `machines_my_workers` | `views_machines.py` through `http/ui_cookies.py` | Effective `/tests/machines` filter and sort state |
| `tasks_sort`, `tasks_order`, `tasks_view`, `tasks_q` | `views.py` -> `_set_tasks_cookies()` | Effective tasks-table state on `/tests/view/{id}` and `/tests/tasks/{id}` |
| `tasks_state` | inline script in `templates/tests_view.html.j2` | Whether the Tasks panel on `/tests/view/{id}` is expanded |
| `spsa_percentage` | `static/js/application.js` via `data-ui-cookie-*` | State of the `% c` checkbox on SPSA detail pages |
| `live_elo_mode` | `static/js/live_elo.js` | Fixed or dynamic Elo gauge scale |
| `active_run_filters` | `static/js/active_run_filters.js` | Active runs filter selection across test type, time control, and threads |
| `active_run_filters_panel` | `static/js/active_run_filters.js` | Whether the Active runs filter controls are shown |
| `<panel>_state` | shared panel-toggle handler in `static/js/application.js` | Show or Hide state of each run-table panel (`pending`, `paused`, `failed`, `active`, `finished`) |

Run-table panel cookies are namespaced per page: `/tests` uses the bare names,
while `/tests/user/{username}` prefixes them with a per-user token so one
user's collapsed panels do not affect another user's page.

Server-written UI cookies are queued as raw `Set-Cookie` lines through
`append_ui_cookie()` so they coexist with the signed session cookie on the same
response.

This split keeps auth/session semantics on the server-controlled signed cookie
while letting low-risk UI preferences remain simple, readable browser state.

## CSRF protection

- A token is generated per session on first access
  (`CookieSession.get_csrf_token()`).
- Validated on POST requests to routes whose config sets `require_csrf`.
  GET requests are never CSRF-checked.
- The token can be submitted as:
  - A form field `csrf_token`.
  - An HTTP header `X-CSRF-Token`. The header wins when both are present.
- Validation uses `secrets.compare_digest()` for timing-safe comparison.
- Failure raises `HTTPException(403)`, which the UI error handler renders as
  the login page with status `403` (not JSON).

## Authentication

### Login flow

1. `GET /login` renders the Remember me checkbox (`stay_logged_in`) checked by
   default.
2. If the `login_remember_me` UI cookie stores an explicit opt-out, the same
   page renders the checkbox unchecked on later visits.
3. User submits username/password to `POST /login`.
4. `UserDb.authenticate()` validates credentials. On failure the reason is
   flashed; a `pending` account gets the extra "waiting for approval" text.
5. On success, `remember(request, username)` sets `session["user"]`.
6. Checked Remember me passes `SESSION_REMEMBER_ME_MAX_AGE_SECONDS`, so the
   signed session cookie becomes persistent. Unchecked login keeps the auth
   cookie scoped to the current browser session.
7. `POST /login` also refreshes `login_remember_me` so the user's explicit
   checkbox choice survives later browser starts.
8. The response redirects to the `next` query parameter when present, otherwise
   to `came_from`; with neither present it redirects to `/`, which forwards to
   `/tests`. Visiting `/login` while already authenticated redirects straight
   to `/tests`.

### Logout flow

1. `POST /logout` calls `forget(request)` and invalidates the session.
2. The session dict is cleared and `session_force_clear` is set, so the
   response emits an expired cookie.
3. Redirect to `/tests`.

### Access control

- `ensure_logged_in(request)` returns the username, or a `RedirectResponse` to
  `/login?next=<current path and query>` with a `Please login` flash. Callers
  must check the return type; there is no exception-based redirect.
- Group-based authorization: `userdb.get_user_groups(username)` returns group
  memberships. `_ViewContext.has_permission("approve_run")` is the only
  supported permission and checks for `group:approvers`; every other permission
  name returns `False`.
- See "Access control by route" for the per-route requirements.

## URL generation

- UI routes are registered without a route `name`, so templates do not use
  `url_for()`. Navigation URLs come from the `urls` dict built by
  `http/boundary.py` -> `build_template_context()`; everything else is a
  literal path.
- `static_url(spec)` (`http/jinja.py`) accepts the legacy
  `fishtest:static/<relative path>` spec, maps it to `/static/<relative path>`,
  and appends a cache-busting `x=` query parameter holding the URL-safe base64
  SHA-384 digest of the file contents. Files outside the static directory, or
  files that cannot be read, get the plain URL with no query parameter.
- `static_url` is exposed both as a Jinja2 global and in the shared template
  context.

## Navigation behavior

- The sidebar `Users` link shows `Users (N)` when `N` users are pending
   approval.
- With JavaScript enabled, that link refreshes its pending-user count
   periodically while the tab is visible and refreshes again when the tab
   becomes visible via `visibilitychange`.
- The sidebar `GitHub Rate Limits` link keeps its text fixed and reflects the
   browser-side GitHub client budget used by pages that read the token from
   local storage.
- When the client budget falls below the current warning threshold, the link
   switches to the same red status styling used by the pending-users sidebar
   item.
- The sidebar reuses the last known client warning state from local storage on
   first paint, so page navigation does not flash the link back to its normal
   color before the client poll completes.
- The browser-side GitHub poll runs on every page that renders the sidebar,
  pauses while the tab is hidden, refreshes immediately on
  `visibilitychange`, window `focus`, and bfcache `pageshow`, and then resumes
  its normal cadence from that activation point.
- When JavaScript is unavailable, the same link still works as normal
   navigation and the count refreshes on the next full-page render.

## Adding a new UI route

1. Write a sync handler function that receives a `_ViewContext` and returns
   either a dict (for template rendering) or a `Response`.

2. Add an entry to `_VIEW_ROUTES` in `server/fishtest/views.py`:
   ```python
   (my_handler, "/my/path", {"renderer": "mypage.html.j2", "require_csrf": True})
   ```

3. Create the Jinja2 template in `server/fishtest/templates/`.

4. Enforce authentication or permissions inside the handler; the dispatcher
   does not do it for you.

5. Add a contract test under `server/tests/` (for example
   `test_views_routes.py` for the method contract, or a page-specific
   `test_views_*.py` file). `test_views_routes.py` asserts the full set of
   declared paths, so a new route must be listed there.

## Homepage (`/tests`) query parameters

| Parameter | Values | Default |
|-----|-----|-----|
| `page` | integer `>= 1` | `1` |
| `live` | `run_tables` | absent |

Behavior notes:

- Page 1 renders the full dashboard: pending, paused, failed, active, and
   finished run tables, the summary stats, and the Workers panel.
- `page=2` and beyond render finished results only.
- `live=run_tables` is the live-refresh contract for the run tables. On an htmx
   request it returns `tests_run_tables_fragment.html.j2`, which swaps the
   `pending`, `paused`, `failed`, `active`, and `finished` table bodies plus the
   panel counts out of band, and on `/tests` also refreshes the workers counter
   and the homepage summary stats.
- The live fragment answers `286` when there are no pending, paused, or active
   runs, which stops the poller until the next full-page load.
- A non-htmx request to `?live=run_tables` is redirected to the same URL
   without the `live` parameter, so the link is never a dead end.
- `/tests/user/{username}` accepts the same `page` and `live` parameters and
   uses the same fragment, minus the workers counter and summary stats.
- The homepage carries `Cache-Control: no-store` because it mixes
   user-specific state with frequently refreshed content.

## Homepage workers (`/tests/machines`) query parameters

The homepage workers fragment (`/tests/machines`) supports URL-driven state for
server sorting, paging, and lightweight filtering.

| Parameter | Values | Default |
|-----|-----|-----|
| `sort` | `last_active`, `machine`, `cores`, `uuid`, `mnps`, `ram`, `system`, `arch`, `compiler`, `python`, `worker`, `running_on` | `last_active` |
| `order` | `asc`, `desc` | column default |
| `page` | integer `>= 1` | `1` |
| `q` | free-text filter matched against any displayed machine column | empty |
| `my_workers` | `1`, `true`, `on`, `yes` | absent/false |

Behavior notes:

- Sorting is server-authoritative and stable with username asc tie-breaks.
- Pagination uses `MACHINES_PAGE_SIZE` from `http/settings.py`; links are
   omitted for one-page result sets.
- Out-of-range `page` values are clamped to the last page.
- `my_workers` only applies for authenticated users; anonymous requests ignore
   this filter, and the checkbox is not rendered for them.
- `q` performs case-insensitive substring matching across all displayed table
   columns (machine, cores, UUID, MNps, RAM, system, arch, compiler, python,
   worker, running-on, and last-active text).
- The filter control is an `<input type="search">` labelled
   `Filter any column`; the owner filter is a checkbox labelled `My workers`.
- Sortable headers are links; the active column carries `aria-sort`
   (`ascending` or `descending`), and the table has a visually hidden
   `Machines` caption.
- Polling includes the current homepage filter-form state (`hx-include`), so
   sort/filter/page settings persist across periodic refreshes, including while
   the `q` search filter remains active.
- Polling runs only while the tab is visible and the Workers panel is expanded.
- Filter controls (`q`, `my_workers`) are rendered on `/tests` outside the
   swapped machines fragment to avoid input focus/caret glitches during table
   refresh swaps.
- The fragment refreshes `#machines-pagination`, the hidden `sort`, `order`,
   and `page` inputs, and the `#workers-count` label out of band while swapping
   the table into `#machines`.
- `/tests/machines` responses persist the effective `sort`, `order`, `page`,
   `q`, and `my_workers` values in cookies, so returning to `/tests` restores
   the last machines filter state.
- Workers counter semantics are stable across `/tests`, `/tests/machines`, and
   the page-1 `/tests?live=run_tables` OOB updates:
  - no active filters: `Workers - <total>`
  - active `q` and/or `my_workers`: `Workers - <total> (<filtered>)`
- When workers filters are active and the Workers panel is collapsed, `/tests`
   and its page-1 live run-table fragment recompute the filtered value from the
   current machine snapshot instead of reusing the last cookie-backed filtered
   count.
- Opening the panel triggers an immediate `/tests/machines` refresh instead of
   waiting for the next periodic poll.
- Machines sorting is fully server-authoritative; there is no client-side
   header sorter.

## Active runs type filter

The Active runs panel on `/tests` provides client-side checkboxes for
filtering visible runs.  The controls use a
compact ellipsis toggle plus three independent dimensions matching the
new-test submission page:

| Dimension | Checkboxes | Classification |
|-----------|------------|---------------|
| Test type | SPRT, SPSA, NumGames | `spsa` if SPSA params present; `sprt` if SPRT bounds present; `numgames` otherwise |
| Time control | STC, LTC | LTC when `get_tc_ratio(tc, threads) > 4`, else STC |
| Threads | ST, SMP | SMP when `threads > 1`, else ST |

An "All" master checkbox checks or unchecks all dimension checkboxes.

Filtering uses AND between dimensions and OR within each dimension:
a row is visible when it matches at least one checked value in **every**
dimension.

Behavior notes:

- Each table row carries three `data-*` attributes (`data-test-type`,
   `data-time-control`, `data-threads`) with a single value per dimension,
   computed by the server template.
- After the page is loaded, filtering remains client-side and uses those
   server-rendered attributes.
- The filter controls sit inside the Active panel's collapse section
  (matching the workers table filter placement).
- The Active filter bar remains visible even when there are zero active runs,
   so users can inspect or change persisted filter state before new runs appear.
- The ellipsis toggle shows or hides the filter controls without leaving
   the Active panel.
- Checkboxes are grouped by dimension with small text labels (Type, TC,
   Threads).  On desktop the controls stay on one inline row.  On mobile
   the ellipsis stays pinned at the left while the filter grid opens beside
   it, with the All checkbox on the first row and aligned checkbox columns.
- The "All" checkbox uses the indeterminate state when some (but not all)
  checkboxes are checked.
- The Active header keeps parentheses whenever a non-`All` filter state is
   active, even when the filtered count equals the total. Parentheses disappear
   only when the effective filter state is truly `All`.
- The panel header count updates to show both total and filtered counts
  when a filter is active: "Active - N (M) tests".
- Filter state is persisted in the `active_run_filters` cookie using the shared
   long-lived UI-cookie policy (`path=/`, `SameSite=Lax`). When all checkboxes
   are checked the cookie is cleared.
- On page reload, `/tests` restores the cookie-backed filter state in the first
   HTML response, including the checkbox state, filtered count text, and initial
   row-hide CSS. Hidden categories therefore do not flash briefly before the
   browser restores the filter logic.
- Active-row zebra striping follows the same visible-row pattern as the other
   filtered tables. The Active filter logic keeps the visible rows contiguous in
   tbody order, so the normal alternating darker or lighter `table-striped`
   pattern stays correct after checkbox changes and after homepage polling
   replaces the Active tbody. The striping contract does not depend on hidden
   categories retaining their original sibling positions.
- Clearing every checkbox persists as an explicit empty selection, so reloads
   keep the Active panel empty until categories are enabled again.
- The filter panel open/closed state is persisted in the
   `active_run_filters_panel` cookie.
- Filters are re-applied after htmx OOB swap updates to the active runs
  tbody, so periodic poll refreshes respect the current filter state.
- Notification bell buttons initialize from browser-local follow state after
   page load and stay hidden until that state is known, which avoids transient
   wrong bell icons during reload.

## User management (`/user_management`) query parameters

The user-management page is approver-only and supports URL-driven
server-authoritative table state. Non-approvers get a flash message and a
redirect to `/tests`.

| Parameter | Values | Default |
|-----|-----|-----|
| `group` | `all`, `pending`, `blocked`, `idle`, `approvers` | `pending` |
| `sort` | `username`, `registration`, `groups`, `email` | `registration` |
| `order` | `asc`, `desc` | column default |
| `page` | integer `>= 1` | `1` |
| `q` | free-text filter matched against username column | empty |
| `view` | `paged`, `all` | `paged` |

Behavior notes:

- Sorting is server-authoritative and stable with username tie-breaks.
- Pagination uses `USER_MANAGEMENT_PAGE_SIZE` in paged view.
- `view=all` returns all matching rows up to `USER_MANAGEMENT_MAX_ALL` and
   hides pagination controls; the page states when the list was truncated.
- `q` performs case-insensitive substring matching on username only.
- User-management filtering stays on the userdb path: the base list comes from
   `request.userdb.get_users()`, pending/blocked subsets come from the
   cached `get_pending()` / `get_blocked()` helpers, and `idle` is the set of
   users with no `user_cache` entry.
- Unrecognized `group`, `sort`, `order`, and `view` values fall back to the
   defaults instead of erroring.
- Per-user actions (approve, block, remove) are POSTed to `/user/{username}`,
   not to this route.
- htmx requests target `#user-management-content` and keep URL state via
   `hx-push-url="true"`.
- The outer GET form keeps `sort`, `order`, and `view` in hidden inputs.
  htmx fragment responses refresh those hidden inputs out of band so later
  group or filter changes preserve the current table state.
- The page carries `Cache-Control: no-store`.
- Table sorting is fully server-authoritative; there is no client-side header
   sorter.

## Workers (`/workers/{worker_name}`)

One route serves two pages:

- `/workers/show` lists blocked workers.
- `/workers/<host>-<cores>-<uuid>` (a three-part short worker name) opens the
   block/unblock form for that worker. It requires a logged-in user who is
   either the worker owner or an approver; anyone else sees the list with a
   flash message. A successful `POST` records a `block_worker` action and
   redirects to `/workers/show`. Issue descriptions longer than 500 characters
   are truncated with a warning.

Anything that is neither `show` nor a valid short worker name is flashed as a
validation error and falls through to the list.

### Blocked-workers table query parameters

The blocked-workers table supports URL-driven server-authoritative state.

| Parameter | Values | Default |
|-----|-----|-----|
| `filter` | `all-workers`, `le-5days`, `gt-5days` | `le-5days` |
| `sort` | `worker`, `last_changed`, `events`, `email` | `last_changed` |
| `order` | `asc`, `desc` | column default |
| `page` | integer `>= 1` | `1` |
| `q` | free-text filter matched against worker column | empty |
| `view` | `paged`, `all` | `paged` |

Behavior notes:

- `filter` keeps the server-side time-window behavior.
- Sorting is server-authoritative and stable with worker-name tie-breaks.
- Pagination uses `WORKERS_PAGE_SIZE` in paged view.
- `view=all` returns all matching rows up to `WORKERS_MAX_ALL` and hides
   pagination controls.
- `q` performs case-insensitive substring matching on worker name only.
- The Email column and the owner mailto links are rendered for approvers only.
- Non-approver users cannot sort by `email`; unsupported values fall back to
   default server sort.
- htmx requests target `#workers-content` and keep URL state via
   `hx-push-url="true"`.
- Sort-header links are dual-mode (`href` + `hx-get`): workers sorting swaps
   `#workers-content` with `hx-push-url="true"` when htmx is active, and still
   degrades to full-page navigation.
- The outer GET form keeps `sort`, `order`, and `view` in hidden inputs.
  htmx fragment responses refresh those hidden inputs out of band so later
  filter changes preserve the current table state.
- Table sorting is fully server-authoritative; there is no client-side header
   sorter.

## Contributors query parameters

The contributors pages (`/contributors` and `/contributors/monthly`) support
URL-driven state for server sorting, search, paging, full view, and rank jump.

| Parameter | Values | Default |
|-----|-----|-----|
| `search` | one-shot go-to query (exact username first, then first substring match) | empty |
| `sort` | `cpu_hours`, `username`, `last_updated`, `games_per_hour`, `games`, `tests`, `tests_repo` | `cpu_hours` |
| `order` | `asc`, `desc` | column default |
| `page` | integer `>= 1` | `1` |
| `view` | `paged`, `all` | `paged` |
| `findme` | any truthy value | absent |
| `highlight` | username | empty |

Behavior notes:

- Sorting is server-authoritative and stable with username asc tie-breaks.
- Rank is global for the filtered+sorted dataset (not page-local loop index).
- Pagination uses `CONTRIBUTORS_PAGE_SIZE`.
- `findme` and `search` both resolve to a target username and then issue a 302
   to the page holding that row, carrying `sort`, `order`, `view`,
   `highlight=<username>`, the sticky `findme` flag, and (in paged view) the
   target `page`, with an `#me` fragment. `findme` only works for
   authenticated users and takes precedence over `search`.
- `view=all` returns all rows up to `CONTRIBUTORS_MAX_ALL` and hides
   pagination; the page states when the list was truncated.
- `search` is consumed as one-shot navigation intent and is not preserved in
   pagination/sort/view links, preventing repeated jumps during later browsing.
- A `highlight` value that matches no row is dropped.
- `/contributors` and `/contributors/monthly` stay on the userdb fast path:
   the all-time page reads from `userdb.user_cache`, and the monthly page reads
   from `userdb.top_month`, which the maintenance job rebuilds from unfinished
   runs plus finished runs started within the last 30 days.
- Sort-header links are dual-mode (`href` + `hx-get`): contributors sorting
   swaps `#contributors-content` with `hx-push-url="true"` when htmx is active,
   and still works as normal navigation when JavaScript is unavailable.
- The outer search form keeps `sort`, `order`, and `view` as hidden inputs.
   htmx fragment responses refresh those inputs out of band so later search or
   rank-jump requests do not replay stale table state.

## Neural networks (`/nns`) query parameters

The neural network repository page (`/nns`) supports URL-driven state for
search, sorting, paging, and full view.

| Parameter | Values | Default |
|-----|-----|-----|
| `network_name` | literal network name substring (case-insensitive) | empty |
| `user` | literal uploader username substring (case-insensitive) | empty |
| `master_only` | `1`, `true`, `on`, `yes` | cookie value, else false |
| `sort` | `time`, `name`, `user`, `first_test`, `last_test`, `downloads` | `time` |
| `order` | `asc`, `desc` | `desc` |
| `page` | integer `>= 1` | `1` |
| `view` | `paged`, `all` | `paged` |

Behavior notes:

- The page renders four summary cards above the table for the current filtered
   result set: Nets, Master nets, Contributors, and Downloads.
- The explanatory CC0 and default-net copy is rendered under the summary cards
   and above the paged or all view controls.
- Sorting is server-authoritative and deterministic, with a network-name
   tie-break so rows do not jitter between requests.
- Pagination uses `NNS_PAGE_SIZE`; `view=all` caps the list at `NNS_MAX_ALL`
   and reports truncation.
- Sort-header links are dual-mode (`href` + `hx-get`): sorting swaps
   `#nns-content` with `hx-push-url="true"` when htmx is active, with plain-link
   fallback preserved.
- Pagination links follow the same dual-mode contract.
- Search inputs are `<input type="search">` controls labelled `Network` and
   `Uploaded by`; they are htmx-triggered on debounced input and on the native
   search-clear event, and also support explicit submit for keyboard and non-JS
   flows.
- `network_name` and `user` are treated as literal substring filters;
  regex metacharacters have no special meaning.
- htmx updates target `#nns-content` and push updated query URLs for
   back/forward and shareable links.
- The filter form is rendered inside `#nns-content`, under the explanatory
  copy and above the paged or all switch, so htmx responses keep the full page
  order aligned with the other card pages: cards, text, filters, view switch,
  pagination, table, pagination.
- `master_only` checkbox preference is persisted in a cookie and reused when
   the query parameter is not present. The form also submits an explicit false
   fallback so unchecked htmx requests and cookie persistence stay aligned.
- Table sorting is fully server-authoritative; there is no client-side header
   sorter.

## Actions (`/actions`) query parameters

The actions log supports URL-driven server-authoritative filtering, paging,
and sort state on the canonical `/actions` route.

| Parameter | Values | Default |
|-----|-----|-----|
| `action` | action name filter | empty |
| `user` | username substring | empty |
| `text` | Mongo text-search query | empty |
| `run_id` | run id | empty |
| `page` | integer `>= 1` | `1` |
| `max_count` | positive integer, capped by auth state | route policy |
| `sort` | `time`, `event`, `source`, `target`, `comment` | `time` |
| `order` | `asc`, `desc` | `desc` for `time`, otherwise `asc` |
| `before` | action timestamp cursor for time-link deep links | absent |

Behavior notes:

- Pagination uses `ACTIONS_PAGE_SIZE` from `http/settings.py`. A `page` value
   beyond the last page redirects (302) to the last page with the effective
   `max_count`, `sort`, and `order` values.
- Sorting is server-authoritative. The default `time desc` path stays on the
   indexed fast query; explicit alternate sorts materialize and sort the
   capped working set server-side, and the page states the sorted scope.
- Anonymous requests are capped at `_ANONYMOUS_RESULT_LIMIT_HARD` actions
   (`views_helpers.py`). Authenticated requests default to
   `_ACTIONS_DEFAULT_MAX_COUNT_AUTH` (`views_actions.py`); explicit `max_count`
   values are preserved in the URL and hidden form state.
- htmx requests target `#actions-content` and keep URL state via
   `hx-push-url="true"`.
- The visible filters auto-submit on select change and search/input events.
- The filter controls are a labelled select (`Show only`) and two labelled
   `<input type="search">` fields (`From user`, `Free text search`); `run_id`,
   `max_count`, `sort`, and `order` ride along as hidden inputs.
- `user` matches case-insensitive username substrings, not only exact names.
- When multiple usernames match a fragment, prefix matches are ranked before
   inner-substring matches, and ties stay recent-first within each username.
- Typing pauses trigger the existing debounced htmx form request, so results
   refresh from `GET /actions?...` without a separate suggestions endpoint,
   popup, or second swap target.
- To keep that debounced path fast on large historical logs, `/actions`
   resolves substring matches from a short-lived cached distinct username list
   built from the actions collection, refreshes it once on a no-match lookup,
   then fetches the matching rows by exact username query while keeping the
   active `action`, `text`, `run_id`, and time-cursor filters applied on each
   exact-username fetch.
   This differs from `/contributors` and
   `/user_management`, which can stay on userdb-backed sources because they
   only need current user records.
- The summary line reports both the visible row count on the current page and
   the total matching row count, so pagination does not imply every match is
   currently rendered.
- The time link remains a normal anchor because it is a shareable deep link
   into the log timeline, not just a local fragment action.
- The table carries a visually hidden caption naming the result set, and the
   active sort column carries `aria-sort`.
- Full-page `/actions` responses emit server-owned Open Graph metadata. Unlike
   `/tests/view/{id}`, the preview keeps the current query string in `og:url`
   because the filters, time cursor, and explicit `max_count` define the shared
   log slice; the title and description summarize the first visible action row.
   htmx fragment responses carry no Open Graph metadata.

## Finished Tests (`/tests/finished`) query parameters

The finished tests page supports URL-driven server-authoritative filtering,
pagination, and htmx fragment refresh on the canonical `/tests/finished` route.

| Parameter | Values | Default |
|-----|-----|-----|
| `mode` | `search` to open the search view | absent (navigation mode) |
| `success_only` | `1` to show green results only | absent |
| `yellow_only` | `1` to show yellow results only | absent |
| `ltc_only` | `1` to show LTC results only | absent |
| `sort` | `time` | `time` |
| `order` | `desc` | `desc` |
| `user` | case-insensitive username substring | empty |
| `text` | MongoDB text-search query for run info | empty |
| `max_count` | positive integer, capped by auth state | route policy |
| `page` | integer `>= 1` | `1` |

Behavior notes:

- The page has two modes. Navigation mode shows the tab strip (All, Green,
   Yellow, LTC) and is uncapped. Search mode (`mode=search`, linked from the
   sidebar `Search` entry) shows the username and run-info filters and applies
   the search caps. The two are mutually exclusive: submitting a filter from
   navigation mode redirects into `mode=search`, and result-tab flags submitted
   in search mode are redirected away.
- `info_regex` is accepted as a legacy alias for `text`.
- htmx requests target `#tests-finished-content` and keep URL state via
   `hx-push-url="true"`.
- Finished tests currently use a fixed recent-first order, but still carry the
   explicit `sort=time` and `order=desc` query parameters so the URL contract
   stays aligned with the actions page.
- htmx tab clicks refresh the results target directly and refresh the tab strip
  out of band so the active-tab styling stays aligned with the pushed URL.
- Pagination uses the same page size as `/actions` (`ACTIONS_PAGE_SIZE`). A
   `page` value beyond the last page redirects (302) to the last page.
- The username input auto-submits on debounced input and native search clear
   events.
- The run-info text-search input auto-submits on debounced input and native
   search clear events.
- In search mode, the username and free-text fields rely on the shared
  Bootstrap grid sizing used by the other standalone filter pages. The page
  does not apply a dedicated width override to the free-text control.
- `user` resolves case-insensitive username substrings from a short-lived
   cached username list on the users collection, then queries the matching
   usernames through the exact-username finished-run path.
- When multiple usernames match a fragment, prefix matches are ranked before
   inner-substring matches, and ties stay recent-first within each username.
- Finished rows without a usable `last_updated` value sort after timestamped
  rows in the merged recent-first result, and equal fallback rows stay stable
  by run id.
- `text` performs a case-insensitive MongoDB `$text` query against the last-column
   run info text on finished runs only.
- Anonymous search requests use `FINISHED_FILTER_MAX_COUNT_ANON`.
   Authenticated search requests use `FINISHED_FILTER_MAX_COUNT_AUTH`.
   Explicit `max_count` values are preserved in the URL and hidden form state.
- Navigation mode is uncapped: `max_count` is not used, and a stale value in
   the URL is stripped with a 302 redirect. `/tests/user/{username}` is always
   in navigation mode, so it strips `max_count` the same way.
- `/actions` and `/tests/finished` share the same `max_count` query parameter
   for result caps.
- The summary line reports both the visible row count on the current page and
   the total matching finished-run count.  Deleted runs are excluded from both
   the displayed rows and the total count.
- Oversized `max_count` values are clamped to MongoDB's signed 64-bit integer
   range before they reach pymongo.

## Tasks table (`/tests/tasks/{id}`) query parameters

The tasks table on `/tests/view/{id}` is served by the fragment-only
`/tests/tasks/{id}` endpoint. The full detail page reads the same parameters
when it renders the initial table.

| Parameter | Values | Default |
|-----|-----|-----|
| `sort` | `idx`, `worker`, `info`, `last_updated`, `played`, `wins`, `losses`, `draws`, `pentanomial`, `crashes`, `time`, `residual` | cookie value, else `idx` |
| `order` | `asc`, `desc` | cookie value, else column default |
| `page` | integer `>= 1` | `1` |
| `q` | combined worker-name and info substring filter | cookie value, else empty |
| `view` | `paged`, `all` | cookie value, else `paged` |
| `show_task` | task index to highlight | `-1` (none) |

Behavior notes:

- Every parameter except `page` and `show_task` falls back to its
   `tasks_*` cookie when absent, and each response rewrites those cookies with
   the effective values.
- Sorting is server-authoritative with a task-id tie-break; there is no
   client-side header sorter. The active column carries `aria-sort` and the
   table has a visually hidden `Tasks` caption.
- `q` is one control that matches case-insensitively against the worker label
   and against the task's worker details (info label, system, memory, compiler
   and version, Python version, worker version, and architecture).
- Pagination uses `TASKS_PAGE_SIZE`; `view=all` caps the list at
   `TASKS_MAX_ALL` and hides pagination.
- With no explicit `page`, a valid `show_task` selects the page containing that
   task. A `show_task` value that is not in the filtered result set is ignored.
- The Wins/Losses/Draws columns are replaced by a single Pentanomial column for
   pentanomial runs, and the Residual column appears only when residuals are
   available; `sort` values for hidden columns still parse but have no header.
- The response swaps the table into `#tasks-content` and refreshes
   `#tasks-view-controls`, `#tasks-pagination`, and the hidden `sort`, `order`,
   `view`, and `page` inputs out of band.
- Unfinished runs poll this endpoint while the tab is visible and the Tasks
   panel is expanded.

## Related references

- [1-architecture.md](1-architecture.md) -- middleware stack, request flow, and
  the htmx integration overview.
- [3-api-reference.md](3-api-reference.md) -- worker and JSON API endpoints.
- [5-templates.md](5-templates.md) -- Jinja2 environment, template catalog, and
  per-template context contracts.
- [7-development.md](7-development.md) -- local server startup and validation
  workflows.
