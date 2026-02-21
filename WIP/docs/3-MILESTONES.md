> [!IMPORTANT]
> **Disclaimer (high-level roadmap):** This file is a milestone map, *not* the authoritative plan and *not* the architecture snapshot.
>
> - Authoritative plan: [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md)
> - Current repo snapshot: [2-ARCHITECTURE.md](2-ARCHITECTURE.md)

# Pyramid → FastAPI roadmap (milestones)

Date: 2026-02-12

This document describes *how we switch* from Pyramid (WSGI) to FastAPI/Starlette (ASGI), and (optionally) from Mako to Jinja2.
It stays intentionally high-level to avoid duplicating (and drifting from) the details in:

- the plan ([1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md))
- the current architecture snapshot ([2-ARCHITECTURE.md](2-ARCHITECTURE.md))

If you need details (exact behaviors, invariants, operational constraints, code pointers), prefer those two docs.

## Milestone 0 — Where we are now

FastAPI/Starlette is the active serving stack in this repo, and an `http/` layer exists to preserve Pyramid-era behavior.
For the current implementation and module map, see [2-ARCHITECTURE.md](2-ARCHITECTURE.md).

Important distinction:

- “Drop Pyramid” can mean two different things:
  - Pyramid *framework/runtime* (packages, app factory, request/response objects in the serving path)
  - Pyramid-*era behavior contracts* (“protocol”): routes, redirects, cookie semantics, error/JSON shapes, template output

This roadmap treats those as separate milestones on purpose.

## Milestone 1 — Drop Pyramid runtime/framework dependency (reached)

Context: server + worker are running mostly fine on a DEV server, so the “serve without Pyramid” milestone is already met.

Goal: the running server should not need Pyramid to boot, route, render, or authorize.

Definition of done:

- Pyramid packages are not required to run the server.
- No Pyramid app factory in the serving path.
- No Pyramid request/response objects in the serving path.
- Any remaining Pyramid-*era* compatibility is implemented as explicit Starlette/FastAPI middleware + helpers.

Non-goal:

- This milestone does *not* mean we can change externally-visible behaviors; it only means Pyramid isn’t the framework anymore.

## Milestone 2 — Behavioral parity + contract-test gate (reached)

> [!NOTE]
> **Protocol contracts** (worker API + UI behavior) are canonically documented in [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md).

Goal: before doing any "idiomatic FastAPI" refactors, lock down parity for the externally-visible behaviors that matter.

Definition of done (examples):

- Worker API endpoints: status codes + JSON shape parity (including any fields clients rely on).
- UI flows: login/logout + a couple representative UI pages (403/404 behavior, redirects, cookies).
- Error shaping: API returns JSON error payloads; UI returns HTML templates.

Status:

- Milestone 2 is complete; see [3.2-ITERATION.md](3.2-ITERATION.md) for completion record.

## Milestone 3 — Make async/blocking boundaries explicit (reached)

> [!NOTE]
> **Async/blocking boundaries** (event loop vs threadpool) are canonically documented in [2.1-ASYNC-INVENTORY.md](2.1-ASYNC-INVENTORY.md).

Goal: ensure the concurrency model is intentional and observable.

Definition of done:

- Blocking DB work runs in a safe context (sync endpoints + threadpool, or explicit offloading).
- Any async endpoints do not accidentally block the event loop.
- Production worker/process settings are consistent with the runtime invariants.

Status:

- Milestone 3 is complete; see [3.3-ITERATION.md](3.3-ITERATION.md) for completion record.


## Milestone 4 — Make the FastAPI/Starlette glue explicit + maintainable (incrementally)

Status:

- Milestone 4 is complete; see completion record in [3.4-ITERATION.md](3.4-ITERATION.md).

Goal: reduce glue where it only exists due to “we haven’t refactored yet”, while keeping parity for behaviors that are relied on.

Examples of what “good FastAPI glue” means (choose deliberately, don’t churn):

- Clear middleware stack (one responsibility each).
- Explicit dependency injection where it simplifies lifecycle and testing.
- Centralized exception handlers instead of ad-hoc per-route shaping.
- Request parsing is explicit (e.g., dependencies) where it reduces bugs/duplication (not as a style exercise).

Scope guidance (incremental, safe-by-default):

- Prefer changes that shrink glue without changing externally-visible behavior.
- Keep upstream behavior parity tests as the safety net; do not skip the contract gate.
- Favor small, reversible refactors over broad rewrites.

Candidate areas (examples, not a mandate):

- Move one-off request parsing into dependencies when it reduces duplication.
- Consolidate error shaping that is currently spread across helpers.

Non-goals for this milestone:

