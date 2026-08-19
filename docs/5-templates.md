# Jinja2 Templates

This page is the reference for the server-rendered UI layer: the Jinja2
environment, the complete template catalog under
`server/fishtest/templates/`, and the context contract of every template.
Route-to-handler mapping lives in [4-ui-reference.md](4-ui-reference.md).

The environment runs with `StrictUndefined`, so a context key that a template
reads and the handler does not supply is a hard render error. Each contract
below marks which keys are required and which are optional because the
template supplies a default.

## Environment configuration

| Setting | Value |
|---------|-------|
| Templates directory | `server/fishtest/templates/`, overridable with `FISHTEST_JINJA_TEMPLATES_DIR` |
| File extension | `.html.j2` |
| Loader | `FileSystemLoader` (flat directory, no subdirectories) |
| Autoescape | Enabled for `html`, `xml`, and `j2` files |
| Undefined behavior | `StrictUndefined` (reading a missing variable raises) |
| Extensions | `jinja2.ext.do` |
| Instance | `default_environment()` in `http/jinja.py`; wrapped by `default_templates()` and cached per process by `_jinja_templates()` in `http/template_renderer.py` |

Filters and globals are registered in `default_environment()`. Most callables
come from `http/template_helpers.py`, which re-exports the formatting helpers
defined in `util.py`.

### Custom filters

| Filter | Source | Description |
|--------|--------|-------------|
| `urlencode` | `http/template_helpers.py` | URL-encodes a value (`quote_plus`) |
| `split` | `http/jinja.py` | Splits a string (`str.split`), optional separator and maxsplit |
| `string` | `http/jinja.py` | Converts to string (`str`) |

Jinja2 builtin filters such as `tojson`, `default`, `join`, `length`, `lower`,
and `trim` are also in use and are not overridden.

### Global functions

| Global | Defined in | Description |
|--------|-----------|-------------|
| `static_url(spec)` | `http/jinja.py` | Static asset URL with a content-hash cache-buster query parameter |
| `diff_url(run, master_check)` | `util.py` | GitHub diff URL builder |
| `display_residual(task)` | `util.py` | Chi-squared residual display |
| `format_bounds(...)` | `util.py` | SPRT bounds formatter |
| `format_date(dt)` | `util.py` | Date formatter |
| `format_group(groups)` | `util.py` | User group label formatter |
| `format_results(run)` | `util.py` | Game results formatter (returns the `results_info` dict) |
| `format_time_ago(dt)` | `util.py` | Relative time formatter |
| `get_tc_ratio(tc, threads)` | `util.py` | Time-control ratio used for the STC/LTC split |
| `is_active_sprt_ltc(run)` | `util.py` | Whether the run is an active SPRT LTC test |
| `tests_repo(run)` | `util.py` | Canonical tests repo URL for a run |
| `worker_name(info, short=False)` | `util.py` | Worker name from a worker-info dict |
| `is_elo_pentanomial_run(run)` | `http/template_helpers.py` | Whether pentanomial Elo display applies |
| `nelo_pentanomial_summary(run)` | `http/template_helpers.py` | Pentanomial Elo summary line (`Markup`) |
| `results_pre_attrs(results_info, run)` | `http/template_helpers.py` | Attributes for the results `<pre>` element (`Markup`) |
| `run_tables_prefix(username)` | `http/template_helpers.py` | Toggle-id prefix for per-user run tables |
| `tests_run_setup(...)` | `http/template_helpers.py` | Default values for the new-test form |
| `list_to_string(values, decimals)` | `http/template_helpers.py` | Formats a list of floats |
| `pdf_to_string(pdf, decimals)` | `http/template_helpers.py` | Formats a probability density function |
| `t_conf(avg, var, skewness, exkurt)` | `http/template_helpers.py` | Confidence-interval helper |
| `fishtest` | `fishtest` package | Package module (version, metadata) |
| `gh` | `github_api.py` | GitHub API module |
| `math`, `datetime`, `copy`, `urllib`, `float` | Python stdlib | `urllib` is bound to `urllib.parse` |

### Global mappings

Four dictionaries expose shared settings from `http/settings.py` so templates
never hardcode timings or limits.

| Global | Keys | Backing settings |
|--------|------|------------------|
| `poll` | `tasks_detail`, `machines_homepage`, `live_elo`, `tests_run_tables`, `tests_stats`, `tests_view_detail`, `pending_users_nav`, `rate_limits_github`, `rate_limits_server` | `POLL_*_S` |
| `htmx` | `input_changed_delay_ms` | `HTMX_INPUT_CHANGED_DELAY_MS` |
| `finished` | `filter_max_count_anon`, `filter_max_count_auth` | `FINISHED_FILTER_MAX_COUNT_*` |
| `cookies` | `ui_state_max_age`, `contributors_findme_max_age` | `UI_STATE_COOKIE_MAX_AGE_SECONDS` |

Values are seconds for `poll`, milliseconds for `htmx.input_changed_delay_ms`,
and seconds for `cookies`. Read the current numbers from
`server/fishtest/http/settings.py`; do not copy them into templates or docs.

## Rendering flow

1. The view handler returns a context `dict`. The template name comes from the
   route config `renderer` key in `_VIEW_ROUTES` (`views.py`).
2. `_dispatch_view()` runs the handler in the threadpool, then calls
   `render_template_to_response()` (`http/template_renderer.py`).
3. `build_template_context()` (`http/boundary.py`) builds the shared base
   context and merges the handler context over it, so handler keys win on a
   name collision.
4. `Jinja2Templates.TemplateResponse()` renders the template.
5. Rendering is synchronous and runs in the threadpool.
6. A handler may bypass the `renderer` config and return a `Response` directly.
   `_render_hx_fragment()` and `_render_hx_or_context()` (`views.py`) use this
   to return a fragment template when `HX-Request` is present and fall back to
   the page context otherwise.
7. A handler that sets `request.response_status` to `204` or `286` short-circuits
   rendering or marks a polling response as final.

`render_template_to_response()` also attaches `template_name` and
`context_data` to the response for tests.

Two UI error paths render outside `_dispatch_view`, in `http/ui_errors.py`:
`render_notfound_response()` renders `notfound.html.j2` with status 404, and
`render_forbidden_response()` renders `login.html.j2` with status 403. Both
pass the shared base context only.

## Shared base context

Every template receives these keys from `build_template_context()`:

| Key | Type | Description |
|-----|------|-------------|
| `request` | `Request` | Starlette request object |
| `csrf_token` | string | CSRF token for forms and the `csrf-token` meta tag |
| `current_user` | `{"username": str}` or `None` | Authenticated user |
| `open_graph` | dict | Open Graph fields rendered by `base.html.j2` |
| `theme` | string | `"dark"`, `"light"`, or empty (from the `theme` cookie) |
| `theme_color` | string or `None` | Optional `theme-color` meta value |
| `pending_users_count` | int | Pending-user count used by the sidebar `Users` link |
| `static_url` | callable | `static_url(spec)` function |
| `flash` | dict | `{"error": [...], "warning": [...], "info": [...]}` |
| `urls` | dict | Navigation URLs (see below) |

## Page metadata contract

`base.html.j2` renders the page head from shared context.

Full-page handlers may override these keys:

- `open_graph`: dictionary with `site_name`, `type`, `title`, `description`,
  and `url`
- `theme_color`: CSS color string or `None`

For `/tests/view/{id}`, `open_graph['description']` is plain multi-line text so
Discord link previews preserve the run summary layout, including pentanomial
lines when present, without shipping literal backticks.

For `/actions`, the full page overrides `open_graph` with the first visible
action row. Its `og:url` keeps the current query string because filters,
timeline cursors, and explicit `max_count` values define the shared events-log
slice.

Fragment templates never own page-head metadata. The `/tests/view/{id}` page
renders the head tags; the `/tests/view/{id}/detail` fragment updates body
content only. `/actions` follows the same rule: the full page owns the Open
Graph tags, the fragment updates `#actions-content`.

### Navigation URLs (`urls` dict)

```python
{
    "home": "/",
    "login": "/login",
    "logout": "/logout",
    "signup": "/signup",
    "user_profile": "/user",
    "tests": "/tests",
    "tests_finished": "/tests/finished",
    "tests_run": "/tests/run",
    "tests_user_prefix": "/tests/user/",
    "tests_machines": "/tests/machines",
    "nn_upload": "/upload",
    "nns": "/nns",
    "contributors": "/contributors",
    "contributors_monthly": "/contributors/monthly",
    "actions": "/actions",
    "user_management": "/user_management",
    "workers_blocked": "/workers/show",
    "sprt_calc": "/sprt_calc",
    "rate_limits": "/rate_limits",
    "api_rate_limit": "/api/rate_limit",
}
```

### Shared sidebar status links

- `base.html.j2` includes `pending_users_nav_fragment.html.j2` inside a stable
  `<span id="pending-users-nav">` poll wrapper. The wrapper owns the `load`,
  periodic, and `visibilitychange` triggers at the `poll.pending_users_nav`
  cadence; the fragment owns only the anchor markup and the current count.
- `base.html.j2` renders the sidebar `GitHub Rate Limits` anchor
  (`#rate-limits-nav-link`) directly and stamps `data-poll-seconds` from
  `poll.rate_limits_github`. `static/js/application.js` updates that link,
  not a server fragment endpoint, because the browser-side GitHub token lives
  in local storage and is not part of server session state.
