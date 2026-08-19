# Contributing to Fishtest

This guide covers the development workflow and coding standards for the
project. For the architecture and reference documentation, start at
[docs/0-README.md](docs/0-README.md).

## Getting Started

### Prerequisites

| Component | Minimum Version  | Purpose          |
|-----------|------------------|------------------|
| Python    | >= 3.14          | Server runtime   |
| Python    | >= 3.8           | Worker runtime   |
| [MongoDB](https://www.mongodb.com/docs/manual/administration/install-community/) | `mongod` service | Data store       |
| [uv](https://docs.astral.sh/uv/) | latest | Package manager |

### Local Setup

```bash
# Clone the repository
git clone https://github.com/official-stockfish/fishtest.git
cd fishtest

# Install development tools (pre-commit, ruff, ty)
uv sync

# Install server dependencies
(cd server && uv sync --group test)

# Install pre-commit hooks
uv run pre-commit install
```

The repository holds three independent uv projects: the root (development
tools only), `server/`, and `worker/`. Each has its own `pyproject.toml` and
`uv.lock`. Run `uv` commands from the directory of the project you are
changing, and commit both files when a dependency changes.

### Running the Development Server

```bash
cd server
FISHTEST_INSECURE_DEV=1 uv run uvicorn fishtest.app:app --reload --port 8000
```

Setting `FISHTEST_INSECURE_DEV=1` enables an insecure fallback secret key
for cookie signing. This must **never** be used in production. For the full
environment variable list, see [docs/8-deployment.md](docs/8-deployment.md).

### Running Tests

The server test suite requires a running MongoDB instance.

```bash
mkdir -p .local/mongo-data
mongod --dbpath .local/mongo-data --fork --logpath .local/mongod.log
(cd server && uv run python -m unittest discover -s tests -v)
mongod --shutdown --dbpath .local/mongo-data
```

The worker test suite does not need MongoDB. It imports the vendored
dependencies in `worker/packages`, so put that directory on `PYTHONPATH`, as CI
does.

```bash
(cd worker && PYTHONPATH=packages python -m unittest discover -s tests -v)
```

## Coding Style

### Python

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Format and lint with [Ruff](https://docs.astral.sh/ruff/), using the shared
  configuration in the root `pyproject.toml`:

```bash
uv run ruff format server worker --config pyproject.toml
uv run ruff check server worker --select I --fix --config pyproject.toml
uv run ruff check server worker --config pyproject.toml
```

Ruff targets Python 3.14 for the server and Python 3.8 for `worker/**/*.py`.
Do not use syntax newer than 3.8 in worker code.

### CSS, HTML, JavaScript

- Follow the [Google HTML/CSS Style Guide](https://google.github.io/styleguide/htmlcssguide.html)
  and the [Google JavaScript Style Guide](https://google.github.io/styleguide/jsguide.html).
- Format with [Prettier](https://prettier.io/), using the same glob CI checks:

```bash
npx prettier --write 'server/fishtest/static/{css/*.css,html/*.html,js/*.js}'
```

### Pre-commit Hooks

The repository uses [pre-commit](https://pre-commit.com/) hooks to automate
formatting and linting on every commit. The hooks are configured in
`.pre-commit-config.yaml` and include:

- Large-file check
- TOML and YAML validation
- End-of-file fixer and trailing-whitespace fixer
- Ruff lint (with `--fix`) and Ruff format
- `uv.lock` sync check

Run hooks manually on all files:

```bash
uv run pre-commit run --all-files
```

To temporarily skip hooks during a commit:

```bash
git commit --no-verify -m "message"
```

## Submitting Changes

1. **Open an issue first** - describe what you plan to change and wait for
   feedback from a maintainer before writing code.
2. **Fork the repository** and create a feature branch from `master`.
3. **Keep PRs small and focused** - one logical change per pull request.
4. **Run the full pre-commit suite** before pushing.
5. **Write tests** for new server functionality (the project uses `unittest`,
   with `fastapi.testclient.TestClient` -- backed by the pinned `httpx2`
   package -- for HTTP tests).
6. **Write a clear PR description** linking to the related issue.
7. **Respond to review feedback** promptly.

Continuous integration runs four workflows on every push and pull request:
lint, server tests, worker tests on POSIX, and worker tests on MSYS2. See
[docs/0-README.md](docs/0-README.md) for what each one covers.

## Project Structure

```
fishtest/
|-- server/
|   |-- fishtest/            -- Server application
|   |   |-- app.py           -- FastAPI application factory and lifespan
|   |   |-- views.py         -- UI routes and view dispatch
|   |   |-- api.py           -- Worker and read-only API endpoints
|   |   |-- rundb.py         -- Run lifecycle and task distribution
|   |   |-- http/            -- Middleware, session, CSRF, Jinja2 wiring
|   |   |-- templates/       -- Jinja2 templates (.html.j2)
|   |   `-- static/          -- CSS, JS, images
|   |-- tests/               -- Server test suite
|   `-- utils/               -- Operational scripts
|-- worker/                  -- Distributed worker client
|-- docs/                    -- Architecture and reference documentation
`-- pyproject.toml           -- Root project config (dev tools, shared ruff config)
```

## Additional Resources

- [Documentation index](docs/0-README.md) - what each document answers and
  where to look for a subsystem.
- [Architecture Overview](docs/1-architecture.md) - module map, request call
  chains, startup and shutdown, scheduler, caches, locking.
- [Threading Model](docs/2-threading-model.md) - async and sync boundaries and
  the rules for new code.
- [Development Guide](docs/7-development.md) - environment variables, nginx
  config, and multi-instance testing.
- [API Reference](docs/3-api-reference.md) - worker API endpoints.
- [Wiki Contributing Page](https://github.com/official-stockfish/fishtest/wiki/Contributing-to-Fishtest) - development environment setup, coding styles, development workflow.
- [Coding Style Guide (Issue #634)](https://github.com/official-stockfish/fishtest/issues/634)
  - original style discussion.
