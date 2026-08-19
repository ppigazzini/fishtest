# Fishtest Server Documentation

This is the entry point to the fishtest documentation set. It says what each
document answers, where to look for a given subsystem, and how to get a
development server running.

## Overview

Fishtest is a distributed chess engine testing system. The server assigns
testing tasks to volunteer workers, collects game results, and computes
statistical analyses (SPRT, ELO) to determine whether code changes improve
Stockfish. The web interface provides dashboards for managing test runs,
viewing results, and administering users and workers.

## Documents

| # | Document | Audience | Answers |
|---|---|---|---|
| 1 | [1-architecture.md](1-architecture.md) | All contributors | What each module owns, request call chains, startup/shutdown, scheduler, caches, locking |
| 2 | [2-threading-model.md](2-threading-model.md) | Backend contributors | Async/sync boundaries, threadpool usage, task-scheduling throttle, rules for new code |
| 3 | [3-api-reference.md](3-api-reference.md) | Worker and integration developers | Worker API endpoints, protocol invariants, error shapes |
| 4 | [4-ui-reference.md](4-ui-reference.md) | UI contributors | UI routes, view dispatch pipeline, htmx fragment dispatch, session/CSRF/auth, query parameters |
| 5 | [5-templates.md](5-templates.md) | UI contributors | Jinja2 environment, template catalog (page + fragment), context contracts |
| 6 | [6-worker.md](6-worker.md) | Worker contributors | Worker architecture, task lifecycle, API usage |
| 7 | [7-development.md](7-development.md) | All developers | Dev setup, environment variables, validation workflows, vtjson rules, OpenAPI |
| 8 | [8-deployment.md](8-deployment.md) | Operators | systemd, nginx, kernel tuning, capacity audit |
| 9 | [9-references.md](9-references.md) | All developers | FastAPI, Starlette, Jinja2, htmx, Python, MongoDB, and tooling references |

## Where to look for X

| Question | Start here | Source of truth |
|---|---|---|
| Which module owns this behavior? | [1-architecture.md](1-architecture.md) module map | `server/fishtest/` |
| How does a UI request reach a template? | [1-architecture.md](1-architecture.md) call chains | `server/fishtest/views.py` -> `_dispatch_view()` |
| Which URL maps to which handler? | [4-ui-reference.md](4-ui-reference.md) | `server/fishtest/views.py` -> `_VIEW_ROUTES` |
| What does a worker endpoint accept and return? | [3-api-reference.md](3-api-reference.md) | `server/fishtest/api.py` -> `WorkerApi` |
| What variables does a template receive? | [5-templates.md](5-templates.md) | `server/fishtest/templates/` |
| Where is a Jinja filter or global defined? | [5-templates.md](5-templates.md) | `server/fishtest/http/jinja.py`, `http/template_helpers.py` |
| Can I block the event loop here? | [2-threading-model.md](2-threading-model.md) | `server/fishtest/app.py`, `views.py`, `api.py` |
| What runs periodically, and how often? | [1-architecture.md](1-architecture.md) background scheduler | `server/fishtest/rundb.py` -> `RunDb.schedule_tasks()` |
| Why is this value stale? | [1-architecture.md](1-architecture.md) caches | `server/fishtest/run_cache.py`, `lru_cache.py` |
| Which lock protects this state? | [1-architecture.md](1-architecture.md) concurrency and locking | `server/fishtest/rundb.py`, `run_cache.py` |
| What is a tunable limit or page size? | [8-deployment.md](8-deployment.md) | `server/fishtest/http/settings.py` |
| What shape must a document have? | [7-development.md](7-development.md) vtjson rules | `server/fishtest/schemas.py` |
| What does the worker do on the client side? | [6-worker.md](6-worker.md) | `worker/worker.py`, `worker/games.py` |
| How is the production cluster laid out? | [8-deployment.md](8-deployment.md) | `.github/workflows/`, systemd and nginx samples in that page |

## Quick start

```bash
# Install server dependencies (from repo root)
(cd server && uv sync --group test)

# Start the development server (from server/)
(cd server && FISHTEST_INSECURE_DEV=1 uv run uvicorn fishtest.app:app --reload --port 8000)
```

For OpenAPI docs, worker setup, environment variables, local validation
workflows, and vtjson validation rules, see
[7-development.md](7-development.md).

