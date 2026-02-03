# Jinja2 references (for this project)

Date: 2026-02-03

Curated web references and a short project-focused synthesis for the Mako -> Jinja2 migration.

## Canonical web references

- Jinja2 template designer documentation: https://jinja.palletsprojects.com/en/latest/templates/
- Jinja2 API (Environment, autoescape, undefined): https://jinja.palletsprojects.com/en/latest/api/
- Starlette templates: https://www.starlette.dev/templates/
- Starlette dependencies list (Jinja2 required for templates): https://www.starlette.dev/
- FastAPI templates: https://fastapi.tiangolo.com/advanced/templates/

## Synthetic report — what matters for this project

### 1) Starlette and FastAPI templating glue

- Use `Jinja2Templates` from `starlette.templating` or `fastapi.templating`.
- `TemplateResponse` must include `request` in the context.
- `url_for` is automatically available in template context when using `Jinja2Templates`.
- Context processors are supported but must be sync functions.

### 2) Dependency requirements

- `jinja2` must be installed (Starlette treats it as an optional dependency).
- No additional Starlette module is required; `jinja2` is enough.

### 3) TemplateResponse signature (FastAPI/Starlette)

- In newer versions, use the keyword form:
  - `TemplateResponse(request=request, name="template.html", context={...})`
- Older versions used positional arguments; avoid positional calls for compatibility.

### 4) Environment configuration

- Prefer `select_autoescape(["html", "xml"])`.
- Keep `autoescape=True` for HTML and use `|safe` sparingly.
- Use `StrictUndefined` in tests to catch missing keys early, while keeping production lenient if needed.
- Add filters via `templates.env.filters[...]` or provide a custom `jinja2.Environment`.

### 5) Context processors vs explicit context

- Context processors are useful for shared globals but make migration harder to audit.
- Prefer explicit context passed by the view for migration safety.
- Keep a minimal, audited set of globals (e.g., `static_url`, formatting helpers).

### 6) Jinja2 syntax points relevant to migration

- Blocks and inheritance: `{% extends %}`, `{% block %}`, `{{ super() }}`.
- Macros: `{% macro %}` and `{% import %}` for shared UI components.
- Includes: `{% include %}` with optional `with context`.
- Escaping: `|e` for escaped output, `|safe` for trusted HTML.
- Whitespace control: `trim_blocks`, `lstrip_blocks`, and `-%}` when required.

### 7) Testing templates (Starlette)

- `TemplateResponse` exposes `.template` and `.context` in tests.
- Use snapshot tests or DOM diffs to compare Mako vs Jinja2 outputs.

## Recommended usage pattern (project-specific)

- Keep Mako templates and Mako rendering available during migration for rebase safety.
- Introduce Jinja2 rendering behind a per-template feature map.
- Convert hardest templates first and validate with DOM diff checks.
- Remove Mako runtime dependencies only after full parity is proven.