- `rate_limits.html.j2` exposes the same browser-side cadence through
  `data-poll-seconds` on `#client_rate_limit`, so the page row and the sidebar
  link read the same client limit state.
- `application.js` owns the browser-side lifecycle of the client poll: it
  initializes on every page that renders `#rate-limits-nav-link`, pauses while
  the document is hidden, refreshes on `visibilitychange` and window `focus`,
  and refreshes again on persisted `pageshow` so bfcache restores do not leave
  stale sidebar or `/rate_limits` client-row state. It also restores the last
  known warning state from local storage before first paint so navigation does
  not flash the link back to its normal color.
- `/rate_limits/server` returns a small inline HTML response built in
  `rate_limits_server()` (`views.py`): the remaining server budget plus an
  out-of-band `#server_reset` update. It has no Jinja template.

## Client-side behavior pattern

Behavior-heavy page scripts use static assets plus `data-*` configuration
rather than large inline `<script>` blocks.

| Template | Script |
|----------|--------|
| `base.html.j2` | `static/js/application.js`, `static/js/notifications.js` |
| `contributors.html.j2` | `static/js/contributors.js` |
| `tests.html.j2` | `static/js/tests_homepage.js`, `static/js/active_run_filters.js` |
| `tests_live_elo.html.j2` | `static/js/live_elo.js` |
| `sprt_calc.html.j2` | `static/js/sprt.js`, `static/js/calc.js` |
| `login.html.j2`, `signup.html.j2` | `static/js/toggle_password.js` |
| `tests_run.html.j2` | `static/js/spsa_new.js` |
| `tests_view.html.j2` | `static/js/spsa.js` |
| `user.html.j2` | `static/js/user_profile.js`, `static/js/toggle_password.js` |

`tests_homepage.js` owns the homepage Workers panel cookie state and triggers
an immediate `machines:load` refresh whenever Bootstrap reports that the panel
has been opened.

Shared behavior that spans multiple pages belongs in shared assets instead of
page-local inline scripts. Search and filter inputs stay plain
`<input type="search">` controls.

## Sortable table accessibility baseline

Seven templates define an inline `sort_header` Jinja2 macro for a sortable
data table. All of them satisfy the same contract:

1. **`scope="col"`** on every `<th>` in `<thead>`, including non-sortable
   headers such as the `#` row-number column.
2. **`<caption class="visually-hidden">`** as the first child of each
   `<table>`, so screen readers announce the table before its cells.
3. **`class="sticky-top"`** on `<thead>` so headers stay visible while
   scrolling long tables.
4. **`aria-sort`** only on the active sort column header, valued `ascending`
   or `descending`.
5. **`aria-hidden="true"`** on the decorative sort-indicator span.

| Template | Table id | Caption text |
|----------|----------|-------------|
| `actions_content_fragment.html.j2` | `actions_table` | Events log results |
| `contributors_content_fragment.html.j2` | `contributors_table` | Contributors |
| `machines_fragment.html.j2` | `machines_table` | Machines |
| `nns_content_fragment.html.j2` | `nns_table` | Neural networks |
| `tasks_content_fragment.html.j2` | `tasks_table` | Tasks |
| `user_management_content_fragment.html.j2` | `user_management_table` | User management |
| `workers_content_fragment.html.j2` | `workers_table` | Workers |

The events-log caption appends ` for run <run_id>` when `run_id_filter` is set.

`server/tests/test_http_boundary.py` asserts `scope="col"`, the
`visually-hidden` caption, and `sticky-top` on `<thead>` for all seven
templates.

### Filter and cookie consistency

The seven sortable tables split into two patterns that determine their filter
form and state persistence:

| Pattern | Pages | Filter control size | Cookie persistence | Polling |
|---------|-------|---------------------|--------------------|---------|
| A (standalone page) | contributors, actions, workers, user_management, nns | `form-control` | 0-1 client-side cookies | none |
| B (embedded panel) | machines, tasks | `form-control-sm` | server-side UI-state cookies | `poll.machines_homepage` / `poll.tasks_detail` |

Design rules:

- **No custom width overrides** on filter inputs. Let `col-md-auto` size fields
  naturally across all standalone pages. This also applies to
  `/tests/finished?mode=search`: its username and free-text controls size
  through the shared grid layout, not a page-specific CSS minimum width.
- **`autocomplete="off"`** on all search and filter text inputs so browser
  autofill cannot interfere with htmx-driven filtering.
- **Cookie max-age** must come from `UI_STATE_COOKIE_MAX_AGE_SECONDS` in
  `http/settings.py`, never from a literal. Client-side cookies flow through
  the shared `writeUiCookie()` helper in `application.js` or through `data-*`
  attributes consumed by that helper. Templates expose the value as
  `cookies.ui_state_max_age` or a page-specific `cookie_max_age` context key;
  do not introduce further aliases.

Known limitations:

- Workers and user management have no cookie persistence for filter or sort
  state, so both reset to defaults on reload.
- Contributors has one client-side cookie (the monthly/all-time toggle) and no
  filter-state cookies.

## Template catalog

`server/fishtest/templates/` contains **53** files:

- **1** base layout
- **19** full-page templates that extend `base.html.j2`
- **33** fragments and shared partials that do not extend anything

### Base layout

| Template | Purpose |
|----------|---------|
| `base.html.j2` | Full document shell: head metadata, theme and asset loading, Bootstrap and htmx from CDN, sidebar navigation, flash messages, pending-users poll wrapper |

`base.html.j2` defines three blocks:

| Block | Default | Used by |
|-------|---------|---------|
| `title` | `Stockfish Testing Framework` | every full-page template |
| `head` | empty | `tests.html.j2`, `signup.html.j2` |
| `body` | empty | every full-page template |

There is no `content` block.

### Full-page templates

Each extends `base.html.j2` and is named by a route `renderer` in
`_VIEW_ROUTES` (`server/fishtest/views.py`).

| Template | Route | Handler |
|----------|-------|---------|
| `actions.html.j2` | `/actions` | `actions` (delegates to `actions` in `views_actions.py`) |
| `contributors.html.j2` | `/contributors`, `/contributors/monthly` | `contributors`, `contributors_monthly` |
| `login.html.j2` | `/login`; also UI 403 | `login`; `render_forbidden_response` in `http/ui_errors.py` |
| `nn_upload.html.j2` | `/upload` | `upload` |
| `nns.html.j2` | `/nns` | `nns` |
| `notfound.html.j2` | UI 404 | `render_notfound_response` in `http/ui_errors.py` |
| `rate_limits.html.j2` | `/rate_limits` | `rate_limits` |
| `signup.html.j2` | `/signup` | `signup` |
| `sprt_calc.html.j2` | `/sprt_calc` | `sprt_calc` |
| `tests.html.j2` | `/tests` | `tests` |
| `tests_finished.html.j2` | `/tests/finished` | `tests_finished` (context from `get_paginated_finished_runs` in `views_finished.py`) |
| `tests_live_elo.html.j2` | `/tests/live_elo/{id}` | `tests_live_elo` |
| `tests_run.html.j2` | `/tests/run` | `tests_run` |
| `tests_stats.html.j2` | `/tests/stats/{id}` | `tests_stats` |
| `tests_user.html.j2` | `/tests/user/{username}` | `tests_user` |
| `tests_view.html.j2` | `/tests/view/{id}` | `tests_view` |
| `user.html.j2` | `/user`, `/user/{username}` | `user` |
| `user_management.html.j2` | `/user_management` | `user_management` |
| `workers.html.j2` | `/workers/{worker_name}` | `workers` |

### Fragments and shared partials

None of these extend `base.html.j2` or declare blocks. The **Kind** column
distinguishes three roles:

- **response fragment**: returned as the top-level body of an htmx response.
- **shared partial**: only ever `{% include %}`-d by another template.
- **both**: included server-side for first paint and returned as a fragment.