- No redesign of DB adapters or scheduling semantics.

Completion notes (what landed):

- Explicit + maintainable middleware/dependency patterns while preserving protocol parity.
- Operational hardening for misrouted primary-only endpoints (worker guard + UI primary-only guard).
- Next work moves to Milestone 5; see [3.5-ITERATION.md](3.5-ITERATION.md).

## Milestone 5 — Idiomatic plumbing, HTTP remains the readable entrypoint

Status:

- Milestone 5 is complete (2026-01-29); see [3.5-ITERATION.md](3.5-ITERATION.md).

Goal: use idiomatic FastAPI/Starlette **plumbing** while keeping `http/api.py` and `http/views.py` as the primary, human‑readable entrypoints.

This milestone keeps the single‑file narrative (HTTP) intact and avoids file‑spread, while still adopting best‑practice plumbing (dependencies, middleware, explicit threadpool boundaries) *inside* the HTTP layer.

Definition of done:

- UI and API entrypoints remain in `http/api.py` and `http/views.py`, with linear, readable flow.
- FastAPI/Starlette plumbing is used where it reduces risk: explicit dependencies, explicit middleware order, explicit threadpool boundaries.
- No new route‑split folder explosion; HTTP entrypoints stay the primary narrative for routes.
- Pyramid‑compat objects exist only at true boundaries (template request object, session/CSRF helpers), not as the internal programming model.
- Contract‑test / parity gate remains the safety net; behavior changes are explicit and reviewed as such.

Human metrics (target values):

- Hop count ≤ 1 per endpoint (route → domain call).
- File hops ≤ 1 for UI/API entrypoints (stay in HTTP).
- Helper calls ≤ 2 per endpoint (avoid helper chains).
- Single‑pass readability: endpoint is understandable without opening other files.

Non-goals:

- No UI redesign.
- No “make everything async” rewrite.

## Milestone 6 — Contract tests & dual‑suite tests

Goal: make the active FastAPI/Starlette behavior the single source of truth for contract tests while **keeping** the legacy Pyramid tests/stubs for upstream rebase safety.

Status: **Complete (2026-01-30)** — worker routes covered by FastAPI contract tests; legacy Pyramid tests/stubs retained for rebase safety.

Definition of done:
- All protocol contract tests (Protocol A worker API + Protocol B UI flows) run against the FastAPI app using `TestClient` or direct callable tests.
- Legacy Pyramid‑importing unit tests are retained alongside FastAPI tests for rebase safety.
- Test-only Pyramid stubs under `server/tests/pyramid/` are retained (do not delete).
- CI passes with both suites present.

Verification gates:
- Full contract test suite for worker endpoints (status codes, `duration`, error shape).
- UI contract tests: login/logout, CSRF, 403/404 HTML, representative list/detail pages.
- Parity scripts updated to point at `server/fishtest/http/*`.

Metrics:
- Contract coverage: all worker endpoints + representative UI flows pass in CI.

Reference implementations (from the bloat branch, to be adapted to `http/`):
- [__fishtest-bloat/server/tests/test_web_api_worker.py](__fishtest-bloat/server/tests/test_web_api_worker.py)
- [__fishtest-bloat/server/tests/test_web_middleware.py](__fishtest-bloat/server/tests/test_web_middleware.py)
- [__fishtest-bloat/server/tests/test_web_session.py](__fishtest-bloat/server/tests/test_web_session.py)
- [__fishtest-bloat/server/tests/test_web_ui_actions.py](__fishtest-bloat/server/tests/test_web_ui_actions.py)

## Milestone 7 — HTTP boundary extraction without bloat

Goal: make the HTTP layer idiomatic Starlette/FastAPI while preserving externally‑visible behavior; reduce shim usage without recreating the helper bloat seen in earlier drafts.

Scope and constraints:
- Preserve protocol parity at all times (tests + parity tools gate changes).
- Incremental: replace shims only when tests cover the affected surfaces.
- Keep `http/api.py` and `http/views.py` readable; avoid route‑layer fragmentation.

Phase 0 (inventory): enumerate the remaining Pyramid-era plumbing surfaces to de-shim, with an explicit list of:
- request shim entrypoints and constructors
- response header helpers
- session flags and cookie semantics
- template context injection
- JSON body parsing and error shaping

Phase 1 (explicit): extract HTTP plumbing from `http/api.py` and `http/views.py` into the boundary module [server/fishtest/http/boundary.py](server/fishtest/http/boundary.py), focused only on shared plumbing and dependencies.
- Keep route handlers as the readable entrypoints; the boundary module only holds plumbing (dependencies, middleware wiring helpers, session/context adapters).
- Move request shim constructors, view-config dispatch/registry helpers, JSON body parsing, and response header/session cookie helpers into the boundary module.
- Add tests that exercise the boundary module directly (unit or lightweight integration), and keep contract/parity tests green during the extraction.

