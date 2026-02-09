# Jinja2 references (project usage)

Date: 2026-02-09

Curated references and a short project-focused summary for the Jinja2 runtime
used in this repo.

## Canonical web references

- Jinja2 template designer documentation: https://jinja.palletsprojects.com/en/latest/templates/
- Jinja2 API (Environment, autoescape, undefined): https://jinja.palletsprojects.com/en/latest/api/
- Starlette templates: https://www.starlette.dev/templates/
- FastAPI templates: https://fastapi.tiangolo.com/advanced/templates/

## What matters for this repo

### Starlette templating behavior

- Use `Jinja2Templates(env=...)` or `Jinja2Templates(directory=...)`, not both.
- `TemplateResponse` requires `request` in the context.
- `url_for` is injected by Starlette when `Jinja2Templates` is used.
- `TemplateResponse` exposes `response.template` and `response.context` for tests.

### Environment configuration

- Use `select_autoescape(["html", "xml", "j2"])`.
- Keep a single `Environment` instance; set globals/filters before templates load.
- Use a lenient `Undefined` in runtime if parity requires it; enforce strict undefined in tests.

### Project-specific runtime rules

- Rendering is synchronous and stays off the event loop.
- The shared helper base is registered as filters/globals.
- Templates are declarative; data shaping stays in view builders and helpers.
- JS payloads are passed via `|tojson`.

## Recommended usage pattern (status quo)

- Build a per-request context in the view layer.
- Use `TemplateResponse(request=..., name=..., context=...)` for UI responses.
- Keep legacy Mako templates read-only for parity tooling.

## Tooling

- Context coverage: [WIP/tools/template_context_coverage.py](WIP/tools/template_context_coverage.py)
- HTML parity: [WIP/tools/compare_template_parity.py](WIP/tools/compare_template_parity.py)
- Response parity: [WIP/tools/compare_template_response_parity.py](WIP/tools/compare_template_response_parity.py)