| Template | Kind | Returned by / included by | Swap target |
|----------|------|---------------------------|-------------|
| `actions_content_fragment.html.j2` | both | `actions` / `actions.html.j2` | `#actions-content` |
| `active_run_filters_fragment.html.j2` | shared partial | `run_table.html.j2` through the `filters_template` variable | -- |
| `contributors_content_fragment.html.j2` | both | `_contributors_common` / `contributors.html.j2` | `#contributors-content` |
| `contributors_rows_fragment.html.j2` | shared partial | `contributors_content_fragment.html.j2` | -- |
| `elo_results.html.j2` | shared partial | `tests_view.html.j2`, `run_table_row_fragment.html.j2`, `elo_results_fragment.html.j2` | -- |
| `elo_results_fragment.html.j2` | shared partial (OOB payload) | `tests_view_detail_fragment.html.j2` | `#elo-<run_id>`, `#run-status-<run_id>`, `#tasks-totals` |
| `homepage_stats_fragment.html.j2` | shared partial (OOB payload) | `tests.html.j2`, `tests_run_tables_fragment.html.j2` | `#homepage-stats` |
| `live_elo_fragment.html.j2` | response fragment | `live_elo_update` (`/tests/live_elo_update/{id}`) | `#live-elo-data` (OOB) |
| `machines_fragment.html.j2` | response fragment | `tests_machines` (`/tests/machines`) | `#machines`, plus OOB |
| `nns_content_fragment.html.j2` | both | `nns` / `nns.html.j2` | `#nns-content` |
| `pagination.html.j2` | shared partial | every paginated page and fragment | -- |
| `pending_users_nav_fragment.html.j2` | both | `user_management_pending_count` / `base.html.j2` | `#pending-users-nav` |
| `run_table.html.j2` | shared partial | `run_tables.html.j2`, `tests_finished_results_fragment.html.j2` | -- |
| `run_table_row_fragment.html.j2` | shared partial | `run_table.html.j2`, `tests_run_tables_fragment.html.j2` | -- |
| `run_tables.html.j2` | shared partial | `tests.html.j2`, `tests_user_content_fragment.html.j2` | -- |
| `tasks_content_fragment.html.j2` | response fragment | `tests_tasks` (`/tests/tasks/{id}`) | `#tasks-content`, plus OOB |
| `tasks_controls_fragment.html.j2` | shared partial | `tests_view.html.j2`, `tasks_content_fragment.html.j2` | -- |
| `tasks_rows_fragment.html.j2` | shared partial | `tasks_content_fragment.html.j2` | -- |
| `tests_filter_tabs_fragment.html.j2` | shared partial | `tests_finished_content_fragment.html.j2`, `tests_finished_results_fragment.html.j2`, `tests_user_content_fragment.html.j2` | -- |
| `tests_finished_content_fragment.html.j2` | shared partial | `tests_finished.html.j2` | -- |
| `tests_finished_results_fragment.html.j2` | both | `tests_finished` / `tests_finished_content_fragment.html.j2` | `#tests-finished-content`, plus OOB |
| `tests_run_tables_fragment.html.j2` | response fragment | `_render_tests_run_tables_live_fragment` for `/tests?live=run_tables` and `/tests/user/{username}?live=run_tables` | OOB only (`hx-swap="none"`) |
| `tests_stats_content_fragment.html.j2` | both | `tests_stats` / `tests_stats.html.j2` | `#tests-stats-content` |
| `tests_user_content_fragment.html.j2` | both | `tests_user` / `tests_user.html.j2` | `#tests-user-content` |
| `tests_view_detail_fragment.html.j2` | response fragment | `tests_view_detail` (`/tests/view/{id}/detail`) | OOB only (`hx-swap="none"`) |
| `tests_view_details_section.html.j2` | shared partial | `tests_view.html.j2`, `tests_view_detail_fragment.html.j2` | `#tests-view-details` |
| `tests_view_spsa_section.html.j2` | shared partial | `tests_view.html.j2` | `#tests-view-spsa` |
| `tests_view_stats_section.html.j2` | shared partial | `tests_view.html.j2`, `tests_view_detail_fragment.html.j2` | `#tests-view-stats` |
| `tests_view_time_section.html.j2` | shared partial | `tests_view.html.j2`, `tests_view_detail_fragment.html.j2` | `#tests-view-time` |
| `user_management_content_fragment.html.j2` | both | `user_management` / `user_management.html.j2` | `#user-management-content` |
| `user_management_rows_fragment.html.j2` | shared partial | `user_management_content_fragment.html.j2` | -- |
| `workers_content_fragment.html.j2` | both | `workers` / `workers.html.j2` | `#workers-content` |
| `workers_rows_fragment.html.j2` | shared partial | `workers_content_fragment.html.j2` | -- |

### Include tree

Shared markup lives in the partials below. Use this tree to find where an
element is defined without grepping.

```
base.html.j2
  pending_users_nav_fragment.html.j2

tests.html.j2
  homepage_stats_fragment.html.j2
  run_tables.html.j2
    run_table.html.j2            (x5: pending, paused, failed, active, finished)
      active_run_filters_fragment.html.j2   (active panel, homepage only)
      pagination.html.j2         (x2: above and below the table)
      run_table_row_fragment.html.j2
        elo_results.html.j2

tests_user.html.j2
  tests_user_content_fragment.html.j2
    tests_filter_tabs_fragment.html.j2
    run_tables.html.j2 ...

tests_finished.html.j2
  tests_finished_content_fragment.html.j2
    tests_filter_tabs_fragment.html.j2
    tests_finished_results_fragment.html.j2
      tests_filter_tabs_fragment.html.j2   (OOB tab strip, navigation mode)
      run_table.html.j2 ...

tests_view.html.j2
  elo_results.html.j2
  tests_view_details_section.html.j2
  tests_view_stats_section.html.j2
  tests_view_time_section.html.j2
  tests_view_spsa_section.html.j2
  tasks_controls_fragment.html.j2
  pagination.html.j2

tests_view_detail_fragment.html.j2
  elo_results_fragment.html.j2
    elo_results.html.j2
  tests_view_details_section.html.j2
  tests_view_stats_section.html.j2         (non-SPSA runs only)
  tests_view_time_section.html.j2

tests_run_tables_fragment.html.j2
  run_table_row_fragment.html.j2
  homepage_stats_fragment.html.j2          (homepage only)

tests_stats.html.j2         -> tests_stats_content_fragment.html.j2
actions.html.j2             -> actions_content_fragment.html.j2 -> pagination.html.j2
contributors.html.j2        -> contributors_content_fragment.html.j2
                                 -> contributors_rows_fragment.html.j2, pagination.html.j2
nns.html.j2                 -> nns_content_fragment.html.j2 -> pagination.html.j2
user_management.html.j2     -> user_management_content_fragment.html.j2
                                 -> user_management_rows_fragment.html.j2, pagination.html.j2
workers.html.j2             -> workers_content_fragment.html.j2
                                 -> workers_rows_fragment.html.j2, pagination.html.j2
tasks_content_fragment.html.j2 -> tasks_controls_fragment.html.j2,
                                  tasks_rows_fragment.html.j2, pagination.html.j2
machines_fragment.html.j2   -> pagination.html.j2
```

`run_table.html.j2` uses a dynamic include: `{% include filters_template %}`.
`run_tables.html.j2` sets `filters_template` to
`active_run_filters_fragment.html.j2` for the Active panel when no username
filter is active, and to `none` otherwise.

### Out-of-band swap inventory

| Template | OOB element ids | Swap mode |
|----------|-----------------|-----------|
| `contributors_content_fragment.html.j2` | `contributors_view`, `contributors_sort`, `contributors_order` | `true` |
| `elo_results_fragment.html.j2` | `elo-<run_id>`, `run-status-<run_id>`, `tasks-totals` | `innerHTML` |
| `homepage_stats_fragment.html.j2` | `homepage-stats` | `innerHTML` |
| `live_elo_fragment.html.j2` | `live-elo-data` | `innerHTML` |
| `machines_fragment.html.j2` | `machines-pagination`, `machines_sort`, `machines_order`, `machines_page`, `workers-count` | `innerHTML` / `true` |
| `tasks_content_fragment.html.j2` | `tasks_sort`, `tasks_order`, `tasks_view`, `tasks_page`, `tasks-view-controls`, `tasks-pagination` | `true` / `innerHTML` |
| `tests_finished_results_fragment.html.j2` | `tests-finished-filter-tabs` | `outerHTML` |
| `tests_run_tables_fragment.html.j2` | one `<tbody>` per panel, one `<span>` per count update, `workers-count` | `innerHTML` |
| `tests_view_detail_fragment.html.j2` | `tests-view-detail-expected`, `spsa-data-<run_id>` | `true` / `innerHTML` |
| `tests_view_details_section.html.j2` | `tests-view-details` | `outerHTML` when `oob` is true |
| `tests_view_spsa_section.html.j2` | `tests-view-spsa` | `outerHTML` when `oob` is true |
| `tests_view_stats_section.html.j2` | `tests-view-stats` | `outerHTML` when `oob` is true |
| `tests_view_time_section.html.j2` | `tests-view-time` | `outerHTML` when `oob` is true |
| `user_management_content_fragment.html.j2` | `user_management_sort`, `user_management_order`, `user_management_view` | `true` |
| `workers_content_fragment.html.j2` | `workers_sort`, `workers_order`, `workers_view` | `true` |

## Page template contracts

Templates do not fetch data. Handlers pass fully shaped context. Keys marked
optional carry an in-template default and are safe to omit.

### `base.html.j2`

Shared base context only. `urls`, `static_url`, `pending_users_count`,
`csrf_token`, and `flash` carry in-template defaults; `open_graph`, `theme`,
and `theme_color` do not.

### `actions.html.j2`

Page shell for `/actions`: the filter form plus the `#actions-content` wrapper
that includes `actions_content_fragment.html.j2`.

| Key | Type | Required |
|-----|------|----------|
| `filters` | dict `{action, username, text, run_id}` | yes |
| `run_id_filter` | string | yes |
| `max_count` | int or `None` | yes |
| `sort` | string | optional (default `time`) |
| `order` | string | optional (default `desc`) |

`action_param`, `username_param`, and `text_param` are optional overrides for
the three form fields; the handler does not set them.