Key outcomes:
- Replace request‑shim call patterns with typed dependencies (`Annotated` aliases) for DB handles, session, and UI context.
- Adopt Starlette/FastAPI session middleware and migrate template access via a thin, well‑tested compatibility layer during transition.
- Remove remaining `TemplateRequest` surface only after templates are ported or an adapter is proven identical.
- UI error rendering helpers live in [server/fishtest/http/ui_errors.py](server/fishtest/http/ui_errors.py), and [server/fishtest/http/errors.py](server/fishtest/http/errors.py) depends on that helper layer.

Verification gates:
- Contract tests (Milestone 6) remain green after each refactor.
- Parity scripts continue to report OK or expected whitelists.

Notes:
- Avoid helper explosion and multi‑hop flow; the route layer should remain readable in a single pass.
- No “glue” moniker should remain in HTTP helpers; rename any surviving helpers that still use that label.

Metrics:
- Hop count ≤ 1 per endpoint; helper calls ≤ 2 per endpoint in `http/api.py` and `http/views.py`.
- No new route‑split folder tree; HTTP entrypoints remain the primary narrative.

## Milestone 8 — Templates: legacy Mako + Jinja2 parity (new Mako retired)

Goal: preserve UI behavior while aligning Jinja2 output with legacy Mako via parity tooling.

This milestone focuses on parity, comparability, and a common helper base. It does not require an immediate full cutover to Jinja2.

Core objectives:
- Keep legacy Mako templates read-only as the parity anchor.
- Use a shared helper base for Jinja2 rendering.
- Compare outputs between legacy Mako and Jinja2.

Hard constraint (non-negotiable):
- MUST keep legacy Mako templates in [server/fishtest/templates](server/fishtest/templates) for upstream rebase safety. Do not delete, rename, or “clean up” legacy templates.

Suggested approach:

1. Add shared helper base
  - Create a single helper module for filters/globals used by the runtime.
  - Keep URL generation and cache-busting semantics compatible with legacy Mako.
2. Introduce Jinja2
  - Use a Jinja2 environment for `server/fishtest/templates_jinja2/`.
  - Compare output against legacy Mako with parity scripts.
3. Compare and measure
  - Use parity tools to compare rendered HTML.
  - Measure complexity via template metrics.

Status:

- Complete (2026-02-05); see [3.8-ITERATION.md](3.8-ITERATION.md).
- The new Mako track was retired in Milestone 10; legacy Mako remains as the parity anchor.
- Metrics snapshot and scripts are tracked in [11.3-TEMPLATES-METRICS.md](11.3-TEMPLATES-METRICS.md).

Definition of done:

- Jinja2 templates render with parity to legacy Mako where required.
- A shared helper base is used by the runtime.
- Parity comparison scripts exist for legacy Mako vs Jinja2.

## Milestone 9 — Template rendering alignment (Starlette Jinja2 + Mako)

Goal: align template rendering with Starlette best practices while preserving UI parity, and document ASGI-specific risks and choices.

Status:
- In progress (2026-02-05); see [3.9-ITERATION.md](3.9-ITERATION.md).

Definition of done:
- Jinja2 rendering uses Starlette `Jinja2Templates` directly or a compatible wrapper with `TemplateResponse`, `url_for`, and optional `context_processors`.
- Mako rendering provides a TemplateResponse-equivalent path with request context and debug info.
- ASGI risks/choices for Jinja2 and Mako are documented in architecture and reference docs.
- Parity remains green for legacy Mako and Jinja2.

## Milestone 10 — Jinja2-only runtime (legacy Mako kept for parity scripts)

Goal: rewrite the Jinja2 template set in an idiomatic, modern style and run **only Jinja2** at runtime, while keeping legacy Mako templates for parity tooling.

Status:

- Complete (2026-02-09); Jinja2-only runtime is live, templates use .html.j2, parity tooling is centralized in WIP/tools, and context coverage is clean.

Scope and intent:
- Legacy Mako templates in [server/fishtest/templates](server/fishtest/templates) remain untouched and used only by parity scripts.
- Runtime rendering uses only Jinja2 templates in [server/fishtest/templates_jinja2](server/fishtest/templates_jinja2).
- Retire any dual-renderer runtime wiring and temporary parity-template paths.
- The Jinja2 set is idiomatic and **not** required to be line-for-line comparable with Mako.
- Keep `http/api.py` and `http/views.py` changes minimal to preserve parity metrics against legacy twins.
- Centralize parity helper scripts in WIP/tools so the runtime HTTP layer only exposes Jinja2 helpers.
- Templates in `server/fishtest/templates_jinja2` use `.html.j2`, and parity tooling maps legacy `.mak` names to those Jinja2 files.

