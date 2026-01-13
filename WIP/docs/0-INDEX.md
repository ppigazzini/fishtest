# WIP docs index (entry point)

Date: 2026-01-22

This directory contains *work-in-progress* docs for the Pyramid → FastAPI migration and the current FastAPI server architecture.

## What to read (recommended order)

1. **Authoritative plan (source of truth):**
   - [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md)

2. **Current repo snapshot (what exists today):**
   - [2-ARCHITECTURE.md](2-ARCHITECTURE.md)

3. **Async/blocking boundaries (runtime invariants):**
   - [2.1-ASYNC-INVENTORY.md](2.1-ASYNC-INVENTORY.md)

4. **High-level roadmap (milestones only):**
   - [3-MILESTONES.md](3-MILESTONES.md)

5. **How to iterate safely (day-to-day loop):**
   - Rules of engagement: [3.0-ITERATION-RULES.md](3.0-ITERATION-RULES.md)
   - Completed iteration records: [3.1-ITERATION.md](3.1-ITERATION.md), [3.2-ITERATION.md](3.2-ITERATION.md), [3.3-ITERATION.md](3.3-ITERATION.md)
   - Current iteration plan (Milestone 4): [3.4-ITERATION.md](3.4-ITERATION.md)

6. **Deployment notes (systemd + nginx examples):**
   - [4-VPS.md](4-VPS.md)

7. **Rebase process + parity tooling:**
   - [5-REBASE.md](5-REBASE.md)

## Sources of truth (don’t mix these up)

- **Behavioral spec:** upstream Pyramid behavior (and the Pyramid-era spec modules kept in-tree for tests).
- **Authoritative migration plan:** [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md).
- **Implementation snapshot:** [2-ARCHITECTURE.md](2-ARCHITECTURE.md) (describes current code, not “the plan”).

## Key invariants / contracts (keep stable unless explicitly changing protocol)

### Worker API contract (`/api/*`)

- Responses are JSON objects and include `duration` (float) on success *and* errors.
- Application-level failures commonly return HTTP 200 with `{ "error": "...", "duration": ... }`.
- Validation/transport failures return non-200 with worker-compatible JSON error strings.
- Error strings are part of the protocol in practice (workers sometimes key off exact wording).

### UI contract (browser-visible behavior)

- UI routes render HTML (Mako) and must **not** start returning JSON errors for browser pages.
- 403/404 behavior is template-rendered HTML (and cookie session semantics are preserved).
- Login/logout, CSRF enforcement, flashes, and redirects must remain compatible.

### Sync/async contract (ASGI reality)

- The FastAPI/Starlette event loop is treated as a thin HTTP wrapper.
- Blocking work (MongoDB, file I/O, CPU-heavy rendering, `requests`, etc.) must run off the event loop (threadpool).

### Operational constraints

- “Primary instance” behavior exists (scheduler/background jobs and certain mutations).
- Primary must run as a **single Uvicorn worker process** (multi-worker on primary is rejected).
- In multi-instance deployments, reverse proxy routing may be required for safety/parity.

## Where changes should go (hotspots + parity tooling)

See [3.0-ITERATION-RULES.md](3.0-ITERATION-RULES.md) for mechanical-port hotspot rules and two-step landing strategy.

See [5-REBASE.md](5-REBASE.md) for rebase process and parity tooling under `WIP/tools/`.