## Technology stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Starlette (ASGI) |
| Application server | Uvicorn |
| Templates | Jinja2 (`.html.j2`, `StrictUndefined`, `select_autoescape`) |
| Client interactivity | htmx 2.0.10 (CDN, fragment polling/swaps, OOB updates) |
| Client styling | Bootstrap and Font Awesome from CDN; see `server/fishtest/templates/base.html.j2` |
| Session management | itsdangerous `TimestampSigner` cookie sessions |
| Database | MongoDB (pymongo, synchronous driver) |
| Validation | vtjson (schema-first validation; no Pydantic) |
| Statistics | scipy, numpy (SPRT, ELO calculations) |
| Python (server) | >= 3.14 |
| Python (worker) | >= 3.8 |

There is no JavaScript build step. Static assets in
`server/fishtest/static/js/` are served as written.

## Project layout

```
fishtest/
|-- pyproject.toml             -- Root: dev tools (ruff, ty, pre-commit)
|-- uv.lock                    -- Locked dependency set for the root dev tools
|-- .pre-commit-config.yaml    -- Pre-commit hooks (pre-commit-hooks, ruff, uv-lock)
|-- .github/workflows/         -- CI: lint, server tests, worker tests (POSIX + MSYS2)
|-- CONTRIBUTING.md            -- Contribution workflow and coding style
|-- docs/                      -- Architecture and reference documentation
|-- server/
|   |-- pyproject.toml         -- Server package: runtime + test dependencies
|   |-- uv.lock                -- Locked server dependency set
|   |-- fishtest/              -- FastAPI application (Python >= 3.14)
|   |-- tests/                 -- Focused unit and HTTP contract tests
|   `-- utils/                 -- Operational utilities (backup, indexes, migration, analysis)
`-- worker/
    |-- pyproject.toml         -- Worker package: runtime dependencies (Python >= 3.8)
    |-- uv.lock                -- Locked worker dependency set
    |-- worker.py              -- Main worker script
    |-- games.py               -- Engine compilation, game execution
    |-- updater.py             -- Self-update mechanism
    |-- sri.txt                -- Subresource integrity hashes for worker files
    |-- tests/                 -- Worker test suite
    `-- packages/              -- Vendored packages (requests, urllib3, openlock, ...)
```

### Why three `pyproject.toml` files

The root `pyproject.toml` defines **development-only tools** (ruff, ty,
pre-commit) shared across the repo, plus the shared ruff configuration that CI
passes with `--config pyproject.toml`. The server and worker each have their own
`pyproject.toml` with independent dependency sets and Python version
constraints: the server requires Python >= 3.14 while the worker supports
Python >= 3.8 to run on contributor machines with older distributions.

### Build system

Both the server and worker use **hatchling** as the build backend. Hatchling
is a lightweight, standards-compliant PEP 517 build system with no runtime
dependencies. The root project is not packaged.

### Dependency management with uv

[uv](https://docs.astral.sh/uv/) is the package manager. Each of the three
projects has its own lock file: `uv.lock` (dev tools), `server/uv.lock`, and
`worker/uv.lock`. Run `uv` commands from the directory of the project you are
changing.

```bash
# Install server dependencies
cd server && uv sync

# Add a dependency
uv add <package>               # runtime dependency
uv add --group test <package>  # test-only dependency

# Remove a dependency
uv remove <package>

# Update a dependency
uv lock --upgrade-package <package>

# Regenerate the lock file from scratch
uv lock
```

After any dependency change, commit both the modified `pyproject.toml` and
the updated `uv.lock` from that project.

### Pre-commit hooks

`.pre-commit-config.yaml` runs on every commit:

- **pre-commit-hooks** -- large-file check, TOML and YAML validation,
  end-of-file fixer, trailing-whitespace fixer
- **ruff** -- `ruff-check` (with `--fix`) and `ruff-format`
- **uv-lock** -- verify the root `uv.lock` is up to date

Install with: `uv run pre-commit install`

### CI workflows

| Workflow | File | Trigger | What it does |
|----------|------|---------|--------------|
| Lint | `lint.yaml` | push, PR, manual | ruff check, ruff import sort, ruff format check, and Prettier check on `server/fishtest/static` |
| Server | `server.yaml` | push, PR, manual | `unittest discover -s tests` in `server/` against a MongoDB service container |
| Worker POSIX | `worker_posix.yaml` | push, PR, manual | Worker tests on Linux and macOS across Python 3.8 through 3.14 |
| Worker MSYS2 | `worker_msys2.yaml` | push, PR, manual | Worker tests on Windows across mingw64, ucrt64, clang64, and clangarm64 |

The lint workflow reports each check separately and fails at the end if any of
them failed, so a red run lists every problem in one pass.