Definition of done:
- Jinja2 templates in [server/fishtest/templates_jinja2](server/fishtest/templates_jinja2) are idiomatic (macros, explicit context, minimal request coupling).
- Runtime uses Starlette Jinja2 best practices (`Jinja2Templates` + `TemplateResponse`) and stays off the event loop.
- Legacy Mako templates remain available for parity scripts, but are not wired into runtime rendering.
- Parity helper scripts run from WIP/tools and keep the legacy templates read-only while capturing parity diffs.

## Milestone 11 — Replace Pyramid shims with idiomatic FastAPI/Starlette (rebase-safe)

Goal: eliminate the remaining Pyramid-era request/response shims, decorator stubs, and exception classes from the HTTP layer, replacing them with idiomatic FastAPI/Starlette patterns — while keeping `http/api.py` and `http/views.py` structurally comparable to their legacy twins (`api.py`, `views.py`) for upstream rebase safety.

Status: Complete (2026-02-11); see [3.11-ITERATION.md](3.11-ITERATION.md) and [93-CLAUDE-M11-REPORT.md](93-CLAUDE-M11-REPORT.md).

Context:

After Milestone 10, the template layer is fully Jinja2. But the HTTP dispatch layer still carries substantial Pyramid compat surfaces:

- `_RequestShim` (60 lines in `views.py`) wrapping every UI request
- `ApiRequestShim` (45 lines in `boundary.py`) wrapping every API request
- `_ResponseShim`, `ResponseShim` for header propagation
- `HTTPFound` / `HTTPNotFound` exception shims for control flow
- `view_config` / `notfound_view_config` / `forbidden_view_config` no-op decorator stubs
- `_ROUTE_PATHS` static dict emulating Pyramid's `route_url()`
- `TemplateRequest` dataclass for template-side `request.static_url()` / `request.GET`
- `remember()` / `forget()` Pyramid auth compat helpers
- `apply_response_headers()` copying shim headers to Starlette responses

The `__fishtest-bloat` branch showed how **not** to do this: it scattered code across 22 files, introduced multi-hop indirection, and added `itsdangerous`-based session middleware that duplicated Starlette's own `SessionMiddleware`. Milestone 11 must avoid that bloat while still adopting idiomatic patterns.

Design constraints:

- All routes remain in `http/api.py` and `http/views.py` (no file-spread).
- `api.py` and `views.py` must stay structurally comparable to the legacy twins: same endpoint order, same logical flow, similar line structure — to keep upstream rebases cheap.
- Hop count ≤ 1 per endpoint; helper calls ≤ 2 per endpoint.
- Adopt Starlette session middleware (`itsdangerous`-backed `SessionMiddleware` or the existing `FishtestSessionMiddleware`) only when it reduces code and preserves cookie/CSRF semantics.
- Use FastAPI dependency injection (`Depends`, `Annotated` aliases) for DB handles, session, and auth context — replacing the shim constructors.
- Replace Pyramid exception shims with `RedirectResponse` / `raise HTTPException` or Starlette equivalents.
- Replace `TemplateRequest` surface with direct template globals or context keys already provided by `build_template_context()`.
- Keep legacy Pyramid test stubs in `tests/pyramid/` for upstream rebase safety.
- Contract tests, parity scripts, and "stop the line" conditions remain the safety net.

Scope exclusions:

- No template changes (Jinja2 migration is complete).
- No DB adapter or scheduling redesign.
- No worker protocol shape changes.
- No `tests/pyramid/` stub deletion (kept for rebase).

Definition of done:

- `http/views.py` no longer defines `_RequestShim`, `_ResponseShim`, `_CombinedParams`, `HTTPFound`, `HTTPNotFound`, `view_config`, `notfound_view_config`, `forbidden_view_config`, or `_ROUTE_PATHS`.
- `http/boundary.py` no longer defines `ApiRequestShim`, `ResponseShim`, `apply_response_headers()`, or the `RequestShim*` protocol classes.
- `http/template_request.py` is removed or reduced to a minimal `static_url` helper.
- `http/api.py` and `http/views.py` use FastAPI/Starlette idioms directly (dependencies, `RedirectResponse`, `request.state`, `request.session`).
- `api.py` and `views.py` remain structurally comparable to legacy twins (parity AST checks stay green or show only expected drifts).
- All contract tests and parity scripts pass.
- No new file-spread; no new multi-hop helper chains.

