# Development Guide

This page covers local setup for the fishtest server and worker, the lint,
format and test commands the project gates on, the environment variables that
apply in development, the vtjson rules for server-side validation, and an
optional nginx config for multi-instance local testing. For production
deployment see [8-deployment.md](8-deployment.md). For library references see
[9-references.md](9-references.md).

## Prerequisites

| Component | Minimum version | Purpose |
|-----------|-----------------|---------|
| Python | >= 3.14 | Server runtime (`server/pyproject.toml`) |
| Python | >= 3.8 | Worker runtime (`worker/pyproject.toml`) |
| MongoDB | `mongod` on `localhost` | Data store |
| uv | -- | Python package manager |
| Node.js with `npx` | -- | Prettier check for CSS, HTML and JS |

`RunDb.__init__` in `server/fishtest/rundb.py` connects with
`MongoClient("localhost")`. There is no connection-string setting, so `mongod`
must listen on the local default port. The application database is
`fishtest_new` (`server/fishtest/util.py` -> `FISHTEST`). The server test suite
uses a separate `fishtest_tests` database
(`server/tests/test_support.py` -> `get_rundb`).

nginx is not required for single-instance development. For multi-instance
local testing, see [nginx development config](#nginx-development-config).

## Installation

```bash
# repo root: dev tooling (ruff, ty, pre-commit)
uv sync

# server runtime + test dependencies into server/.venv
(cd server && uv sync --group test)

# git hooks
uv run pre-commit install
```

`uv sync --group test` installs the runtime dependencies declared in
`server/pyproject.toml` plus the `test` group (`httpx2`). This is the same
command CI runs (`.github/workflows/server.yaml`, step "Sync project").

The repo has three uv projects and three lock files. The root project is
virtual (`uv.lock` -> `source = { virtual = "." }`) and pins only the `dev`
group of the root `pyproject.toml` (`pre-commit`, `ruff`, `ty`). `server/` and
`worker/` are separate projects with their own `pyproject.toml` and `uv.lock`,
which is why the server and the worker can require different Python versions.

## Running the development server

```bash
cd server
FISHTEST_INSECURE_DEV=1 uv run uvicorn fishtest.app:app --reload --port 8000
```

No concurrency flags are needed in development. The async event loop handles
concurrent requests natively.

`FISHTEST_INSECURE_DEV=1` selects an insecure fallback signing secret in
`server/fishtest/http/cookie_session.py` -> `_secret_key`. Without it, and
without `FISHTEST_AUTHENTICATION_SECRET`, the first request raises
`MissingAuthenticationSecretError`. Never set `FISHTEST_INSECURE_DEV` in
production.

With `FISHTEST_PORT` and `FISHTEST_PRIMARY_PORT` unset, the instance is primary
(`server/fishtest/http/settings.py` -> `AppSettings.from_env`). A primary
instance runs the scheduler, refreshes the official master SHA from GitHub, and
updates aggregated data during lifespan startup (`server/fishtest/app.py`).

### Creating a local user

The worker and every authenticated UI flow need a user that exists in the local
MongoDB, is not `pending` and is not `blocked`
(`server/fishtest/userdb.py` -> `UserDb.authenticate`). Signup through the UI
requires a reCAPTCHA secret, so create the user directly instead:

```bash
cd server
uv run python -c "
from fishtest.rundb import RunDb
rundb = RunDb()
rundb.userdb.create_user('devuser', 'devpass', 'devuser@example.com', '')
user = rundb.userdb.get_user('devuser')
user['pending'] = False
rundb.userdb.save_user(user)
rundb.conn.close()
"
```

To give the user approver rights, append `group:approvers` to `user['groups']`
before `save_user`. `server/tests/ui_user_test_case.py` uses exactly this
sequence and is the reference for other group and state fixtures.

### Running the worker against the development server

The worker validates its credentials against `/api/request_version` before it
starts (`worker/worker.py` -> `get_credentials`), and the server rejects
unknown, pending and blocked users.

After starting the server, run the worker in a second terminal:

```bash
cd worker
uv run worker.py devuser devpass --protocol http --host 127.0.0.1 --port 8000
```

Useful worker flags for development (`worker/worker.py` argument parser):

| Flag | Effect |
|------|--------|
| `-v`, `--no_validation` | Skip the client-side credential check; the server still authenticates every API call |
| `-w`, `--only_config` | Write `worker/fishtest.cfg`, refresh `sri.txt` hashes, then exit |
| `-c`, `--concurrency` | Expression over `MAX` giving the core count to use |
| `-f`, `--fleet` | Quit on error or when no task is available |

### OpenAPI documentation

Enable the interactive OpenAPI docs during development:

```bash
cd server
OPENAPI_URL=/openapi.json FISHTEST_INSECURE_DEV=1 uv run uvicorn fishtest.app:app --reload --port 8000
```

`AppSettings.openapi_url` is `None` unless `OPENAPI_URL` is set, and FastAPI
registers `/openapi.json`, `/docs` and `/redoc` only when `openapi_url` is
truthy. The schema covers both routers: `server/fishtest/api.py` registers its
routes under the `api` tag and `server/fishtest/views.py` under the `ui` tag.

## Environment variables

The exhaustive table, including production requirements, is in
[8-deployment.md](8-deployment.md). The variables below are the ones that
matter locally.

| Variable | Read in | Default | Effect in development |
|----------|---------|---------|-----------------------|
| `FISHTEST_INSECURE_DEV` | `http/cookie_session.py` -> `_secret_key` | unset | `1`, `true`, `yes` or `on` (case-insensitive) selects the insecure fallback signing secret |
| `FISHTEST_AUTHENTICATION_SECRET` | `http/cookie_session.py` -> `_secret_key` | unset | Real cookie signing secret; supersedes the insecure fallback |
| `OPENAPI_URL` | `http/settings.py` -> `AppSettings.from_env` | unset | Set to `/openapi.json` to register `/docs`, `/redoc` and `/openapi.json` |
| `FISHTEST_PORT` | `http/settings.py` -> `AppSettings.from_env` | `-1` | Port identity of this instance |
| `FISHTEST_PRIMARY_PORT` | `http/settings.py` -> `AppSettings.from_env` | `-1` | Primary port of the cluster |
| `FISHTEST_URL` | `rundb.py` -> `RunDb.__init__` | unset | Seeds `RunDb.base_url` for run links in log lines; when unset it starts as `http://127.0.0.1` and `AttachRequestStateMiddleware` overwrites it from the first request's `Host` header |
| `FISHTEST_JINJA_TEMPLATES_DIR` | `http/jinja.py` -> `templates_dir` | package `templates/` | Override the Jinja2 template search path |
| `FISHTEST_STATIC_DIR` | `http/settings.py` -> `default_static_dir` | package `static/` | Override the directory mounted at `/static` |
| `GH_TOKEN` | `github_api.py` -> `call` | unset | Sent as `Authorization: Bearer`; raises the GitHub API rate limit |

Paths in the table are relative to `server/fishtest/`.

When `FISHTEST_PORT` and `FISHTEST_PRIMARY_PORT` are both unset or negative,
the instance defaults to primary -- the expected mode for single-instance
development.

## Linting and formatting

Run every check from the repo root so the tracked dev tools and the shared
config are used.

```bash
uv run ruff check server worker --config pyproject.toml
uv run ruff check server worker --select I --config pyproject.toml
uv run ruff format server worker --check --config pyproject.toml
npx prettier --check "server/fishtest/static/{css/*.css,html/*.html,js/*.js}"
```

To apply fixes instead of reporting them:

```bash
uv run ruff check server worker --config pyproject.toml --fix
uv run ruff format server worker --config pyproject.toml
npx prettier --write "server/fishtest/static/{css/*.css,html/*.html,js/*.js}"
```

Ruff configuration lives in the root `pyproject.toml`:

| Setting | Value |
|---------|-------|
| `[tool.ruff] target-version` | `py314` |
| `[tool.ruff] src` | `server`, `worker` |
| `[tool.ruff] extend-exclude` | `worker/packages`, `*.md` |
| `[tool.ruff.per-file-target-version]` | `py38` for `worker/**/*.py` |
| `[tool.ruff.lint] select` | `E4`, `E7`, `E9`, `F` |
| `[tool.ruff.lint] extend-select` | `I` (import sorting) |

`server/pyproject.toml` and `worker/pyproject.toml` carry matching
`[tool.ruff]` blocks, so an editor or pre-commit run that resolves the nearest
config agrees with the repo-root run.

The root dev group also pins `ty`
(`uv run ty check server`). No CI job runs it.

## Running tests

Start local MongoDB, run the server test suite, then stop MongoDB:

```bash
mkdir -p .local/mongo-data
mongod --dbpath .local/mongo-data --fork --logpath .local/mongod.log
(cd server && uv run python -m unittest discover -s tests -v)
mongod --shutdown --dbpath .local/mongo-data
```

For a focused server-only loop when MongoDB is already running:

```bash
(cd server && uv run python -m unittest discover -s tests -v)
```

Run a single module or test case:

```bash
(cd server && uv run python -m unittest -v tests.test_views_run)
(cd server && uv run python -m unittest -v tests.test_views_run.SpsaParsingTests)
```

The worker suite needs no database and no uv project sync:

```bash
(cd worker && PYTHONPATH=packages python3 -m unittest discover -vb -s tests)
```

Tests write to the `fishtest_tests` database and drop their collections in
`cleanup_test_rundb` (`server/tests/test_support.py`). They never touch
`fishtest_new`.

## Pre-commit hooks

`.pre-commit-config.yaml` runs on every commit:

| Hook | Repo | What it does |
|------|------|--------------|
| `check-added-large-files`, `check-toml`, `check-yaml`, `end-of-file-fixer`, `trailing-whitespace` | `pre-commit/pre-commit-hooks` | Basic file hygiene and TOML/YAML validation |
| `ruff-check` | `astral-sh/ruff-pre-commit` | Lint with `--fix --exit-zero` |
| `ruff-format` | `astral-sh/ruff-pre-commit` | Format |
| `uv-lock` | `astral-sh/uv-pre-commit` | Run `uv lock` so the lock file stays in sync with `pyproject.toml` |

`ruff-check` runs with `--exit-zero`, so lint findings fix what they can and
never block a commit. CI is the gate. Run the whole suite manually with
`uv run pre-commit run --all-files`.

## CI workflows

All four workflows trigger on `push`, `pull_request` and `workflow_dispatch`.

| Workflow name | File | What it runs |
|---------------|------|--------------|
| CI lint | `.github/workflows/lint.yaml` | Ruff lint, import sort, format check and Prettier check on Python 3.14 and Node 26 |
| CI server | `.github/workflows/server.yaml` | `uv sync --group test` then `python -m unittest discover -vb -s tests` against MongoDB 8.0, with `GH_TOKEN` set |
| CI worker posix | `.github/workflows/worker_posix.yaml` | Worker tests on Ubuntu and macOS for Python 3.8 through 3.14 |
| CI worker msys2 | `.github/workflows/worker_msys2.yaml` | Worker tests on Windows under mingw64, ucrt64, clang64 and clangarm64 |

The lint job records each check with `continue-on-error` and fails at the end
if any of lint, sort, format or Prettier failed, so one run reports every
formatting problem at once.

## vtjson rules

Use vtjson for all server-side validation. Schema format reference:
`https://www.cantate.be/vtjson`.

Core rules:

- Define persisted document schemas in `server/fishtest/schemas.py`.
- Treat persisted vtjson schemas as both documentation and the final
    server-side gate for stored data.
- Keep vtjson as the only server-side data validation layer. Do not introduce
    Pydantic models or a second schema system for the same contracts.
- Use different schemas when raw input and persisted data intentionally allow
    different values.
- Canonicalize benign user input in Python before validating the persisted
    document shape.
- Keep field-specific contracts in the relevant reference page instead of
    freezing them into this guide.
- Name schemas by boundary or purpose, not by convenience.
- Bump `server/fishtest/schemas.py` -> `RUN_VERSION` when the stored run schema
    contract changes. `server/fishtest/rundb.py` and `server/fishtest/views.py`
    compare the stored `run["version"]` against it before trusting a document.
- Shared literals used by schemas live in `server/fishtest/constants.py`
    (`PASSWORD_MAX_LENGTH`, `VALID_USERNAME_PATTERN`, `supported_arches`,
    `supported_compilers`). Change them there, not in the schema module.
- Add focused tests for both the schema rule and the changed behavior.
- Run the lint and test commands above after schema changes.

Boundary split pattern:

- a route may accept a broader raw-input value than the stored document allows
- canonicalization narrows the value before the persisted document is validated
    and written
- the persisted schema describes the final stored form, not every accepted
    input variant

## Operational utilities

`server/utils/` holds the maintenance and analysis scripts. Two are useful
during development:

- `server/utils/create_indexes.py` -- create the MongoDB indexes the server
    queries expect. Run it once against a fresh local database.
- `server/utils/nginx_cidr_builder.py` -- generate the `geo $region` map
    required by the nginx config below.

[8-deployment.md](8-deployment.md) documents the full inventory and when an
operator runs each script.

## nginx development config

For local multi-instance testing with nginx, use the development-only HTTP
config below. It mirrors the production routing topology without TLS and works
on local VMs where the IP may change between boots. Leave `FISHTEST_URL` and
`FISHTEST_NN_URL` empty in the systemd units to allow dynamic host and IP
usage.

This config has three prerequisites:

1. `$region` is defined by a `geo` block that is not part of this file.
   Generate it and place it where the `http` context includes it, otherwise
   nginx refuses to start with `unknown "region" variable`:

   ```bash
   (cd server && uv run python utils/nginx_cidr_builder.py -o /etc/nginx/conf.d/cidr.conf)
   ```

2. `/var/www/fishtest/static/` must contain the contents of
   `server/fishtest/static/`, because nginx serves `/static/`, `/robots.txt`
   and `/favicon.ico` itself and never reaches the app mount.

3. `/var/www/fishtest/nn/` must exist and be writable by the server process.
   `server/fishtest/views.py` writes uploaded networks there as
   `nn-<hash>.nnue.gz`, and `location /nn/` serves them with `gzip_static`.

File: `/etc/nginx/sites-available/fishtest.conf`

```nginx
upstream backend_8000 {
    server 127.0.0.1:8000;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

upstream backend_8001 {
    server 127.0.0.1:8001;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

upstream backend_8002 {
    server 127.0.0.1:8002;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

upstream backend_8003 {
    server 127.0.0.1:8003;
    keepalive 256;
    keepalive_requests 10000;
    keepalive_timeout 60s;
}

map $uri $backends {
    /tests                                 backend_8001;
    ~^/api/(actions|active_runs|calc_elo)  backend_8002;
    ~^/api/(nn|pgn|run_pgns)/              backend_8002;
    ~^/api/upload_pgn                      backend_8003;
    ~^/tests/(finished|machines|user)      backend_8002;
    ~^/(actions/|contributors)             backend_8002;
    ~^/(api|tests)/                        backend_8000;
    default                                backend_8001;
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    server_tokens off;

    location = /nginx_status {
        stub_status  on;
        allow        127.0.0.1;
        allow        ::1;
        deny         all;
    }

    location = /        { return 308 /tests; }
    location = /tests/  { return 308 /tests; }

    location = /robots.txt {
        alias       /var/www/fishtest/static/robots.txt;
        access_log  off;
    }

    location = /favicon.ico {
        alias       /var/www/fishtest/static/favicon.ico;
        access_log  off;
        expires     1y;
        add_header  Cache-Control "public, max-age=31536000, immutable";
    }

    location ^~ /static/ {
        alias       /var/www/fishtest/static/;
        try_files   $uri =404;
        access_log  off;
        etag        on;
        expires     1y;
        add_header  Cache-Control "public, max-age=31536000, immutable";
    }

    location /nn/ {
        root         /var/www/fishtest;
        gzip_static  always;
        gunzip       on;
    }

    location / {
        # Canonical upstream identity
        proxy_set_header Host               $http_host;
        proxy_set_header X-Real-IP          $remote_addr;

        # Forwarded chain
        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto  $scheme;
        proxy_set_header X-Forwarded-Host   $host;
        proxy_set_header X-Forwarded-Port   $server_port;
        proxy_set_header Connection         "";

        # Custom metadata
        proxy_set_header X-Country-Code     $region;

        # Timeouts
        proxy_connect_timeout    2s;
        proxy_send_timeout       30s;
        proxy_read_timeout       60s;

        # Buffering
        proxy_request_buffering  on;
        proxy_buffering          on;
        proxy_next_upstream      off;

        client_max_body_size     200m;
        client_body_buffer_size  512k;

        proxy_redirect           off;
        proxy_http_version       1.1;

        # Decompression
        gunzip                   on;

        proxy_pass http://$backends;
    }
}
```

Start each backend with a matching `FISHTEST_PORT`, and set
`FISHTEST_PRIMARY_PORT=8000` on all of them, so only port 8000 runs the
scheduler and accepts the primary-only worker API paths.
