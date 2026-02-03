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

## Milestone 8 — Templates: parallel new Mako + Jinja2 (with shared helpers)

Goal: modernize template rendering while preserving UI behavior by running two parallel tracks:
- a cleaned-up Mako template set in `server/fishtest/templates_mako/`
- a Jinja2 template set in `server/fishtest/templates_jinja2/`

This milestone focuses on parity, comparability, and a common helper base. It does not require an immediate full cutover to Jinja2.

Core objectives:
- Clean up Mako templates (reduce embedded Python, clearer structure).
- Build and use a shared helper base for both renderers.
- Compare outputs between legacy Mako, new Mako, and Jinja2.
- Adopt 2026 best practices for Mako and Jinja2 template structure.

Hard constraint (non-negotiable):
- MUST keep legacy Mako templates in [server/fishtest/templates](server/fishtest/templates) for upstream rebase safety. Do not delete, rename, or “clean up” legacy templates.

Suggested approach (incremental, parallel):

1. Add shared helper base
  - Create a single helper module for filters/globals used by both renderers.
  - Keep URL generation and cache-busting semantics compatible.
2. Introduce the new Mako renderer
  - Add `server/fishtest/templates_mako/` and a new Mako renderer.
  - Keep legacy Mako templates intact for parity and rebase safety.
3. Introduce Jinja2 alongside Mako
  - Add Jinja2 environment for `server/fishtest/templates_jinja2/`.
  - Keep dual-renderer support and per-template selection.
4. Port templates page-by-page (two tracks)
  - Convert to new Mako and Jinja2 in lockstep, starting with safer pages.
  - Keep diffs minimal and reversible.
5. Compare and measure
  - Add parity tools to compare rendered HTML.
  - Measure performance and complexity across implementations.

Status:

- Complete (2026-02-05); see [3.8-ITERATION.md](3.8-ITERATION.md).
- Parity OK for legacy vs new Mako and legacy vs Jinja2; shared helper base in place.
- Renderer selection is driven by a Python variable in the renderer module (not environment variables).
- Metrics snapshot and scripts are tracked in [8-TEMPLATE-METRICS.md](8-TEMPLATE-METRICS.md).

Definition of done:

- New Mako templates render with parity to legacy Mako.
- Jinja2 templates render with parity to legacy Mako.
- A shared helper base is used by both renderers.
- Parity comparison scripts exist for both tracks.

## Milestone N-1 — Optional: Pydantic (only when it buys real safety)

Goal: allow Pydantic only where it materially reduces bugs/duplication, without duplicating vtjson validation across the whole codebase or changing externally-visible error semantics.

Scope guidance:

- Prefer vtjson as the protocol-validation source of truth unless we deliberately migrate a specific surface.
- If Pydantic is used for request parsing, ensure validation failures are routed through existing error shaping (avoid leaking FastAPI default `422` behavior on worker/UI paths).
- Any Pydantic introduction must be paired with contract tests that lock response shape + error strings + worker `duration` behavior.

Non-goals:

- No broad conversion of existing endpoints “for style”.
- No replacement of vtjson across the codebase.

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