## Milestone 12 — Hardening, route idiom evaluation, and Pydantic assessment

Goal: fix open issues from M11, evaluate whether `_VIEW_ROUTES` data-driven registration can be replaced by idiomatic `@router.get/post` decorators, and assess Pydantic feasibility vs the existing vtjson validation model.

Status: Complete (2026-02-12); see [3.12-ITERATION.md](3.12-ITERATION.md).

Context:

M11 is complete with 7 Pyramid shim classes removed, 2 thin adapters retained (`_ViewContext`, `ApiRequestShim`), a pure ASGI `FishtestSessionMiddleware` adopted, and 143 net lines removed. Several quality and hardening items remain from the M11 report (93-CLAUDE-M11-REPORT.md Section 9).

Additionally, the route registration model and the Pydantic question have been deferred from earlier milestones and now warrant explicit analysis.

Scope:

**Phase A — M11 open issues (hardening):**
- Replace `MakoUndefined` with `StrictUndefined` in the Jinja2 environment (template context coverage is clean).
- Convert `RedirectBlockedUiUsersMiddleware` from `BaseHTTPMiddleware` to pure ASGI (now feasible via `scope["session"]`).
- Add session middleware integration tests (mock user lookup; verify cookie setting/clearing, "remember me" max-age, CSRF persistence).
- Add deployment note: M11 invalidates all existing sessions (atomic cookie format cutover).
- Document expected parity similarity ranges per milestone in this file.

**Phase B — Route registration evaluation:**
- Assess whether `_VIEW_ROUTES` data-driven registration + `_dispatch_view()` + `_make_endpoint()` can be replaced by `@router.get/post` decorators on each view function.
- Key blocker: `_dispatch_view()` centralizes 11 cross-cutting concerns (session extraction, POST parsing, CSRF enforcement, `_ViewContext` construction, primary-instance guard, threadpool dispatch, redirect handling, template rendering, session commit, HTTP cache, response headers). Replacing it with decorators requires decomposing these concerns into FastAPI dependencies or middleware.
- `api.py` already uses `@router.get/post` decorators (20 endpoints) — no shared dispatch pattern.
- Recommendation is captured in the iteration plan with pros/cons analysis.

**Phase C — Pydantic assessment:**
- Assess whether introducing Pydantic for API request body parsing would reduce bugs, improve type safety, or generate useful OpenAPI schemas.
- Key factors: vtjson deeply embedded (19 schemas, 37 imported functions, cross-field validators using `ifthen`/`cond`/`intersect`/`lax`), only 3 `validate()` call sites in `api.py`, worker error format (`{"error": "...", "duration": N}` at status 400) conflicts with FastAPI default 422 validation.
- Recommendation is captured in the iteration plan with pros/cons analysis.

Design constraints:
- All existing constraints carry forward: no file-spread, hop count ≤ 1, helper calls ≤ 2, structural comparability with legacy twins.
- Contract tests, parity scripts, and "stop the line" conditions remain the safety net.
- No worker protocol shape changes.

Scope exclusions:
- No template changes.
- No DB adapter or scheduling redesign.
- No `tests/pyramid/` stub deletion.

Definition of done:
- All M11 high/medium-priority action items resolved.
- Route registration evaluation documented with explicit pros/cons/recommendation.
- Pydantic assessment documented with explicit pros/cons/recommendation.
- All contract tests and parity scripts pass.
- Iteration plan written at [3.12-ITERATION.md](3.12-ITERATION.md).

Completion outcomes summary:
- `MakoUndefined` was replaced with `StrictUndefined`; middleware stack is now pure ASGI (`BaseHTTPMiddleware` users: 0).
- Session/boundary integration tests were added; full local suite passed (187/187).
- Route registration decision recorded: keep `_VIEW_ROUTES` + `_dispatch_view()` (no decorator migration).
- Pydantic assessment recorded: vtjson and Pydantic solve different problems (object validation vs data parsing); vtjson remains sole validation layer; no Pydantic adoption planned. See M N-1 for closure.
- Phase 6 parity remediation completed: dead API helpers/assertions removed, `InternalApi` parity stub restored, and `ensure_logged_in()` return-contract documented.
- Operational parity gaps closed: `_base_url_set` first-request fallback confirmed in middleware and optional SIGUSR1 thread-dump support installed.
- Parity tooling strengthened: API AST parity now checks required class-presence parity (`InternalApi`) in addition to endpoint method-body parity.
- Phase 4 low-priority items closed explicitly:
  - **Won't do** in M12: split `template_helpers.py`, generate template `urls` from `router.routes`, migrate to Starlette `context_processors`.
  - **Done**: parity similarity trend tracking in `3.12-ITERATION.md` Appendix D.

