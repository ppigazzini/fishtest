> [!IMPORTANT]
> **Disclaimer (high-level roadmap):** This file is a milestone map, *not* the authoritative plan and *not* the architecture snapshot.
>
> - Authoritative plan: [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md)
> - Current repo snapshot: [2-ARCHITECTURE.md](2-ARCHITECTURE.md)

# Pyramid → FastAPI roadmap (milestones)

Date: 2026-01-14

This document describes *how we switch* from Pyramid (WSGI) to FastAPI/Starlette (ASGI), and (optionally) from Mako to Jinja2.
It stays intentionally high-level to avoid duplicating (and drifting from) the details in:

- the plan ([1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md))
- the current architecture snapshot ([2-ARCHITECTURE.md](2-ARCHITECTURE.md))

If you need details (exact behaviors, invariants, operational constraints, code pointers), prefer those two docs.

## Milestone 0 — Where we are now

FastAPI/Starlette is the active serving stack in this repo, and a “glue” layer exists to preserve Pyramid-era behavior.
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


## Milestone 4 — Make the FastAPI/Starlette layer idiomatic (incrementally)

Goal: reduce glue where it only exists due to “we haven’t refactored yet”, while keeping parity for behaviors that are relied on.

Examples of what “idiomatic” means (choose deliberately, don’t churn):

- Clear middleware stack (one responsibility each).
- Explicit dependency injection where it simplifies lifecycle and testing.
- Centralized exception handlers instead of ad-hoc per-route shaping.
- Typed request models where they reduce bugs (not as a style exercise).

Scope guidance (incremental, safe-by-default):

- Prefer changes that shrink glue without changing externally-visible behavior.
- Keep upstream behavior parity tests as the safety net; do not skip the contract gate.
- Favor small, reversible refactors over broad rewrites.

Candidate areas (examples, not a mandate):

- Move one-off request parsing into dependencies when it reduces duplication.
- Consolidate error shaping that is currently spread across helpers.
- Introduce Pydantic models only for new endpoints or when they remove known bugs.

Non-goals for this milestone:

- No redesign of DB adapters or scheduling semantics.
- No template engine switch (that is Milestone 5).

## Milestone 5 — Templates: Mako → Starlette Jinja2 (optional but recommended long-term)

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
