# Development Guide

## Prerequisites

| Component | Minimum version | Purpose |
|-----------|-----------------|---------|
| Python | >= 3.14 | Server Runtime |
| Python | >= 3.8 | Worker Runtime |
| MongoDB | mongod service | Data store |
| uv | -- | Python package manager |

nginx is not required for single-instance development. For multi-instance
local testing, see the [nginx development config](#nginx-development-config)
section below.

## Installation

```bash
cd server && uv sync && uv sync --group test
```

This installs all runtime and test dependencies into a virtual environment at
`server/.venv`.

## Running the development server

```bash
cd server
FISHTEST_INSECURE_DEV=1 uv run uvicorn fishtest.app:app --reload --port 8000
```

No concurrency flags are needed in development. The async event loop handles
concurrent requests natively.

Setting `FISHTEST_INSECURE_DEV=1` enables an insecure fallback secret key
for cookie signing. This must never be used in production.

### Running the worker with the development server

The worker must authenticate against `/api/request_version`, so the username
must exist in the local MongoDB and must not be `pending` or `blocked`.

After starting the server with the command above, run the worker in a second
terminal:

```bash
cd worker
uv run worker.py USERNAME PASSWORD --protocol http --host 127.0.0.1 --port 8000
```

### OpenAPI documentation

To enable the interactive OpenAPI docs (`/docs`, `/redoc`, `/openapi.json`)
during development:

```bash
OPENAPI_URL=/openapi.json FISHTEST_INSECURE_DEV=1 uv run uvicorn fishtest.app:app --reload --port 8000
```

OpenAPI docs are disabled in production (`openapi_url` defaults to `None`,
which prevents FastAPI from registering the `/openapi.json`, `/docs`, and
`/redoc` routes). Setting `OPENAPI_URL=/openapi.json` re-enables them and
exposes the full API and UI route schema in the Swagger UI.

## Environment variables

The full environment variables table is in [8-deployment.md](8-deployment.md).
The following subset is relevant during development:

| Variable | Default | Description |
|----------|---------|-------------|
| `FISHTEST_INSECURE_DEV` | -- | Set to `1` to use insecure fallback signing secret |
| `OPENAPI_URL` | (empty) | Set to `/openapi.json` to enable `/docs` and `/redoc` |
| `FISHTEST_PORT` | `-1` | Defaults to primary when unset |
| `FISHTEST_PRIMARY_PORT` | `-1` | Defaults to primary when unset |
| `FISHTEST_JINJA_TEMPLATES_DIR` | auto | Override Jinja2 templates directory |

When `FISHTEST_PORT` and `FISHTEST_PRIMARY_PORT` are both unset or negative,
the instance defaults to primary -- the expected mode for single-instance
development.

## Linting

Run Ruff from the repo root so it uses the tracked dev tool and shared config.

```bash
uv run ruff check server worker --config pyproject.toml
```

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

See [0-README.md](0-README.md) for pre-commit hooks and CI workflows.

## valgebra rules

Use [valgebra](https://ppigazzini.github.io/valgebra/) for all server-side
validation. A schema denotes a set of Python values, and validation is
membership: the document is checked as it stands, never copied or coerced.

Core rules:

- Define persisted document schemas in `server/fishtest/schemas.py`.
- Treat the persisted schemas as both documentation and the final server-side
    gate for stored data.
- Write a schema as a typing annotation where typing can spell the set, and use
    `Annotated[T, ...]` with the annotated-types markers for a refinement. Reach
    for `union`, `intersection` and `complement` when the set is a combination,
    and for a predicate only when no marker expresses the check.
- Compile each schema once, at import, into a module-level `Validator`; call
    `.validate(document)` at the boundary.
- State the type a field actually stores. `int` and `float` are disjoint sets
    and a literal is a typed singleton, so a field declared `float` rejects
    `0` and a field declared `0.0` rejects `0`. Where a writer disagrees with
    the schema, fix the writer: the schema is the statement of intent.
- Name a cross-field rule and intersect it with the record, rather than burying
    the check inside the record. A document then belongs to the record and to
    every rule, which is what the algebra says and what the error report shows.
- Pass `fail_fast=True` where the structure is unbounded (a run document, the
    caches) so one bad field does not produce a report per element; let the
    small documents aggregate every failure.
- A mapping clause's key names a whole type, so a key narrowed by a constraint
    is refused where it is written. Write `dict[str, V]` and check the key shape
    beside the mapping with the `keys_in` recipe; the mapping stays in the
    algebra and only the key constraint is opaque to a relation.
- `Regex` matches the whole string on the Rust engine, whose dialect is close
    to `re`'s but not equal to it. `\d`, `\w` and `\s` are Unicode-aware, so
    write `[0-9]` where the value is parsed as a number afterwards, and check a
    ported pattern against the [refinements
    page](https://ppigazzini.github.io/valgebra/05-refinements/) rather than
    against "it compiled".
- A `False` from `is_subtype_of`, `is_equivalent` or `is_empty` is "no, or not
    proven", never a proof of the negative. Ask `relation_to` when a test needs
    the refutation: `"not_subset"` is a statement about a value, `"undecided"`
    is the conservative answer, and only membership is exact in both directions.
    `relation_to(nothing)` is the same three answers about emptiness, so
    `"not_subset"` there is a *proof that a schema admits a value*.
- Validate a JSON boundary with `load`, which parses and checks in one pass on
    the Rust path and returns the document. Nothing else may parse the same
    bytes in Python: a `json.loads` beside a schema reads the document twice.
- A shape check written as a chain of `isinstance` probes is a second validation
    layer for the same contract. State it as a schema and check it once. An open
    record (`open_record`, or `.open()`) is "at least these fields", which is
    what a reader tolerant of what it does not touch means — and it is what
    keeps a third-party payload gaining a key from failing.
- Where a reader is deliberately tolerant, give each read its own schema rather
    than one schema for the whole payload; a partly corrupt document then still
    yields the fields that are intact. State the relation between those schemas
    as a meet when one is the other plus a field, so it stays a proof rather
    than two shapes kept in step by hand.
- `value in schema` is the operator form of `schema.is_valid(value)`. Prefer it
    where the surrounding code reads as set membership, and the method where the
    validator's name does not read as a set.
- Keep valgebra as the only server-side data validation layer. Do not introduce
    Pydantic models or a second schema system for the same contracts.
- Use different schemas when raw input and persisted data intentionally allow
    different values.
- Canonicalize benign user input in Python before validating the persisted
    document shape.
- Keep field-specific contracts in the relevant reference page instead of
    freezing them into this guide.
- Name schemas by boundary or purpose, not by convenience.
- Update `RUN_VERSION` when the stored run schema contract changes.
- Add focused tests for both the schema rule and the changed behavior.
- Run the relevant lint checks and test suite after schema changes.

Boundary split pattern:

- a route may accept a broader raw-input value than the stored document allows
- canonicalization narrows the value before the persisted document is validated
    and written
- the persisted schema describes the final stored form, not every accepted
    input variant

## nginx development config

For local multi-instance testing with nginx, use the development-only HTTP
config below. This mirrors the production routing topology without TLS and
works on local VMs where the IP may change between boots. Leave
`FISHTEST_URL` and `FISHTEST_NN_URL` empty in the systemd units to allow
dynamic host/IP usage.

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
