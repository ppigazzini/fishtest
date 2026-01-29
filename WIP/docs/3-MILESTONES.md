> [!IMPORTANT]
> **Disclaimer (high-level roadmap):** This file is a milestone map, *not* the authoritative plan and *not* the architecture snapshot.
>
> - Authoritative plan: [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md)
> - Current repo snapshot: [2-ARCHITECTURE.md](2-ARCHITECTURE.md)

# Pyramid → FastAPI roadmap (milestones)

Date: 2026-01-29

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

## Milestone 6 — Contract tests & de‑Pyramidized tests

Goal: make the active FastAPI/Starlette behavior the single source of truth for tests and remove the test‑time Pyramid stubs.

Definition of done:
- All protocol contract tests (Protocol A worker API + Protocol B UI flows) run against the FastAPI app using `TestClient` or direct callable tests.
- Legacy Pyramid‑importing unit tests are either ported to FastAPI tests or removed.
- Test-only Pyramid stubs under `server/tests/pyramid/` are deleted after their consumers are migrated.
- CI passes with no Pyramid stubs present.

Verification gates:
- Full contract test suite for worker endpoints (status codes, `duration`, error shape).
- UI contract tests: login/logout, CSRF, 403/404 HTML, representative list/detail pages.
- Parity scripts updated to point at `server/fishtest/http/*`.

Metrics:
- Zero imports from `pyramid.*` in test modules.
- Contract coverage: all worker endpoints + representative UI flows pass in CI.

Reference implementations (from the bloat branch, to be adapted to `http/`):
- [__fishtest-bloat/server/tests/test_web_api_worker.py](__fishtest-bloat/server/tests/test_web_api_worker.py)
- [__fishtest-bloat/server/tests/test_web_middleware.py](__fishtest-bloat/server/tests/test_web_middleware.py)
- [__fishtest-bloat/server/tests/test_web_session.py](__fishtest-bloat/server/tests/test_web_session.py)
- [__fishtest-bloat/server/tests/test_web_ui_actions.py](__fishtest-bloat/server/tests/test_web_ui_actions.py)

## Milestone 7 — Starlette idiomatic plumbing (reduce shim surface, avoid bloat)

Goal: make the HTTP layer idiomatic Starlette/FastAPI while preserving externally‑visible behavior; reduce shim usage without recreating the helper bloat seen in earlier drafts.

Scope and constraints:
- Preserve protocol parity at all times (tests + parity tools gate changes).
- Incremental: replace shims only when tests cover the affected surfaces.
- Keep `http/api.py` and `http/views.py` readable; avoid route‑layer fragmentation.

Key outcomes:
- Replace request‑shim call patterns with typed dependencies (`Annotated` aliases) for DB handles, session, and UI context.
- Adopt Starlette/FastAPI session middleware and migrate template access via a thin, well‑tested compatibility layer during transition.
- Remove remaining `TemplateRequest` surface only after templates are ported or an adapter is proven identical.

Verification gates:
- Contract tests (Milestone 6) remain green after each refactor.
- Parity scripts continue to report OK or expected whitelists.

Notes:
- Avoid helper explosion and multi‑hop flow; the route layer should remain readable in a single pass.

Metrics:
- Hop count ≤ 1 per endpoint; helper calls ≤ 2 per endpoint in `http/api.py` and `http/views.py`.
- No new route‑split folder tree; HTTP entrypoints remain the primary narrative.

## Milestone N-2 — Optional: Pydantic (only when it buys real safety)

Goal: allow Pydantic only where it materially reduces bugs/duplication, without duplicating vtjson validation across the whole codebase or changing externally-visible error semantics.

Scope guidance:

- Prefer vtjson as the protocol-validation source of truth unless we deliberately migrate a specific surface.
- If Pydantic is used for request parsing, ensure validation failures are routed through existing error shaping (avoid leaking FastAPI default `422` behavior on worker/UI paths).
- Any Pydantic introduction must be paired with contract tests that lock response shape + error strings + worker `duration` behavior.

Non-goals:

- No broad conversion of existing endpoints “for style”.
- No replacement of vtjson across the codebase.

## Milestone N-1 — Templates: Mako → Starlette Jinja2 (optional but recommended long-term)

Goal: remove the Mako/Pyramid-template compatibility layer and use the standard Starlette template integration.
This is an architectural cleanup; it should happen only when Milestone 2 parity gates are solid.

Suggested approach (incremental):

1. Introduce Jinja2 alongside Mako
  - Add a Jinja2 template environment for a new template directory (e.g. `server/fishtest/templates_jinja2/`).
  - Keep Mako templates working so the UI doesn’t need a flag-day conversion.
2. Build compatibility helpers in Jinja2
  - Recreate the handful of global functions/filters the templates rely on (e.g., `static_url` equivalent, formatting helpers).
  - Keep URL generation and cache-busting semantics compatible until we decide to change them explicitly.
3. Port templates page-by-page
  - Start with low-risk pages (read-only pages like `/rate_limits`, `/contributors`).
  - Then auth pages (`/login`) once the cookie + CSRF behavior is stable.
  - Leave complex pages (run/task views) for last.
4. Switch the renderer per-route
  - Each UI route chooses Mako or Jinja2 until everything is ported.
5. Delete Mako glue
  - Remove the Mako environment, request wrapper, and any template shims once all routes render via Jinja2.

Definition of done:

- No Mako runtime dependency in the server.
- All UI templates render via Starlette’s Jinja2 integration.
- Template test coverage exists for at least: login page, one list page, one “detail-ish” page.

## Milestone N — Delete legacy Pyramid code

Goal: the repo no longer carries Pyramid-specific runtime codepaths.

Definition of done:

- Pyramid packages/configs are removed from the runtime deployable.
- Tests no longer depend on Pyramid stubs for coverage of the active server behavior.
- Docs point to FastAPI/Starlette as the only server.

## What does NOT belong here

This file should not be the place where we restate detailed behavior or code pointers.

If you want to document any of the following, put it in the correct source of truth instead:

- Migration strategy, phases, acceptance criteria, tooling rules → [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md)
- Runtime architecture, module map, invariants, operational constraints → [2-ARCHITECTURE.md](2-ARCHITECTURE.md)

This current file stays small on purpose.