Parity similarity expected ranges after M12:
- views: 0.70–0.72 (stable; M12 changes are infrastructure, not view logic)
- api: 0.74–0.76 (stable; no API body changes)

## Milestone N-1 — Pydantic assessment outcome (no adoption planned)

Status: assessed in M12; conclusion: vtjson stays, Pydantic not adopted.

### vtjson vs Pydantic — different tools, different jobs

**vtjson** validates existing Python objects against declarative schemas. It answers: "does this dict conform to these constraints?" It does not create objects, coerce types, or generate API schemas. Fishtest uses 19 vtjson schemas in `schemas.py` (37 imported functions) with ~15+ `validate()` call sites across both the domain layer (`rundb`, `actiondb`, `userdb`, `kvstore`, `workerdb`, `github_api`) and the HTTP layer (`http/api.py`, `http/views.py`).

vtjson features actually used: `ifthen`/`cond` (cross-field conditional validation), `intersect` (composable multi-constraint validators), `lax` (match shape ignoring extra keys), `union`/`complement` (Boolean algebra), callable predicates (`valid_results()`, `final_results_must_match()`). The most complex schema (`runs_schema`, ~100 lines) validates 50+ fields with 6 cross-field validators, conditional task/state invariants, and aggregated-data consistency — all declarative.

**Pydantic** deserializes raw data into typed model instances (`BaseModel`). It answers: "parse this JSON into a typed Python object." Pydantic is bundled with FastAPI but Fishtest does not use it for request parsing. The only Pydantic appearance in the codebase is a test fixture in `test_http_errors.py`.

They are **not interchangeable**:

| Concern | vtjson | Pydantic |
|---|---|---|
| Cross-field logic | `ifthen`, `cond`, `intersect` — declarative | `@model_validator` — imperative Python |
| Schema algebra | `union`, `intersect`, `complement`, `lax` — complete | `Union` only; no intersection or complement |
| Model classes needed | No — validates plain dicts directly | Yes — requires a `BaseModel` class per shape |
| Data source | Any Python object (MongoDB docs, in-memory dicts) | Typically JSON at an API boundary |
| Coercion | No (validates, never transforms) | Yes (auto-coerces types) |

### Why Pydantic is not adopted

1. **Scope mismatch.** Most validation happens in the domain layer (MongoDB documents), not at the HTTP boundary. Pydantic only helps at the API boundary — 3 call sites out of ~15+.
2. **Cross-field schemas don't translate.** `runs_schema` (~100 lines declarative vtjson) would need ~200+ lines of `@model_validator` chains. `action_schema` (16-branch `cond`) would need 16 model classes + a discriminated union.
3. **Error format conflict.** Workers expect HTTP 400 + `{"error": "...", "duration": N}`. Pydantic defaults to 422 with a different error shape. Custom exception handlers would be needed — added complexity, not reduced.
4. **Dual validation is worse than one system.** Pydantic for simple schemas + vtjson for complex ones = two validation systems to maintain.
5. **No OpenAPI need.** The API serves internal workers, not external consumers.

This milestone is effectively closed. Reassess only if a concrete use case appears where Pydantic provides safety or maintenance value that vtjson cannot — no such case exists today.

## Milestone N — Finalize branch for upstream merge

Goal: prepare the branch for upstream merge by removing legacy Pyramid/Mako code, updating authoritative documentation to reflect the finished project, and writing the conventional commit for the squashed branch.

Status: Phases 0–7 complete; Phase 8 pending (PR branch only). See [3.13-ITERATION.md](3.13-ITERATION.md) for iteration plan.

Target end-state for upstream at Milestone N:

- FastAPI + Starlette + Jinja2 + Uvicorn are the only active server stack.
- Pyramid and Mako are fully removed from runtime and repository surfaces used by the active server.
- Authoritative documentation (`1-FASTAPI-REFACTOR.md`, `2-ARCHITECTURE.md`) is updated to describe the final state, not the migration process.
- A conventional commit message is written for squashing the branch into a single commit for the upstream PR.
- No milestone task depends on `WIP/` paths (the `WIP/` tree is removed only in the PR branch).

### Task 1 — Delete legacy Pyramid code and test stubs

- Delete legacy test modules that still validate Pyramid-era surfaces:
  - `server/tests/test_actions_view.py`
  - `server/tests/test_api.py`
  - `server/tests/test_users.py`
- Keep domain-layer tests that do not depend on Pyramid and remain valuable:
  - `server/tests/test_rundb.py`
  - `server/tests/test_github_api.py`
