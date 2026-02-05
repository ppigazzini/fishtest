# Mako references (for this project)

Date: 2026-02-04

Curated references and a project-focused synthesis for the legacy Mako and new Mako tracks.

## Canonical references (from ___mako/doc/build)

- Overview index: ___mako/doc/build/index.rst
- Syntax (expressions, control flow, tags): ___mako/doc/build/syntax.rst
- Defs and blocks: ___mako/doc/build/defs.rst
- Namespaces: ___mako/doc/build/namespaces.rst
- Inheritance: ___mako/doc/build/inheritance.rst
- Filtering and buffering: ___mako/doc/build/filtering.rst
- Runtime and Context: ___mako/doc/build/runtime.rst
- Usage and TemplateLookup: ___mako/doc/build/usage.rst
- Unicode handling: ___mako/doc/build/unicode.rst
- Caching: ___mako/doc/build/caching.rst

## Synthetic report — what matters for this project

### 1) Layout and inheritance (base + blocks)
- Prefer `<%block>` for layout regions and inheritance overrides.
- Use `${self.body()}` (legacy) or `${next.body()}` only when the chain requires middle layouts.
- Avoid `<%def>` for layout sections; defs are better for reusable snippets.

Why: Inheritance in Mako is driven by `self`, `parent`, and `next` namespaces; blocks are designed for layout overrides and avoid scoping issues seen with nested defs.

### 2) Embedded Python is powerful but expensive
- Keep `<% %>` and `<%! %>` blocks minimal and deterministic.
- Module-level `<%! %>` is for imports and pure helpers only (no request state).
- Move heavy logic into shared helpers (see [WIP/docs/2.4-MAKO-NEW.md](WIP/docs/2.4-MAKO-NEW.md)).

Why: Templates compile to Python; excess inline logic hurts readability and makes Jinja2 parity harder.

### 3) Escaping and filters
- Default-escape HTML via `<%page expression_filter="h"/>` or `default_filters=["h"]` in the lookup.
- Use `|n` sparingly and document the trust boundary.
- Prefer explicit filters (`|h`, `|u`, `|trim`) for clarity in critical sections.

Why: The filtering system is simple but easy to misuse. Default escaping reduces latent XSS risk.

### 4) Namespace usage
- Prefer `<%namespace file="..." import="name1, name2"/>` over `import="*"`.
- Avoid inheritable namespaces unless the base layout truly needs them.

Why: `import="*"` is slower and makes dependencies implicit. Explicit imports are faster and clearer.

### 5) TemplateLookup configuration
- Use `TemplateLookup` with `module_directory` for compiled template caching.
- Set `filesystem_checks=False` in production for stable deployments.
- Consider `collection_size` to bound in-memory template cache.

### 9) Runtime behavior and errors
- `format_exceptions=True` can render HTML error output during template execution.
- `strict_undefined=True` is useful in tests to surface missing context early.

Why: This reduces repeated compilation and avoids filesystem stats on every request.

### 6) Strict undefined in tests
- Use `strict_undefined=True` for tests to surface missing context keys.
- Keep production lenient if legacy templates rely on `UNDEFINED` behavior.

Why: This catches broken contexts during migration while keeping production stable.

### 7) Unicode and encoding
- Use `input_encoding="utf-8"` in the lookup unless templates embed a coding comment.
- Avoid manual byte decoding in templates; decode upstream in Python helpers.

Why: Mako always operates on Unicode internally; explicit encoding settings prevent subtle bugs.

### 8) Caching (only when clearly needed)
- If using `<%page cached="True" ...>` or `<%block cached="True" ...>`, prefer cache regions configured on the lookup.
- Avoid inline caching unless a real hot spot is measured.

Why: Caching is optional and can complicate invalidation if misused.

## Project-specific guidance (Milestone 8)

Based on [WIP/docs/2.4-MAKO-NEW.md](WIP/docs/2.4-MAKO-NEW.md) and [WIP/docs/3.8-ITERATION.md](WIP/docs/3.8-ITERATION.md):

- The new Mako track is not a copy of legacy Mako. It should reduce embedded Python and centralize helpers.
- Keep legacy templates untouched in [server/fishtest/templates](server/fishtest/templates) for rebase safety.
- New templates live in [server/fishtest/templates_mako](server/fishtest/templates_mako) and should match legacy output.
- Shared helper base must be used by both new Mako and Jinja2 renderers.
- Use parity checks and avoid behavior changes during conversion.

## Idiomatic usage patterns (Mako + FastAPI/Starlette)

### Template rendering flow
- Render templates in a threadpool when called from async endpoints.
- Pass an explicit, minimal context dictionary to templates.
- Keep request/session helpers in a shared helper module to avoid per-template imports.

### Example: TemplateLookup setup
```python
from mako.lookup import TemplateLookup

lookup = TemplateLookup(
    directories=["/path/to/templates_mako"],
    input_encoding="utf-8",
    default_filters=["h"],
    module_directory="/path/to/mako_modules",
    filesystem_checks=False,
)
```

### Example: Safe default escaping
```mako
<%page expression_filter="h"/>

<div>${user_name}</div>
```

### Example: Small reusable def
```mako
<%def name="render_badge(label)">
  <span class="badge">${label}</span>
</%def>

${render_badge("Stable")}
```

## Quick “use this when…” cheatsheet

- Layout overrides: `<%block>` + `${self.body()}`.
- Reusable view bits: `<%def>` or macros with namespaces.
- Shared helpers: Python module + explicit imports in `<%! %>`.
- Safe output: default filters + explicit `|n` only for trusted HTML.
- Template caching: lookup + `module_directory`, `filesystem_checks=False` in production.
- Parity migration: keep output stable, move logic into helpers.

## Lessons learned (migration safety)

- Copying legacy templates into `templates_mako` is only a bootstrap step; it does not meet the cleanup goals.
- If a template uses heavy inline Python, move calculations into helpers before translating syntax.
- Ensure every template that inherits base uses `<%block>` sections, not raw content only.
- Keep request-specific logic out of `<%! %>` blocks to avoid stale or incorrect runtime behavior.

## Suggested checklist for each new Mako template

- Uses `<%block>` for layout regions.
- Default escaping enabled.
- Inline Python minimized and documented.
- No `import="*"` namespaces.
- Output parity checked vs legacy Mako.
- Any `|n` usage justified and reviewed.

## Project-specific notes (current)

- New Mako templates use `TemplateLookup` with `default_filters=["h"]` and `strict_undefined=False` for parity safety.
- `MakoTemplateResponse` emits debug metadata when request extensions include `http.response.debug`.
- Response parity checks validate status, headers, and debug metadata, not just HTML.
- Context coverage is tracked via [WIP/tools/template_context_coverage.py](WIP/tools/template_context_coverage.py).