The included fragment needs the remaining keys listed under
[`actions_content_fragment.html.j2`](#actions_content_fragmenthtmlj2).

### `contributors.html.j2`

Page shell for `/contributors` and `/contributors/monthly`: summary cards,
search form, and the `#contributors-content` wrapper.

| Key | Type | Required |
|-----|------|----------|
| `is_monthly` | bool | yes |
| `monthly_suffix` | string (`" - Top Month"` or empty) | yes |
| `summary` | dict `{testers, developers, active_testers, cpu_hours, games, tests}` | yes |
| `search` | string | yes |
| `highlight` | string | yes |
| `sort`, `order`, `view` | string | optional |
| `num_users`, `max_all` | int | optional |
| `is_truncated` | bool | optional |

`users` and `pages` reach the table through the content fragment.

### `login.html.j2`

| Key | Type | Required |
|-----|------|----------|
| `remember_me_checked` | bool | optional (default `true`) |
| `remember_me_cookie_name` | string | optional (default `login_remember_me`) |

Both keys default because the same template renders the UI 403 page, where
only the shared base context is available.

Behavior notes:

- The form submits `stay_logged_in=0` through a hidden input and
  `stay_logged_in=1` through the checkbox, so the server can tell an explicit
  opt-out from the checked or default case.
- With no `login_remember_me` UI cookie, the checkbox renders checked. An
  explicit opt-out stored in that cookie renders it unchecked on later visits.
- `application.js` mirrors checkbox changes into the same UI-state cookie via
  `writeUiCookie()`, and `POST /login` refreshes it server-side.

### `nn_upload.html.j2`

| Key | Type | Required |
|-----|------|----------|
| `testing_guidelines_url` | string | yes |
| `cc0_url` | string | yes |
| `nn_stats_url` | string | yes |

The form posts to `{{ request.url }}`; the handler's `upload_url` key is not
read by the template.

### `nns.html.j2`

Page shell for `/nns`. It owns only the `<h2>` heading and the `#nns-content`
wrapper; the summary cards, filter form, view toggle, pagination, and table all
live in `nns_content_fragment.html.j2`.

| Key | Type | Required |
|-----|------|----------|
| `filters` | dict `{network_name, user, master_only}` | optional (default `{}`) |
| `sort`, `order`, `view` | string | optional |
| `num_nns`, `max_all` | int | optional |
| `is_truncated` | bool | optional |

### `notfound.html.j2`

Shared base context only.

### `rate_limits.html.j2`

| Key | Type | Required |
|-----|------|----------|
| `server_rate_limit` | int (`-1` when GitHub is unreachable) | yes |
| `server_reset` | string `HH:MM:SS` | yes |

The server row polls `/rate_limits/server` at `poll.rate_limits_server` and
swaps `#server_rate_limit`; that endpoint also OOB-updates `#server_reset`.
The client row (`#client_rate_limit`) is browser-driven and carries
`data-poll-seconds` from `poll.rate_limits_github`.

### `signup.html.j2`

| Key | Type | Required |
|-----|------|----------|
| `recaptcha_site_key` | string | yes |
| `VALID_USERNAME_PATTERN` | string (regex for the `pattern` attribute) | yes |

### `sprt_calc.html.j2`

Shared base context only.

### `tests.html.j2`

Homepage shell for `/tests`.

| Key | Type | Required |
|-----|------|----------|
| `page_idx` | int | yes |
| `machines_count` | int | yes on page 1 |
| `machines_shown` | bool | yes on page 1 |
| `cores`, `nps_m`, `games_per_minute`, `pending_hours` | int / string | yes on page 1 (consumed by `homepage_stats_fragment.html.j2`) |
| `run_tables_ctx` | dict | optional; when absent, `run_tables.html.j2` is included with the outer context |
| `workers_count_text` | string | optional (falls back to `Workers - <machines_count>`) |
| `machines_sort`, `machines_order`, `machines_q` | string | optional |
| `machines_page` | int | optional |
| `machines_my_workers`, `has_authenticated_user` | bool | optional |

Behavior notes:

- `run_tables_ctx` carries the homepage Active-panel first-paint filter state,
  including restored checkbox selections, filtered count text, and the
  hide-only style block emitted in the `head` block.
- `run_tables_ctx` is intentionally lightweight. Unfinished-run rows omit
  `tasks`, `bad_tasks`, and `args.spsa.param_history`; task detail stays on the
  detail and tasks routes.
- Notification button state is not part of `run_tables_ctx`; it derives from
  browser-local follow state at page load.

`run_tables_ctx` keys, built by `_build_run_tables_context()` in `views.py`:
`pending_approval_runs`, `paused_runs`, `failed_runs`, `active_runs`,
`active_count_text`, `active_run_filters`, `finished_runs`,
`num_finished_runs`, `finished_runs_pages`, `page_idx`, `prefix`,
`toggle_states`, `finished_title_text`, `live_url`, `show_gauge`.

### `tests_finished.html.j2`

Thin shell: title plus `tests_finished_content_fragment.html.j2`.

| Key | Type | Required |
|-----|------|----------|
| `title` | string (`""`, `" - Greens"`, `" - Yellows"`, `" - LTC"`) | yes |

Everything else the page renders comes from the included content fragment and
results fragment.

### `tests_live_elo.html.j2`

| Key | Type |
|-----|------|
| `run` | dict |
| `page_title` | string |
| `sprt_state` | string |
| `elo_raw`, `ci_lower_raw`, `ci_upper_raw`, `LLR_raw`, `LOS_raw`, `a_raw`, `b_raw` | float |
| `elo_value`, `ci_lower`, `ci_upper`, `LLR`, `LOS`, `a`, `b` | number |
| `games` | int |
| `w_pct`, `l_pct`, `d_pct` | float |
| `pentanomial` | list (first five entries) |
| `elo_model`, `elo0`, `elo1`, `alpha`, `beta` | mixed |

All values come from `_build_live_elo_context()` in `views.py`. That builder
also returns `run_status_label`, `W`, `L`, and `D`, which no template reads.

`static/js/live_elo.js` renders the LOS, LLR, and Elo gauges from the
`#gauge-data` `data-*` attributes. The Elo gauge supports a fixed default range
and a dynamic range; the gauge reports the uncapped server Elo value while the
needle stays visually bounded by the active range.

### `tests_run.html.j2`

| Key | Type |
|-----|------|
| `args` | dict (run arguments; empty for a new test) |
| `is_rerun` | bool |
| `rescheduled_from` | string or `None` |
| `tests_repo_value` | string |
| `new_tag_value`, `new_signature_value` | string |
| `new_options_value`, `base_options_value` | string |
| `info_value` | string |
| `spsa_form` | dict |
| `test_book`, `pt_book` | string |
| `master_info` | dict or `None` |
| `valid_books` | iterable |
| `setup` | dict (from `tests_run_setup()`) |
| `create_num_games_constraints` | dict |
| `supported_arches`, `supported_compilers` | list |

The handler also returns `form_action` and `pt_info`; neither is read by this
template.

Rendered structure notes:

- the preset chooser caption for `Test type` is a neutral heading row, not a
  `label` wrapper;
- the ellipsis control is a sibling button that toggles the extra preset blocks
  through Bootstrap collapse;
- the collapsed preset blocks expose stable ids so the toggle button can name
  them through `aria-controls`.

Tests repository contract:

- `tests_repo_value` is rendered from the current GitHub repo value for the form;
- trailing-slash input is accepted on submit, but persisted run data is
  canonicalized and validated as the slash-free form.

### `tests_stats.html.j2`

Page shell for `/tests/stats/{id}`. For unfinished non-SPSA runs it renders a
visibility-aware poller that targets `#tests-stats-content` with
`hx-swap="outerHTML"` at the `poll.tests_stats` cadence, then includes the
shared content fragment.

| Key | Type |
|-----|------|
| `run` | dict |
| `page_title` | string |
| `stats` | dict (see the fragment contract) |

### `tests_user.html.j2`

Thin shell: heading title plus a `#tests-user-content` wrapper around
`tests_user_content_fragment.html.j2`.

| Key | Type |
|-----|------|
| `username` | string |

The fragment consumes the remaining keys.

### `tests_view.html.j2`

Detail page for `/tests/view/{id}`.

| Key | Type |
|-----|------|
| `run` | dict |
| `page_title` | string |
| `run_args` | list of `(name, value, url)` tuples |
| `run_status_label` | string (`active`, `paused`, `pending`, `finished`, `failed`) |
| `tasks_totals` | string (active worker and core summary) |
| `approver` | bool |
| `chi2` | dict `{chi2, dof, p, residual}` |
| `document_size` | int |
| `spsa_data` | dict or `None` |
| `spsa_percentage_checked` | bool |
| `results_info` | dict from `format_results(run)` |
| `tasks_shown` | bool |
| `show_task` | int (`-1` when nothing is highlighted) |
| `tasks_sort`, `tasks_order`, `tasks_view`, `tasks_q` | string |
| `tasks_page`, `tasks_num_tasks`, `tasks_max_all` | int |
| `tasks_pages` | list of pagination dicts |
| `tasks_is_truncated` | bool |
| `follow` | int |
| `can_modify_run`, `same_user` | bool |
| `modify_num_games_constraints` | dict |
| `notes`, `warnings` | list of strings (`warnings` may contain `Markup`) |
| `use_3dot_diff`, `allow_github_api_calls` | bool |
| `open_graph`, `theme_color` | page-metadata overrides |

`run_id` is derived in-template as `run["_id"] | string`.

Merged live-polling contract:

- Unfinished runs render one visibility-aware htmx poller targeting
  `/tests/view/{id}/detail` with `hx-swap="none"` at the
  `poll.tests_view_detail` cadence.
- The poller includes `#tests-view-detail-expected`, a hidden server-owned
  input carrying the current canonical `expected` status. Its value must match
  `run_status_label`: `active`, `paused`, or `pending`.
- The shared OOB targets are `#elo-<run_id>`, `#run-status-<run_id>`,
  `#tasks-totals`, `#tests-view-details`, `#tests-view-time`, and either
  `#tests-view-stats` or the embedded `#spsa-data-<run_id>` payload depending
  on the run type.
- `tests_view_detail_fragment.html.j2` OOB-refreshes the hidden expected-state
  input alongside the visible status label, so paused and pending transitions
  settle back to `204` on the next poll.
- SPSA runs keep the chart shell stable in `tests_view.html.j2`; live detail
  refreshes update only the embedded `application/json` payload contents so
  `static/js/spsa.js` can keep the payload node mounted, skip unchanged
  redraws, and redraw changed payloads without replacing the mounted chart
  shell. The full page renders the `% c` checkbox from a browser-readable
  cookie on first paint.

Tasks loader contract:

- When `tasks_shown` is true, `#tasks-content` starts an htmx `load` request
  against `/tests/tasks/{id}`; otherwise it waits for the `tasks:load` event.
- Unfinished runs add a periodic and `visibilitychange` trigger at the
  `poll.tasks_detail` cadence, gated on the `#tasks` section being expanded.
- The tasks form (`#tasks-filters`) stays outside the swapped fragment, so the
  fragment refreshes the hidden inputs out of band.

Run-table row contract:

- Run tables use the normal `.table-striped` contract.
- Active-run filtering emits a first-paint style block that hides excluded
  rows, and `active_run_filters.js` keeps the visible rows contiguous in tbody
  order so striping stays correct while filters are active. The style block is
  hide-only and does not encode row parity.
- Active row markup carries `data-test-type`, `data-time-control`, and
  `data-threads` filter dimensions plus `data-active-filter-index`, a
  source-order index used to restore the server order after checkbox changes
  and OOB swaps.

### `user.html.j2`

Serves both the own-profile form and the approver view of another user.

| Key | Type |
|-----|------|
| `profile` | bool (own profile vs approver view) |
| `user` | dict (`username`, `email`, `tests_repo`, `blocked`, `pending`, ...) |
| `limit` | machine limit value |
| `hours` | int (CPU hours) |
| `safe_tests_repo_url` | string (normalized `https://github.com/<user>/<repo>`) |
| `extract_repo_from_link` | string (`<user>/<repo>`) |
| `blocked` | bool |

The handler also returns `form_action` and `registration_time_label`; neither
is read by any template.

Tests repository contract:

- `user["tests_repo"]` is persisted as the canonical slash-free GitHub repo URL;
- profile form submissions may include a trailing slash, but stored user data
  is normalized and validated before save.

### `user_management.html.j2`

Page shell for `/user_management`: group tabs, search form, and the
`#user-management-content` wrapper.

| Key | Type |
|-----|------|
| `all_count`, `pending_count`, `blocked_count`, `idle_count`, `approvers_count` | int |
| `group` | string (`all`, `pending`, `blocked`, `idle`, `approvers`) |
| `sort`, `order`, `q`, `view` | string |

`selected_users`, `pages`, `num_selected_users`, `max_all`, and `is_truncated`
are consumed by the content fragment.

### `workers.html.j2`

Page shell for `/workers/{worker_name}`. `/workers/show` renders the blocked
list only; a real worker name additionally renders the block/unblock form.

| Key | Type | Required |
|-----|------|----------|
| `show_admin` | bool | yes |
| `worker_name` | string | only when `show_admin` is true |
| `last_updated_label` | string | only when `show_admin` is true |
| `message` | string | only when `show_admin` is true |
| `blocked` | bool | only when `show_admin` is true |
| `filter_value` | string (`all-workers`, `le-5days`, `gt-5days`) | yes |
| `sort`, `order`, `q`, `view` | string | yes |

The `worker_name` context key shadows the `worker_name()` global inside this
template. That is intentional and confined to `workers.html.j2`.

`blocked_workers`, `pages`, `show_email`, `num_workers`, `max_all`, and
`is_truncated` are consumed by the content fragment.

## Fragment and partial contracts

Fragments receive the same shared base context as full pages, because both
paths go through `build_template_context()`.

### `actions_content_fragment.html.j2`

Swaps `#actions-content`. Also included by `actions.html.j2` for first paint.

| Key | Type |
|-----|------|
| `actions` | list of action row dicts |
| `visible_actions` | int (rows rendered on this page) |
| `num_actions` | int (total matching count) |
| `page_size` | int |
| `current_page` | int (1-based) |
| `run_id_filter` | string |
| `max_count` | int or `None` |
| `filters` | dict `{action, username, text, run_id}` |
| `pages` | list of pagination dicts |
| `sort` | string, optional (default `time`); one of `time`, `event`, `source`, `target`, `comment` |
| `order` | string, optional (default `desc`) |
| `sort_summary` | string, optional (default empty) |

Each action row, built by `_build_action_row()` in `views_actions.py`, adds
these keys to the raw action document:

| Key | Type |
|-----|------|
| `time_label` | string (preformatted, non-breaking hyphens) |
| `time_url` | string |
| `event` | string |
| `agent_name` | string |
| `agent_url` | string or `None` |
| `target_name` | string |
| `target_url` | string or `None` |
| `message` | string |

Sortable headers are dual-mode links (`href` plus `hx-get`) targeting
`#actions-content` with `hx-push-url="true"`.

### `active_run_filters_fragment.html.j2`

Server-side include only. It is never returned by a route and never swapped by
htmx; `run_table.html.j2` includes it through `filters_template` for the
homepage Active panel.

| Key | Type | Required |
|-----|------|----------|
| `active_run_filters` | dict or `None` | optional (default `none`) |

When `active_run_filters` is `None` the panel renders with every checkbox
checked. Otherwise the dict, built by `_build_active_run_filter_context()` in
`views.py`, carries:

| Key | Type | Description |
|-----|------|-------------|
| `all_enabled` | bool | Every dimension fully selected |
| `count_text` | string | `Active - N tests` or `Active - N (M) tests` |
| `enabled_by_dim` | dict of tuples | Keyed `test-type`, `time-control`, `threads` |
| `hidden_selectors` | list of strings | CSS selectors for excluded rows |
| `style_text` | string | Hide-only style block rendered in the page `head` |
| `ordered_runs` | list of run dicts | Visible rows first, each tagged `_active_filter_index` |

Rendering the checkbox state server-side removes the flash where all rows are
briefly visible before JavaScript reapplies the persisted filter. The panel
reads `cookies.ui_state_max_age` for its `data-filter-cookie-max-age`
attribute.

### `contributors_content_fragment.html.j2`

Swaps `#contributors-content`.

| Key | Type | Required |
|-----|------|----------|
| `users` | list of contributor row dicts | yes (consumed by the rows partial) |
| `highlight` | string | yes (consumed by the rows partial) |
| `pages` | list of pagination dicts | yes |
| `sort`, `order`, `view` | string | optional |
| `num_users`, `max_all` | int | optional |
| `is_truncated` | bool | optional |

Each contributor row, from `build_contributors_rows()` in
`http/template_helpers.py`: `username`, `user_url` (empty for non-approvers),
`rank`, `last_updated_label`, `last_updated_sort`, `games_per_hour`,
`cpu_hours`, `games`, `tests`, `tests_repo_url`, `tests_user_url`. The
handler's `is_approver` flag is consumed in Python by
`build_contributors_rows()` to decide whether `user_url` is populated; no
contributors template reads it.

Sortable headers are dual-mode links (`href` plus `hx-get`) targeting
`#contributors-content` with `hx-push-url="true"`.

The outer search form on `contributors.html.j2` keeps `view`, `sort`, and
`order` in hidden inputs. The fragment refreshes `#contributors_view`,
`#contributors_sort`, and `#contributors_order` out of band so later form
submissions preserve the live table state.

### `contributors_rows_fragment.html.j2`

| Key | Type |
|-----|------|
| `users` | list of contributor row dicts |
| `highlight` | string (username to mark with `id="me"`, empty for none) |

### `elo_results.html.j2`

Shared Elo summary block. Included by `tests_view.html.j2`,
`run_table_row_fragment.html.j2`, and `elo_results_fragment.html.j2`.

| Key | Type | Required |
|-----|------|----------|
| `run` | dict (full run document) | yes |
| `show_gauge` | bool | optional (default `false`) |
| `results_info` | dict | optional (defaults to `format_results(run)`) |

When `show_gauge` is true the partial emits a `#chart_div_<run_id>` gauge
container. For SPRT runs past the pending state it wraps the block in a link to
`/tests/live_elo/<run_id>`.

### `elo_results_fragment.html.j2`

Out-of-band payload included by `tests_view_detail_fragment.html.j2`.

| Key | Type | Required |
|-----|------|----------|
| `run` | dict | yes |
| `run_status_label` | string | optional (recomputed from `run` when absent) |
| `tasks_totals` | string | optional (the `#tasks-totals` update is skipped when absent) |

Targets `#elo-<run_id>`, `#run-status-<run_id>`, and `#tasks-totals`, all with
`hx-swap-oob="innerHTML"`.

### `homepage_stats_fragment.html.j2`

Always an out-of-band `#homepage-stats` update, including on first paint from
`tests.html.j2`.

| Key | Type |
|-----|------|
| `cores` | int |
| `nps_m` | string |
| `games_per_minute` | int |
| `pending_hours` | string |

### `live_elo_fragment.html.j2`

Returned by `live_elo_update` for `/tests/live_elo_update/{id}` and swapped
into `#live-elo-data` out of band, preserving the current client-side gauge
mode. Same context as `tests_live_elo.html.j2` minus `page_title`.

The handler sets status `286` once `sprt_state` is non-empty, which stops the
poller.

### `machines_fragment.html.j2`

Returned by `tests_machines` for `/tests/machines`, swapped into `#machines`.

| Key | Type | Required |
|-----|------|----------|
| `machines` | list of machine row dicts | yes |
| `machines_count` | int | yes |
| `workers_count_text` | string | yes (OOB `#workers-count`) |
| `pages` | list | optional (default `[]`) |
| `sort`, `order`, `q` | string | optional |
| `current_page` | int | optional (default `1`) |
| `my_workers` | bool | optional (default `false`) |

`tests_machines()` in `views_machines.py` also returns `machines_list`,
`machines_total_count`, `machines_filters_active`, `machines_page_size`, and
`has_authenticated_user`; the fragment does not read them.

Out-of-band updates: `#machines-pagination`, the hidden inputs
`#machines_sort`, `#machines_order`, `#machines_page`, and `#workers-count`.
This keeps homepage polling, sorting, paging, and filtering in sync without
replacing the filter controls themselves.

Each machine row, from `_normalize_machine_row()` in `views_machines.py`:
`username`, `country_code`, `concurrency`, `unique_key`, `worker_url`,
`worker_short`, `nps`, `nps_m` (preformatted string), `max_memory`, `system`,
`uname`, `worker_arch`, `compiler`, `compiler_version`, `compiler_label`,
`python`, `python_version`, `python_label`, `version`, `version_label`,
`run_url`, `run_label`, `last_active_label`, `last_active_sort`,
`last_updated`.

The `#workers-count` label uses the short format `Workers - <total>`, or
`Workers - <total> (<filtered>)` when a filter is active. The homepage and its
page-1 live run-table fragment recompute the filtered value from the current
machine snapshot rather than trusting the last cookie-backed count, so a
collapsed panel still shows a live filtered count.

### `nns_content_fragment.html.j2`

Swaps `#nns-content`. Also included by `nns.html.j2` for first paint. The
fragment owns the summary cards, explanatory copy, filter form, view toggle,
pagination, and table, so filtered htmx responses keep the whole vertical page
order synchronized with server state.

| Key | Type | Required |
|-----|------|----------|
| `nns` | list of nn row dicts | yes |
| `pages` | list | yes |
| `cookie_max_age` | int | yes |
| `filters` | dict `{network_name, user, master_only}` | optional (default `{}`) |
| `nns_summary` | dict `{nets, master_nets, contributors, downloads}` | optional (defaults to zeros) |
| `sort`, `order`, `view` | string | optional |
| `num_nns`, `max_all` | int | optional |
| `is_truncated` | bool | optional |
| `network_name_filter`, `user_filter` | string | optional fallbacks when `filters` is absent |
| `master_only` | bool | optional fallback when `filters` is absent |

Each nn row: the raw nn document plus `time_label`, `name`, `name_url`, `user`,
`first_test_label`, `first_test_url`, `last_test_label`, `last_test_url`,
`downloads`, `is_master`.

Behavior notes:

- Search is URL-driven and served by the same `/nns` endpoint; full-page
  rendering still works without JavaScript.
- Typing in `network_name` or `user` and toggling `master_only` triggers htmx
  requests; submit remains the non-JS fallback.
- `network_name` and `user` are literal case-insensitive substring filters.
  Regex metacharacters are escaped before reaching MongoDB, and regex syntax is
  not part of the public contract.
- The fragment pairs a hidden `master_only=0` field with the checkbox
  `value="1"` and `data-ui-cookie-*` attributes, so unchecked htmx requests and
  cookie persistence stay synchronized without inline JavaScript.
- Because the whole filter form lives inside the swapped fragment, this page
  needs no out-of-band hidden-input refresh.

Sortable headers are dual-mode links targeting `#nns-content` with
`hx-push-url="true"`.

### `pagination.html.j2`

Reusable pagination control. Renders nothing unless `pages` has more than three
entries.

| Key | Type | Required |
|-----|------|----------|
| `pages` | list of dicts `{idx, url, state}` | yes |
| `pagination_hx_target` | string (element id, no `#`) | optional; empty means plain links |
| `pagination_hx_push_url` | bool | optional (default `false`) |
| `pagination_hx_include` | string | optional |
| `pagination_hx_sync` | string | optional |
| `pagination_hx_disinherit` | string | optional |
| `pagination_hx_params` | string | optional |

`state` values: `"active"`, `"disabled"`, or the empty string. Callers set the
`pagination_hx_*` variables with `{% set %}` before including the partial, and
pass `pages` through `{% with pages=... %}`.

### `pending_users_nav_fragment.html.j2`

Returned by `user_management_pending_count` for
`/user_management/pending_count`, and included by `base.html.j2` for first
paint. Uses shared base context only: `pending_users_count` and
`urls.user_management`. The handler returns an empty context dict.

### `run_table.html.j2`

Renders one collapsible run-table panel: heading, optional toggle button,
optional filters include, pagination above and below, and the row body. Every
key is optional.

| Key | Type | Default |
|-----|------|---------|
| `runs` | list of run row dicts | `[]` |
| `header` | string or `None` | `none` |
| `count` | int or `None` | `none` |
| `count_text` | string or `None` | `none` (falls back to `<header> - <count> tests`) |
| `alt` | string | `""` (empty-table message, shown when `count == 0`) |
| `toggle` | string or `None` | `none` (element id for the collapse target and button) |
| `toggle_state` | `"Hide"` or `"Show"` | `"Show"` |
| `cookie_name` | string | `<toggle>_state` |
| `panel_id` | string | `toggle`, else `"finished"` |
| `pages` | list | `[]` |
| `show_delete` | bool | `false` |
| `show_gauge` | bool | `false` |
| `filters_template` | template name or `None` | `none` |
| `active_run_filters` | dict or `None` | `none` |

The tbody id is `<panel_id>-tbody` and the count span id is
`<panel_id>-count`; the OOB payloads in `tests_run_tables_fragment.html.j2`
target exactly those ids. The toggle button reads
`cookies.ui_state_max_age` for `data-toggle-cookie-max-age`.

### `run_table_row_fragment.html.j2`

Renders one `<tr>` with `id="run-<run_id>"`.

| Key | Type | Required |
|-----|------|----------|
| `row` | run row dict | yes |
| `show_delete` | bool | yes |
| `show_gauge` | bool | yes (forwarded to `elo_results.html.j2`) |
| `panel_id` | string | optional; when it equals `"active"` the row gains filter data attributes |

`row` is shaped by `build_run_table_rows()` in
`server/fishtest/http/template_helpers.py`: `run` (the full run document),
`run_id`, `active_filter_index`, `start_date_label`, `user_short`, `user_name`,
`user_url`, `is_finished`, `is_sprt`, `new_tag_short`, `run_url`, `diff_url`,
`live_label`, `live_url`, `tc_label`, `threads`, `cores_label`, `info_html`
(pre-escaped `Markup`).

`diff_url` comes from the canonical run diff URL builder in `util.py`. The
delete form embeds `csrf_token` from the shared base context.

### `run_tables.html.j2`

Composes the pending-approval, paused, failed, active, and finished panels by
including `run_table.html.j2` five times. On page 1 it also emits the
`?live=run_tables` poller (`hx-swap="none"`, `poll.tests_run_tables` cadence,
visibility-gated).

| Key | Type | Required |
|-----|------|----------|
| `page_idx` | int | yes |
| `pending_approval_runs`, `paused_runs`, `failed_runs`, `active_runs` | list of run rows | yes on page 1 |
| `finished_runs` | list of run rows | yes |
| `num_finished_runs` | int | yes |
| `finished_runs_pages` | list | yes |
| `finished_title_text` | string | yes |
| `live_url` | string or `None` | yes on page 1 |
| `show_gauge` | bool | yes |
| `active_count_text` | string | optional |
| `active_run_filters` | dict or `None` | optional |
| `prefix` | string | optional (default `""`) |
| `toggle_states` | dict | optional (default `{}`) |
| `username` | string | optional; when set, the Active filters panel is omitted |

`prefix` comes from `run_tables_prefix(username)` and namespaces the per-panel
toggle ids so the homepage and per-user pages keep separate collapse cookies.

### `tasks_content_fragment.html.j2`

Returned by `tests_tasks` for `/tests/tasks/{id}`. Swaps the scrolling task
table into `#tasks-content` and refreshes the fixed controls and pagination out
of band.

| Key | Type | Required |
|-----|------|----------|
| `tasks` | list of task row dicts | yes (consumed by the rows partial) |
| `show_pentanomial` | bool | yes |
| `show_residual` | bool | yes |
| `pages` | list of pagination dicts | yes |
| `run_id` | string | optional (default `""`) |
| `show_task` | int | optional (default `-1`) |
| `sort` | string | optional (default `idx`) |
| `order` | string | optional (default `desc`) |
| `q` | string | optional (combined worker/info filter) |
| `view` | string | optional (default `paged`) |
| `current_page` | int | optional (default `1`) |
| `num_tasks`, `max_all` | int | optional |
| `is_truncated` | bool | optional |

The handler also supplies `run`, `approver`, and `chi2`, which this fragment
does not read.

Out-of-band targets: `#tasks-view-controls`, `#tasks-pagination`, and the
hidden inputs `#tasks_sort`, `#tasks_order`, `#tasks_view`, `#tasks_page`.
`#tasks-view-controls` is wrapped in a `<template>` element.

Each task row, from `build_tasks_rows()` in `http/template_helpers.py`:
`task_id`, `row_class`, `worker_label`, `worker_url`, `worker_is_bad`,
`info_label`, `info_filter_text`, `pgn_url`, `last_updated_label`,
`last_updated_sort`, `played_label`, `played_sort`, `results_cells`, `wins`,
`losses`, `draws`, `crashes`, `time_losses`, `pentanomial_sort`,
`residual_label`, `residual_sort`, `residual_bg`.

### `tasks_controls_fragment.html.j2`

Included by `tests_view.html.j2` for the page-owned controls, and returned out
of band by `tasks_content_fragment.html.j2` after sort, filter, or view
changes.

| Key | Type | Required |
|-----|------|----------|
| `run_id` | string | yes |
| `show_task` | int | yes |
| `sort`, `order`, `view` | string | yes |
| `num_tasks`, `max_all` | int | yes |
| `is_truncated` | bool | yes |
| `q` | string | optional (default `""`) |

### `tasks_rows_fragment.html.j2`

| Key | Type |
|-----|------|
| `tasks` | list of task row dicts |
| `show_residual` | bool |

`show_pentanomial` controls only the table header in
`tasks_content_fragment.html.j2` and is not read here.

### `tests_filter_tabs_fragment.html.j2`

Renders the All / Green / Yellow / LTC tab strip. Included by
`tests_finished_content_fragment.html.j2`,
`tests_finished_results_fragment.html.j2`, and
`tests_user_content_fragment.html.j2`.

| Key | Type | Required |
|-----|------|----------|
| `filters` | dict | optional (default `{}`) |
| `hx_target` | string (element id, no `#`) | optional (default `""`) |
| `base_url` | string | optional (default `/tests/finished`) |

Recognized `filters` keys: `success_only`, `yellow_only`, `ltc_only`,
`all_query_string`, `green_query_string`, `yellow_query_string`,
`ltc_query_string`, and `filtered_query_suffix`. `get_paginated_finished_runs()`
in `views_finished.py` supplies all of them; the per-tab query strings fall
back to `?<tab>=1` plus `filtered_query_suffix` when absent. These strings
preserve username and free-text search state across tab switches.

### `tests_finished_content_fragment.html.j2`

Full-page body for `/tests/finished`, included by `tests_finished.html.j2`. It
owns the search-first GET form or the tab strip, plus the
`#tests-finished-content` wrapper around the results fragment.

| Key | Type |
|-----|------|
| `filters` | dict (`ltc_only`, `success_only`, `yellow_only`, `username_query`, `text`, `max_count`, `mode`, and the tab query strings) |
| `search_mode` | bool |

Username substring search uses a cached finished-run username list and the
exact-username finished-run path. The `text` field runs a case-insensitive
MongoDB text search against the last-column run info text on finished rows
only. The form preserves the effective `max_count` cap, whose anonymous and
authenticated ceilings are exposed to templates as
`finished.filter_max_count_anon` and `finished.filter_max_count_auth`.

The results fragment supplies the rest of the required context.

### `tests_finished_results_fragment.html.j2`

Returned by `tests_finished` for htmx requests, and included by
`tests_finished_content_fragment.html.j2` for first paint. Swaps
`#tests-finished-content`.

| Key | Type | Required |
|-----|------|----------|
| `filters` | dict | yes |
| `finished_runs` | list of run rows | yes |
| `num_finished_runs`, `visible_finished_runs`, `finished_page_size` | int | yes |
| `finished_runs_pages` | list | yes |
| `page_idx` | int | yes |
| `title_text` | string | yes |
| `show_gauge` | bool | yes |
| `search_mode` | bool | optional (default `false`) |
| `is_hx` | bool | optional (default `false`) |

In navigation mode (`is_hx` true and `search_mode` false) it piggy-backs an
out-of-band `outerHTML` replacement of `#tests-finished-filter-tabs`, so the
active tab stays aligned with the pushed URL after htmx tab clicks.

### `tests_run_tables_fragment.html.j2`

Returned by `_render_tests_run_tables_live_fragment()` for
`/tests?live=run_tables` and `/tests/user/{username}?live=run_tables`. The
poller uses `hx-swap="none"`, so every element in this fragment is out of band.
A non-htmx request to the same URL redirects to the plain page URL.

| Key | Type | Required |
|-----|------|----------|
| `panels` | list of dicts, each `{tbody_id, rows, show_delete, empty_text}` | yes |
| `count_updates` | list of dicts, each `{id, text}` | yes |
| `machines_count` | int | optional; homepage only |
| `workers_count_text` | string | optional; used with `machines_count` |
| `stats` | dict `{pending_hours, cores, nps_m, games_per_minute}` | optional; homepage only |

Each `<tbody>` OOB payload is wrapped in a `<template>` element. Panel tbody
ids are `pending-tbody`, `paused-tbody`, `failed-tbody`, `active-tbody`, and
`finished-tbody`; count-update ids are the matching `*-count` spans.

Behavior notes:

- The unfinished-run rows use the same lightweight shape as `run_tables_ctx`.
- The `machines_count`, `workers_count_text`, and `stats` keys are omitted for
  the per-user route, because those targets exist on the homepage only.
- When homepage worker filters are active, the handler recomputes the filtered
  `#workers-count` value from the current machine snapshot instead of trusting
  the last cookie-backed count.
- The handler sets status `286` when no pending, paused, or active runs remain,
  which stops the poller.

### `tests_stats_content_fragment.html.j2`

Raw-statistics body, shared by the `/tests/stats/{id}` page shell and its
`HX-Request` responses. Swaps `#tests-stats-content`.

| Key | Type |
|-----|------|
| `stats` | dict |

`run` and `page_title` belong to the page shell and are not read here.

`stats`, built by `build_tests_stats_context()` in
`http/template_helpers.py`, contains `run_id`, `has_sprt`, `has_pentanomial`,
`has_spsa`, `context_rows`, `sprt_rows`, `draw_rows`, `sprt_bounds_rows`,
`sprt_note`, `llr_note`, `tri_note`, `bayes_note`, and the `pentanomial` and
`trinomial` sub-dicts (`basic_rows`, `llr_rows`, `aux_rows`, and for
`pentanomial` also `comparison_rows`).

Two local macros render the tables:

| Macro | Signature | Renders |
|-------|-----------|---------|
| `render_rows` | `render_rows(rows)` | Two-column label/value rows from `(label, value)` tuples |
| `render_bounds` | `render_bounds(rows)` | Five-column SPRT bounds rows from dicts with `label`, `logistic`, `normalized`, `bayes`, `score` |

For SPSA runs the fragment renders a single message in place of all statistics
tables.

The handler returns `286` for finished or failed runs and `204` for runs that
are neither active nor finished, so the page poller can settle.

### `tests_user_content_fragment.html.j2`

Swaps `#tests-user-content`. Also included by `tests_user.html.j2`.

| Key | Type | Required |
|-----|------|----------|
| `username` | string | yes |
| `is_approver` | bool | yes |
| `filters` | dict | yes (forwarded to the tab strip and to `run_tables.html.j2`) |
| `run_tables_ctx` | dict | optional; when absent, `run_tables.html.j2` reads the outer context |

Passing `username` into `run_tables.html.j2` suppresses the Active filters
panel, which is homepage-only.

### `tests_view_detail_fragment.html.j2`

Returned by `tests_view_detail` for `/tests/view/{id}/detail`. It sets
`oob = true` and composes the shared detail sections, so every element updates
out of band.

| Key | Type | Required |
|-----|------|----------|
| `run` | dict | yes |
| `run_status_label` | string | yes |
| `run_args` | list of `(name, value, url)` tuples | yes (details section) |
| `approver` | bool | yes (details section) |
| `document_size` | int | yes (details section) |
| `chi2` | dict | yes for non-SPSA runs (stats section) |
| `tasks_totals` | string | yes (Elo fragment) |
| `spsa_data` | dict or `None` | yes for SPSA runs |

Rendered behavior:

- OOB-refreshes the hidden `#tests-view-detail-expected` input;
- always includes `elo_results_fragment.html.j2`,
  `tests_view_details_section.html.j2`, and `tests_view_time_section.html.j2`;
- includes `tests_view_stats_section.html.j2` for non-SPSA runs;
- for SPSA runs emits an OOB `#spsa-data-<run_id>` `application/json` payload
  with `hx-swap-oob="innerHTML"`, so the mounted script node is preserved.

The handler returns `286` for finished or failed runs and `204` when the actual
status still matches the submitted `expected` value and is not `active`.

### `tests_view_details_section.html.j2`

Shared detail table. Root element `#tests-view-details`.

| Key | Type | Required |
|-----|------|----------|
| `run` | dict | yes |
| `run_args` | list of `(name, value, url)` tuples | yes |
| `approver` | bool | yes |
| `document_size` | int | yes |
| `oob` | bool | optional (default `false`); adds `hx-swap-oob="outerHTML"` |

Renders the run argument rows, document size, actions link, the raw-statistics
link where applicable, and the approver link when present.

### `tests_view_stats_section.html.j2`

Compact chi-square block. Root element `#tests-view-stats`.

| Key | Type | Required |
|-----|------|----------|
| `chi2` | dict with `chi2`, `dof`, `p` | yes |
| `oob` | bool | optional (default `false`) |

### `tests_view_time_section.html.j2`

Start time and last-updated rows. Root element `#tests-view-time`.

| Key | Type | Required |
|-----|------|----------|
| `run` | dict with `start_time` and `last_updated` datetimes | yes |
| `oob` | bool | optional (default `false`) |

### `tests_view_spsa_section.html.j2`

SPSA chart section, included by `tests_view.html.j2` only. Root element
`#tests-view-spsa`, carrying `data-run-id`.

| Key | Type | Required |
|-----|------|----------|
| `run` | dict | yes |
| `spsa_data` | dict or `None` | yes |
| `spsa_percentage_checked` | bool | yes |
| `oob` | bool | optional (default `false`) |

Rendered structure:

- a DOM-embedded `<script id="spsa-data-<run_id>" type="application/json">`
  payload exposing `param_names` plus server-shaped `chart_rows`; those rows
  fold together the start row, compact sampled history rows, and the current
  live point, with `c_values` present when `% c` mode can be rendered;
- the cookie-backed `% c` checkbox `#spsa_percentage`, rendered from
  `spsa_percentage_checked`;
- `#spsa_history_scroll`, the retained scroll container;
- `#spsa_history_plot`, the CSS-owned fixed-size chart shell;
- the chart toolbar and container used by `static/js/spsa.js`.

### `user_management_content_fragment.html.j2`

Swaps `#user-management-content`.

| Key | Type | Required |
|-----|------|----------|
| `selected_users` | list of user row dicts | yes (consumed by the rows partial) |
| `pages` | list | yes |
| `group` | string | optional (default `pending`) |
| `sort`, `order`, `q`, `view` | string | optional |
| `num_selected_users`, `max_all` | int | optional |
| `is_truncated` | bool | optional |

Each user row, from `_user_management_rows()` in `views.py`: `username`,
`user_url`, `username_key`, `registration_time`, `registration_label`,
`groups`, `groups_label`, `email`.

The outer GET form keeps `sort`, `order`, and `view` in hidden inputs. The
fragment refreshes `#user_management_sort`, `#user_management_order`, and
`#user_management_view` out of band.

### `user_management_rows_fragment.html.j2`

| Key | Type |
|-----|------|
| `selected_users` | list of user row dicts |
| `group` | string (selects the empty-state label) |

### `workers_content_fragment.html.j2`

Swaps `#workers-content`.

| Key | Type | Required |
|-----|------|----------|
| `blocked_workers` | list of worker row dicts | yes (consumed by the rows partial) |
| `show_email` | bool | yes |
| `pages` | list | yes |
| `filter_value` | string | optional (default `le-5days`) |
| `sort`, `order`, `q`, `view` | string | optional |
| `num_workers`, `max_all` | int | optional |
| `is_truncated` | bool | optional |

Each blocked worker row, from `_blocked_worker_rows()` in `views.py`:
`worker_name`, `worker_name_key`, `last_updated`, `last_updated_label`,
`actions_url`, `owner_email`, `mailto_url`. The email column and `mailto_url`
are populated only for approvers.

Sortable headers are dual-mode links targeting `#workers-content` with
`hx-push-url="true"`. The outer GET form keeps `sort`, `order`, and `view` in
hidden inputs; the fragment refreshes `#workers_sort`, `#workers_order`, and
`#workers_view` out of band.

### `workers_rows_fragment.html.j2`

| Key | Type |
|-----|------|
| `blocked_workers` | list of worker row dicts |
| `show_email` | bool |

## htmx shell-state rule

When a GET form stays outside the swapped fragment but the fragment owns the
sort, view, and pagination links, the fragment must refresh the stateful hidden
form inputs out of band:

- keep stable ids on the outer hidden inputs;
- return matching hidden inputs from the fragment with `hx-swap-oob="true"`.

This keeps later form submissions aligned with the current server-authoritative
table state without page-specific synchronization JavaScript. Contributors,
workers, user management, tasks, and machines all follow this pattern. `/nns`
does not need it, because its filter form is inside the swapped fragment.

## Authoring rules

1. **Templates are declarative.** All data shaping stays in view handlers.
   Templates receive pre-computed values and render them.

2. **JavaScript data.** Pass values through the `tojson` filter. Never
   interpolate Python values directly into `<script>` blocks.

3. **Display strings.** Prefer preformatted values (`*_label` keys) over
   formatting in templates.

4. **URLs.** Prefer explicit URL keys (`*_url`) over building URLs in
   templates.

5. **Macros.** Use Jinja2 macros for repeated patterns such as sortable
   headers and statistics rows.

6. **Request object.** Not used directly beyond `request.url`,
   `request.url.path`, and the `static_url` helper.

7. **Escaping boundary.** View handlers pass raw strings; autoescape handles
   HTML escaping at render time. Do not pre-escape values with `html.escape()`
   into plain strings, which double-escapes. Escaping in Python is correct only
   when building a `Markup` value: escape untrusted input first, then wrap.

8. **`safe` filter.** Use only on values already typed as `Markup`, or after an
   explicit `e` escape followed by controlled transformations, for example
   `{{ value | e | replace("\n", "<br>") | safe }}`.

9. **External links.** Every `target="_blank"` anchor must include
   `rel="noopener noreferrer"`.

10. **No inline event handlers.** Use `addEventListener` or delegated
    listeners instead of `onclick`, `onsubmit`, and similar attributes.

11. **Fragments do not extend `base.html.j2`.** Fragment templates are
    standalone files. They receive the shared base context but must not use
    `{% extends %}` or `{% block %}`.

12. **OOB elements carry their own `hx-swap-oob` attribute.** The handler does
    not set response headers for out-of-band updates. Each OOB element declares
    its own id and swap strategy, for example
    `<span id="count" hx-swap-oob="innerHTML">`.

13. **Table OOB requires `<template>` wrappers.** The HTML parser rejects
    `<tbody>` inside `<div>`:

    ```jinja
    <template>
      <tbody id="my-table" hx-swap-oob="innerHTML">
        {% for row in rows %}...{% endfor %}
      </tbody>
    </template>
    ```

    htmx processes the `<template>` content and discards the wrapper.

14. **DOM API over `innerHTML` in error handlers.** Build retry buttons with
    `createElement`, `textContent`, and `setAttribute` rather than string
    concatenation, so error messages cannot inject markup.

15. **Unicode over HTML entities.** Use the literal character instead of
    `&#8804;` or `&gt;`, so values that contain it do not need `safe`.

16. **Explicit CSRF hidden fields.** Every server-rendered `<form>` that posts
    to a `require_csrf` route must include:

    ```jinja
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    ```

    The meta-tag CSRF token is a fallback for htmx headers; forms must not rely
    on it as the sole transport.

17. **Polling trigger policy.** Every periodic htmx poller must combine a
    periodic trigger gated on `document.visibilityState === 'visible'`, an
    immediate `visibilitychange[...] from:document` refresh, and, for
    section-scoped pollers, a gate on the section's expanded state. See
    [1-architecture.md](1-architecture.md) for the full policy.

18. **Cadences and limits come from globals.** Read poll intervals from `poll`,
    debounce delays from `htmx`, cookie lifetimes from `cookies`, and finished
    search caps from `finished`. Do not write the numbers into templates.

## Adding a new template

### Page template

1. Create `templates/mypage.html.j2` extending `base.html.j2`:

   ```jinja
   {% extends "base.html.j2" %}

   {% block title %}My Page | Stockfish Testing{% endblock %}

   {% block body %}
   ...
   {% endblock %}
   ```

2. Document the context contract in this page, marking required and optional
   keys.

3. Build the context in the view handler. `build_template_context()` adds the
   shared base context automatically.

4. Return the context dict from the handler and register the route in
   `_VIEW_ROUTES` with `"renderer": "mypage.html.j2"`.

5. Add a test that renders the template.

### Fragment template

1. Create `templates/mypage_fragment.html.j2` as a standalone file, with no
   `{% extends %}` and no `{% block %}`.

2. Document the context contract in this page, including the swap target and
   any out-of-band ids.

3. In the view handler, return the fragment when `HX-Request` is present:

   ```python
   return _render_hx_or_context(
       request,
       "mypage_fragment.html.j2",
       context,
   )
   ```

   Use `_render_hx_fragment()` directly when the fragment is the only response
   shape for that route.

4. Add `hx-swap-oob` attributes to the elements that update out of band. Wrap
   table bodies in `<template>`.

5. Give every key a template-side default when the same file must also render
   from a smaller context, since `StrictUndefined` turns a missing key into a
   render error.

6. Add a test that covers both the full-page and fragment responses.