- Delete Pyramid test stubs no longer needed once legacy tests are gone:
  - `server/tests/pyramid/__init__.py`
  - `server/tests/pyramid/security.py`
  - `server/tests/pyramid/httpexceptions.py`
  - `server/tests/pyramid/view.py`
  - `server/tests/pyramid/response.py`
  - `server/tests/pyramid/testing.py`

### Task 2 — Delete legacy Pyramid spec modules and move HTTP routes up

- Move active HTTP route modules up one level:
  - source move: `server/fishtest/http/api.py` → `server/fishtest/api.py`
  - source move: `server/fishtest/http/views.py` → `server/fishtest/views.py`
  - remove old legacy Pyramid modules currently at `server/fishtest/api.py` and `server/fishtest/views.py` as part of this replacement (no dual copies).
  - files touched by this move (code/tests/tools/docs):
    - runtime wiring/imports:
      - `server/fishtest/app.py`
      - `server/fishtest/http/errors.py`
      - `server/fishtest/http/middleware.py`
    - tests/helpers importing old module paths:
      - `server/tests/fastapi_util.py`
      - `server/tests/test_http_actions_view.py`
      - `server/tests/test_http_api.py`
      - `server/tests/test_http_boundary.py`
      - `server/tests/test_http_helpers.py`
      - `server/tests/test_http_users.py`
    - parity tooling (if still present):
      - `WIP/tools/parity_check_api_ast.py`
      - `WIP/tools/parity_check_api_routes.py`
      - `WIP/tools/parity_check_urls_dict.py`
    - authoritative docs:
      - `WIP/docs/1-FASTAPI-REFACTOR.md`
      - `WIP/docs/2-ARCHITECTURE.md`
- Delete `server/fishtest/models.py` (Pyramid ACL/authorization; unused by FastAPI runtime).
- Remove any remaining runtime imports from `pyramid.*` across `server/fishtest/**`.

### Task 3 — Remove Mako from test dependencies and delete legacy templates

- Drop Mako from packaging metadata (`server/pyproject.toml`):
  - remove `mako>=...` from `[dependency-groups] test`
- Delete legacy Mako template tree at `server/fishtest/templates/`.
- Remove/replace any scripts, tests, or docs that still assume Mako template parity.
- Delete parity tooling in `WIP/tools/` that depends on Mako templates (e.g., `compare_template_parity.py`, `compare_jinja_mako_parity.py`, `template_context_coverage.py` Mako paths).

### Task 4 — Update authoritative documentation for the finished project

Update `1-FASTAPI-REFACTOR.md` and `2-ARCHITECTURE.md` to describe the **final state**, not the migration process:

- `1-FASTAPI-REFACTOR.md`: retire migration-oriented framing (phases, parity gates, "mechanical port" language); rewrite as the authoritative server architecture and protocol contract reference. Remove references to Pyramid as a "behavioral spec" — the FastAPI implementation is now the spec.
- `2-ARCHITECTURE.md`: update module map to reflect post-move file locations (`api.py` and `views.py` at top level, no `http/api.py` or `http/views.py`). Remove "what changed from Pyramid" framing — describe what exists now. Remove references to legacy twins, parity scripts, and `tests/pyramid/` stubs.
- `0-INDEX.md`: update navigation to reflect final doc set.
- Remove or archive migration-only docs that have no value post-merge (iteration docs, reports, reference guides for porting decisions).
- Keep docs that remain useful post-merge (deployment notes, rebase process adapted for ongoing maintenance).

### Task 5 — Final verification

Run the final quality gates after Tasks 1–4 and record metrics.

Verification gates:
- Run full local test suite: `bash WIP/tools/run_local_tests.sh`.
- Run lint: `bash WIP/tools/lint_http.sh`.
- Verify no remaining `pyramid`/`mako` imports in Python sources:
  - `grep -rn 'from pyramid\|import pyramid\|from mako\|import mako' server/ --include='*.py' | grep -v __pycache__`
- Verify `server/pyproject.toml` has no Pyramid or Mako references.
- Verify deployment entrypoint import: `uv run python -c 'import fishtest.app'`.
- Record final metrics in [3.13-ITERATION.md](3.13-ITERATION.md).

### Task 6 — Create permanent project documentation

Create `server/fishtest/docs/` with contributor-facing technical documentation for the finished server. These docs describe the system as it is — not the migration from Pyramid. New contributors read these to understand how the server works and how to add features.

Documents to create:
- `README.md` — documentation index and quick-start guide.
- `architecture.md` — server structure, module map, request flow, startup, middleware stack, primary instance concept, signals, core domain adapters, validation.
- `threading-model.md` — async/sync boundaries, event loop vs threadpool inventory, rules for adding new code.
- `api-reference.md` — worker API protocol invariants, endpoint catalog, authentication, error shape, validation, how to add a new endpoint.
- `ui-reference.md` — route registration, `_dispatch_view()` pipeline, session handling, CSRF, authentication, URL generation, how to add a new UI route.
- `templates.md` — Jinja2 environment configuration, rendering flow, shared base context, template catalog, context contracts per template, authoring rules, how to add a new template.
- `deployment.md` — prerequisites, installation, systemd/nginx configuration, environment variables, primary/secondary model, signals, update procedure, session cookie notes.

Source material: refactored from `WIP/docs/2-ARCHITECTURE.md`, `WIP/docs/2.1-ASYNC-INVENTORY.md`, `WIP/docs/2.3-JINJA2.md`, `WIP/docs/11.1-JINJA2-CONTEXT-CONTRACTS.md`, `WIP/docs/4-VPS.md`, `WIP/docs/1-FASTAPI-REFACTOR.md` Protocol A/B, `WIP/docs/6-FASTAPI-REFERENCES.md`, `WIP/docs/7-STARLETTE-REFERENCES.md`.

### Task 7 — Write the conventional commit for the upstream PR

Write the conventional commit message for squashing the entire branch into a single commit. The message must:

- Use the `feat(server):` prefix.
- Have a subject line ≤ 72 characters.
- Have a body wrapped at 80 characters.
- Summarize the architectural change (Pyramid/Mako → FastAPI/Jinja2).
- List key outcomes (runtime stack, template engine, session handling, middleware, test suite).
- Note breaking changes (session cookie format, deployment command).

Prepared commit message:

```
feat(server): replace Pyramid/Mako with FastAPI/Jinja2

Replace the Pyramid WSGI framework and Mako template engine with
FastAPI/Starlette (ASGI) and Jinja2. Deploy via Uvicorn instead
of Waitress.

Runtime stack:
- FastAPI + Starlette + Uvicorn (ASGI).
- Jinja2 templates (.html.j2, StrictUndefined).
- itsdangerous TimestampSigner cookie sessions.
- Pure ASGI middleware stack (5 layers: shutdown guard, request
  state, worker routing, blocked-user redirect, session).
- vtjson validation layer (17 schemas).

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
  python-multipart, httpx (test).
- Removed: pyramid, pyramid-debugtoolbar, pyramid-mako, waitress,
  setuptools, mako.

Documentation:
- 10 docs in docs/ (architecture, threading model, API reference,
  UI reference, templates, worker, development guide, deployment
  with systemd + nginx configs, references).

Test suite:
- 161 tests (unittest discover) covering worker API, UI flows,
  HTTP boundary, middleware, session semantics, domain layer.
- Test helpers consolidated in test_support.py.

Breaking changes:
- Deployment entrypoint: `uvicorn fishtest.app:app` (was
  `pserve development.ini`).
- Session cookies invalidated on first deploy (itsdangerous
  TimestampSigner format); users re-authenticate once.
- Python >= 3.14 required (server); >= 3.8 (worker).
```

### Task 8 — OPTIONAL: delete the `WIP/` folder

> [!IMPORTANT]
> This task is performed **only in the PR branch** that opens the upstream pull request, not in the development branch.

- Migrate any still-needed tools/docs out of `WIP/` into permanent repo locations before deletion.
- Update references currently pointing to `WIP/tools/*` and `WIP/docs/*` to their non-WIP destinations.
- Delete `WIP/` tree.
- Verify no remaining references to `WIP/` paths in code, tests, or docs.

Definition of done:

- Pyramid packages/configs are removed from the runtime deployable.
- Mako packages/templates are removed from the active repository/runtime path.
- Tests no longer depend on Pyramid stubs for coverage of the active server behavior.
- Authoritative docs describe the final FastAPI/Jinja2 architecture (not the migration process).
- Permanent project documentation exists in `server/fishtest/docs/` (7 files: README + 6 docs).
- A conventional commit message is written and ready for the squashed branch commit.
- Docs/config/scripts no longer depend on `WIP/` paths (if Task 8 is executed).
- All contract tests pass.

## What does NOT belong here

This file should not be the place where we restate detailed behavior or code pointers.

If you want to document any of the following, put it in the correct source of truth instead:

- Migration strategy, phases, acceptance criteria, tooling rules → [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md)
- Runtime architecture, module map, invariants, operational constraints → [2-ARCHITECTURE.md](2-ARCHITECTURE.md)

This current file stays small on purpose.
