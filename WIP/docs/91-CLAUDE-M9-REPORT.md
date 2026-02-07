# Prompt: Write `91-CLAUDE-M9.md` — Milestone 9 Analysis Report

**Date:** 2026-02-06
**Target output file:** `WIP/docs/91-CLAUDE-M9.md`

Note: The new Mako plan doc (2.4-MAKO-NEW) was retired in Milestone 10; any
references in this closed report are historical.

---

## Role & Perspective

Act as a **team of two senior Pythonists** reviewing the fishtest Pyramid → FastAPI migration.

- **Reviewer A (Architecture & Idiom):** focuses on whether the code follows idiomatic 2026 Starlette/FastAPI/Mako/Jinja2 patterns, whether the Starlette `Jinja2Templates` class was adopted correctly, whether the Mako `TemplateResponse`-style wrapper mirrors Starlette semantics faithfully, and whether the shared helper extraction is clean.

- **Reviewer B (Rebase Safety & Ops):** focuses on whether the code stays rebaseable against upstream commits, whether the mechanical-port contract is preserved, whether parity scripts and metrics tooling are sufficient, and whether `ruff check --select ALL` + `ty check` cleanliness is maintained for all new `http/` code (excluding `api.py` and `views.py`, which are mechanical ports compared via parity scripts to their legacy twins).

Both reviewers value:
- Ability to rebase cleanly after an upstream commit
- Human-readable code (single-pass readability, hop count ≤ 1, helper calls ≤ 2)
- Idiomatic modern 2026 Starlette / FastAPI / Mako / Jinja2 code
- Idiomatic Python 3.14, enforced via `ruff check --select ALL` and `ty check` on all new `http/` modules (but NOT on `api.py` / `views.py`)

---

## Context You Must Read (source of truth)

Read and internalize **all** of the following before writing the report. Cross-reference code against docs and reference implementations throughout.

### 1. Project docs (`WIP/docs/`)

Read in this order:

| File | Purpose |
|------|---------|
| `0-INDEX.md` | Entry point, invariants, contracts |
| `1-FASTAPI-REFACTOR.md` | Authoritative migration plan (mechanical port rules, protocol contracts, tooling rules, acceptance criteria) |
| `2-ARCHITECTURE.md` | Current repo snapshot (module map, runtime architecture, UI contract, template shims) |
| `2.1-ASYNC-INVENTORY.md` | Async/blocking boundaries |
| `2.2-MAKO.md` | Legacy Mako template catalog |
| `2.3-JINJA2.md` | Jinja2 migration plan (environment, filters/globals, rendering flow, DOM diff strategy) |
| new Mako plan (retired in M10) | Best practices, helper base, rendering setup |
| `3-MILESTONES.md` | Roadmap — pay special attention to Milestone 8 (complete) and Milestone 9 (in progress) |
| `3.0-ITERATION-RULES.md` | Rules of engagement, two-step landing, verification gates |
| `3.8-ITERATION.md` | Milestone 8 completion record (parity, metrics, helper assessment) |
| `3.9-ITERATION.md` | **Milestone 9 iteration plan** — the primary subject of this report (phases 0-4, ASGI risks, decision log) |
| `5-REBASE.md` | Rebase process and parity tooling |
| `7-STARLETTE-REFERENCES.md` | Starlette reference synthesis (templates section is key) |
| `11.3-TEMPLATES-METRICS.md` | Metrics snapshot (legacy Mako vs new Mako vs Jinja2) |
| `9-JINJA2-REFERENCES.md` | Jinja2 reference synthesis |
| `10-MAKO-REFERENCES.md` | Mako reference synthesis |
| `90-CLAUDE-REPORT.md` | Previous analysis report (Milestone 6 era — compare progress) |

### 2. Starlette reference code (`___starlette/`)

**Critical file:** `___starlette/starlette/templating.py`

Study the actual Starlette implementation:
- `class _TemplateResponse(HTMLResponse)` — how it stores `template` + `context`, renders via `template.render(context)`, and sends `http.response.debug` metadata.
- `class Jinja2Templates` — constructor (`directory` vs `env`), `_setup_env_defaults` (how `url_for` is injected via `@pass_context`), `context_processors`, and `TemplateResponse(...)` method signature.

Compare this **line by line** against the project's:
- `server/fishtest/http/jinja.py` — does it use `Jinja2Templates(env=...)` correctly? Are globals set before template load? Is `url_for` handled correctly? Does `render_template_response()` match Starlette's `TemplateResponse` semantics?
- `server/fishtest/http/mako_new.py` — does `MakoTemplateResponse` faithfully mirror `_TemplateResponse`? Is the debug metadata path the same? Are there semantic gaps?

### 3. Template rendering code (`server/fishtest/http/`)

Read and analyze these files:

| File | Role |
|------|------|
| `jinja.py` | Jinja2 environment, `Jinja2Templates` wrapper, `render_template()`, `render_template_response()` |
| `mako.py` | Legacy Mako `TemplateLookup` + `render_template()` |
| `mako_new.py` | New Mako `TemplateLookup` + `MakoTemplateResponse` + `render_template()` + `render_template_response()` |
| `template_renderer.py` | Unified renderer switch (`TemplateEngine` literal, `set_template_engine()`, engine dispatch, dual-render for parity) |
| `template_helpers.py` | Shared helper base (filters, formatters, stats helpers — used by both Jinja2 and Mako) |
| `template_request.py` | `TemplateRequest` shim (Pyramid-compatible request surface for templates) |
| `ui_pipeline.py` | `build_template_request()` + `apply_http_cache()` |
| `ui_context.py` | UI context assembly |
| `ui_errors.py` | UI error rendering (404/403) |
| `boundary.py` | HTTP boundary (session, response helpers) |

### 4. Parity and metrics tools (`WIP/tools/`)

Read and evaluate these scripts:

| Script | Purpose |
|--------|---------|
| `compare_template_parity.py` | Core parity engine: renders with two engines, normalizes HTML, diffs |
| `compare_jinja_mako_parity.py` | Jinja2 vs new Mako parity runner (wraps the core) |
| `templates_mako_metrics.py` | Mako template complexity metrics |
| `templates_jinja_metrics.py` | Jinja2 template complexity metrics |
| `templates_comparative_metrics.py` | Cross-engine comparative metrics |
| `parity_check_api_routes.py` | API route parity check |
| `parity_check_views_routes.py` | Views route parity check |
| `parity_check_api_ast.py` | API AST parity check |
| `parity_check_views_ast.py` | Views AST parity check |
| `parity_check_hotspots_similarity.py` | Hotspot similarity check |
| `parity_check_views_no_renderer.py` | Views without renderer inventory |

### 5. Reference implementations

| Directory | What to look for |
|-----------|-----------------|
| `___starlette/starlette/templating.py` | The canonical `Jinja2Templates` + `_TemplateResponse` implementation |
| `___fastapi/` | FastAPI template integration patterns (if any `templates` module exists) |
| `___jinja/` | Jinja2 `Environment`, `FileSystemLoader`, `select_autoescape`, `Undefined` class hierarchy |
| `___mako/` | Mako `TemplateLookup` configuration, `Template.render()`, default filters, `strict_undefined` |

---

## Report Structure (write this as `91-CLAUDE-M9.md`)

### Header
```
# Claude M9 Analysis Report: Template Rendering Alignment
**Date:** 2026-02-06
**Milestone:** 9 — Template rendering alignment (Starlette Jinja2 + Mako)
**Codebase Snapshot:** Current `server/fishtest/http/` template modules
**Python Target:** 3.14+
```

### Section 1 — Executive Summary
- One-paragraph assessment of Milestone 9 progress.
- Key finding: is the Starlette `Jinja2Templates` adoption correct and complete?
- Key finding: is the Mako `TemplateResponse` wrapper a faithful mirror?
- Key finding: are the shared helpers well-extracted?

### Section 2 — Starlette `Jinja2Templates` Alignment Analysis

**Reviewer A's analysis.** Compare `jinja.py` against `___starlette/starlette/templating.py`:

- Is `Jinja2Templates(env=custom_env)` used correctly?
- Is `_setup_env_defaults` (url_for injection) happening? If not, is url_for provided manually?
- Are `context_processors` supported / used?
- Does `render_template_response()` match the signature and semantics of Starlette's `TemplateResponse()`?
  - `request` injection into context
  - `context_processors` application
  - Debug metadata via `http.response.debug`
- Are there any gaps, deviations, or missing features?
- Is the `MakoUndefined` class a correct approximation of Mako's `UNDEFINED` behavior?
- Are globals/filters set before template loading (stable environment)?

### Section 3 — Mako `MakoTemplateResponse` Analysis

**Reviewer A's analysis.** Compare `mako_new.py` against `___starlette/starlette/templating.py`:

- Does `MakoTemplateResponse` mirror `_TemplateResponse`?
  - `template` + `context` stored on the response?
  - `template.render(**context)` called correctly?
  - `http.response.debug` metadata sent correctly?
- Is there a semantic gap between Starlette's `request.get("extensions", {})` (dict-based) and `getattr(request, "extensions", None)` (attribute-based)?
- Is the `__call__` method ASGI-correct?
- Does `render_template_response()` in `mako_new.py` cover the same use cases as `jinja.py`'s version?

### Section 4 — Shared Helper Base (`template_helpers.py`) Nitpick

**Both reviewers.** Analyze the helper extraction:

- Is the helper module well-scoped? Are there too many or too few helpers?
- Are any helpers re-exported from `fishtest.util` that should be owned by the template layer?
- Are the stats helpers (`t_conf`, `nelo_pentanomial_summary`, `LLRcalc` usage) correctly placed?
- Is `__all__` complete and accurate?
- Are there helpers registered in `jinja.py` globals that are NOT in `template_helpers.py`? (e.g., `copy`, `datetime`, `math`, `float`, `urllib`)
- Are there naming inconsistencies between what Mako templates expect and what Jinja2 globals provide?
- `ruff check --select ALL` and `ty check` cleanliness?

### Section 5 — `template_renderer.py` (Unified Switch) Analysis

**Reviewer B's analysis:**

- Is the engine switch mechanism clean and deterministic?
- Is `_STATE` (mutable module-level singleton) appropriate, or should this be app-state?
- Are the unused functions (`_jinja_template_exists`, `_mako_new_template_exists`, `assert_*`) dead code or planned?
- Is `render_template_dual()` only used by parity tools? Should it live in tools instead?
- `TemplateEngine` literal type — is it used consistently?
- Are there any risks from eagerly constructing `_MAKO_LOOKUP`, `_MAKO_NEW_LOOKUP`, `_JINJA_TEMPLATES` at module import time?

### Section 6 — Whole-Project Status Assessment

**Both reviewers.** Assess the entire project against the goals in `1-FASTAPI-REFACTOR.md` and `3-MILESTONES.md`:

#### What is done correctly:
- List milestones completed and their quality.
- Highlight code that exemplifies the project values (readability, rebasability, idiom).

#### What can be improved:
- Specific code improvements with file paths and line references.
- Documentation gaps.
- Test coverage gaps.

#### What is still wrong or risky:
- Any protocol parity issues.
- Any ASGI correctness issues (event loop blocking, threadpool discipline).
- Any rebase-safety risks.
- Any lint/type-check issues in new `http/` modules.

### Section 7 — Suggestions

Organize suggestions into three categories:

#### 7.1 Fixing Issues
- Concrete code fixes with before/after examples.
- File paths and specific line numbers.

#### 7.2 Improving Code
- Refactoring suggestions that preserve behavior.
- Idiomatic Python 3.14 improvements.
- Starlette/FastAPI alignment improvements.

#### 7.3 Improving Parity & Metrics Scripts
- Gaps in current parity checking.
- Suggestions for new scripts or enhanced checks.
- Template coverage gaps.
- Ways to measure rendering performance.
- Suggestions for response-level parity checks (not just HTML string parity).

### Section 8 — Reviewer A vs Reviewer B: Points of Disagreement

Where the two perspectives differ (e.g., "clean code" vs "rebase safety"), state both positions and recommend a resolution.

### Section 9 — Action Items (Prioritized)

A prioritized list of specific, actionable items with effort estimates.

---

## Quality Gates for Your Report

Before finalizing your report, verify it against these gates:

1. **Code-verified:** Every claim about code behavior is verified against the actual source files. Do not guess — read the files.
2. **Cross-referenced:** Claims about Starlette behavior are verified against `___starlette/starlette/templating.py`, not just docs.
3. **Docs-consistent:** Your assessment of milestone status matches what `3-MILESTONES.md` and `3.9-ITERATION.md` claim.
4. **Actionable:** Every suggestion includes a file path and enough detail to implement.
5. **Dual-perspective:** Both Reviewer A and Reviewer B perspectives are present in every section.
6. **No hallucination:** If you cannot find evidence for something, say so explicitly.
7. **Rebase-aware:** Suggestions do not create unnecessary diff churn in mechanical-port hotspots.
8. **Lint-aware:** Verify that suggestions for new code would pass `ruff check --select ALL` and `ty check`.

---

## Files to Read (complete list, absolute paths)

### Docs
```
WIP/docs/0-INDEX.md
WIP/docs/1-FASTAPI-REFACTOR.md
WIP/docs/2-ARCHITECTURE.md
WIP/docs/2.1-ASYNC-INVENTORY.md
WIP/docs/2.2-MAKO.md
WIP/docs/2.3-JINJA2.md
Retired new Mako plan (Milestone 10)
WIP/docs/3-MILESTONES.md
WIP/docs/3.0-ITERATION-RULES.md
WIP/docs/3.8-ITERATION.md
WIP/docs/3.9-ITERATION.md
WIP/docs/5-REBASE.md
WIP/docs/7-STARLETTE-REFERENCES.md
WIP/docs/11.3-TEMPLATES-METRICS.md
WIP/docs/9-JINJA2-REFERENCES.md
WIP/docs/10-MAKO-REFERENCES.md
WIP/docs/90-CLAUDE-REPORT.md
```

### Code (template rendering)
```
server/fishtest/http/jinja.py
server/fishtest/http/mako.py
server/fishtest/http/mako_new.py
server/fishtest/http/template_renderer.py
server/fishtest/http/template_helpers.py
server/fishtest/http/template_request.py
server/fishtest/http/ui_pipeline.py
server/fishtest/http/ui_context.py
server/fishtest/http/ui_errors.py
server/fishtest/http/boundary.py
server/fishtest/http/errors.py
server/fishtest/http/views.py          (scan for render call sites only)
server/fishtest/http/dependencies.py
server/fishtest/http/__init__.py
```

### Code (app wiring)
```
server/fishtest/app.py
```

### Reference implementations
```
___starlette/starlette/templating.py   (CRITICAL — line-by-line comparison target)
___fastapi/                            (scan for template usage)
___jinja/                              (scan for Environment, Undefined)
___mako/                               (scan for TemplateLookup, render)
```

### Tools
```
WIP/tools/compare_template_parity.py
WIP/tools/compare_jinja_mako_parity.py
WIP/tools/templates_mako_metrics.py
WIP/tools/templates_jinja_metrics.py
WIP/tools/templates_comparative_metrics.py
WIP/tools/template_parity_context.json
WIP/tools/parity_check_api_routes.py
WIP/tools/parity_check_views_routes.py
WIP/tools/parity_check_api_ast.py
WIP/tools/parity_check_views_ast.py
WIP/tools/parity_check_hotspots_similarity.py
WIP/tools/parity_check_views_no_renderer.py
```

### Templates (scan for structure, not line-by-line)
```
server/fishtest/templates/           (legacy Mako — 26 templates)
server/fishtest/templates_mako/      (new Mako — 26 templates)
server/fishtest/templates_jinja2/    (Jinja2 — 26 templates)
```

---

## Output Format

- Write as a single Markdown file.
- Use tables for comparisons.
- Use code blocks for code examples.
- Use `> [!NOTE]` / `> [!WARNING]` / `> [!IMPORTANT]` admonitions where appropriate.
- Keep the report factual and evidence-based. Cite file paths and line numbers.
- Target length: 1800–15000 lines of Markdown.

# Claude M9 Analysis Report: Template Rendering Alignment

**Date:** 2026-02-06
**Milestone:** 9 — Template rendering alignment (Starlette Jinja2 + Mako)
**Codebase Snapshot:** Current `server/fishtest/http/` template modules
**Python Target:** 3.14+
**Reviewers:** Reviewer A (Architecture & Idiom), Reviewer B (Rebase Safety & Ops)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Starlette Jinja2Templates Alignment Analysis](#2-starlette-jinja2templates-alignment-analysis)
3. [Mako MakoTemplateResponse Analysis](#3-mako-makotemplateresponse-analysis)
4. [Shared Helper Base Nitpick](#4-shared-helper-base-nitpick)
5. [template_renderer.py Analysis](#5-template_rendererpy-analysis)
6. [Whole-Project Status Assessment](#6-whole-project-status-assessment)
7. [Suggestions](#7-suggestions)
8. [Reviewer A vs Reviewer B: Points of Disagreement](#8-reviewer-a-vs-reviewer-b-points-of-disagreement)
9. [Action Items (Prioritized)](#9-action-items-prioritized)

---

## 1. Executive Summary

Milestone 9 is substantially complete. The codebase has adopted Starlette `Jinja2Templates` for Jinja2 rendering and introduced a Mako `MakoTemplateResponse` wrapper that mirrors Starlette's `_TemplateResponse` semantics. Both implementations are functional, lint-clean (`ruff check --select ALL` and `ty check` pass on all new `http/` modules), and preserve UI parity.

**Key findings:**

1. **Starlette `Jinja2Templates` adoption is correct and mostly complete.** The `Jinja2Templates(env=custom_env)` pattern is used properly. The `render_template_response()` function delegates to `templates.TemplateResponse(...)` with the correct keyword signature. One gap: context processors are accepted by the `Jinja2Templates` constructor but are not wired or used yet.

2. **The Mako `MakoTemplateResponse` is a faithful mirror of Starlette's `_TemplateResponse`**, with one semantic difference in how `request.extensions` is accessed (attribute-based via `getattr` vs Starlette's dict-based `.get()`). This is intentional and correct for this codebase because the context `request` is a `TemplateRequest` dataclass, not a raw Starlette `Request` dict.

3. **The shared helper base (`template_helpers.py`) is well-extracted** but has a scope mismatch: `jinja.py` registers additional globals (`copy`, `datetime`, `math`, `float`, `urllib`, `fishtest`, `gh`) that are not in `template_helpers.py` and are not shared with Mako. This is by design (Mako templates use `<%! %>` module blocks for imports), but it creates an asymmetry worth documenting.

4. **The unified renderer switch (`template_renderer.py`) works** but uses module-level mutable state (`_STATE`) and eagerly constructs all three template engines at import time, which has minor startup/testing implications.

5. **The `views.py` dispatch pipeline does NOT yet use `TemplateResponse`-style responses.** The central `_dispatch_view()` function still calls `render_template()` (string rendering) and wraps the result in a plain `HTMLResponse`. The `render_template_response()` functions in `jinja.py` and `mako_new.py` exist but are not wired into the live pipeline. This is the main remaining gap for Milestone 9 completion.

---

## 2. Starlette `Jinja2Templates` Alignment Analysis

*Reviewer A (Architecture & Idiom)*

### 2.1 `Jinja2Templates(env=custom_env)` — Correct

In [server/fishtest/http/jinja.py](../../server/fishtest/http/jinja.py), `default_templates()` creates `Jinja2Templates(env=env)`, passing a preconfigured `Environment`. This matches the Starlette overload that accepts `env` (see [___starlette/starlette/templating.py](../../___starlette/starlette/templating.py) line 86).

**Verified:** The Starlette `__init__` asserts `bool(directory) ^ bool(env)`, so passing `env=` without `directory=` is correct.

### 2.2 `_setup_env_defaults` and `url_for` — Handled differently

Starlette's `_setup_env_defaults()` injects a `@pass_context` `url_for` global into `env.globals.setdefault("url_for", url_for)`. Since `default_templates()` calls `Jinja2Templates(env=env)`, Starlette's `_setup_env_defaults` runs on the custom environment — so the `url_for` global is injected **after** the project's own `env.globals.update(...)`.

**Finding:** `url_for` is NOT explicitly listed in the project's `env.globals.update(...)` in `default_environment()`. This is correct: Starlette's `_setup_env_defaults` uses `setdefault`, so it will inject `url_for` into the environment only if it wasn't already set. The project correctly relies on Starlette to provide `url_for`.

**However**, templates also access `url_for` via `TemplateRequest.url_for()`. This means there are two `url_for` paths:
- Starlette's `@pass_context url_for` in the Jinja2 environment (uses Starlette `Request` from context).
- `request.url_for()` on the `TemplateRequest` shim (delegates to `raw_request.url_for()`).

> [!NOTE]
> The dual `url_for` is not a bug — Jinja2 templates use `{{ url_for("route_name") }}` which goes through Starlette's injected global, while legacy Mako-style `${request.url_for("route_name")}` calls the `TemplateRequest` method. Both ultimately delegate to `Request.url_for()`. But the duality should be documented.

### 2.3 Context Processors — Not wired

The `Jinja2Templates` constructor accepts `context_processors`, and Starlette applies them in `TemplateResponse()`:
```python
for context_processor in self.context_processors:
    context.update(context_processor(request))
```

The project's `default_templates()` call does not pass `context_processors`. This means context injection is done manually via `build_template_context()` in `boundary.py`. This is a deliberate choice: explicit context is preferred over implicit processors for migration safety.

**Assessment:** Acceptable for now. Context processors would be valuable later if the project wants to inject `static_url`, `csrf_token`, or `authenticated_userid` automatically.

### 2.4 `render_template_response()` — Correct but unused in the live pipeline

The function at [jinja.py](../../server/fishtest/http/jinja.py) lines 107-127 calls `templates.TemplateResponse(request=request, name=template_name, context=context_dict, ...)`. This matches Starlette's keyword-based signature ([templating.py](../../___starlette/starlette/templating.py) lines 117-148).

**Important:** It validates that `"request"` is present in context and raises `ValueError` if missing. Starlette's own `TemplateResponse` uses `context.setdefault("request", request)`, so it silently adds `request` if missing. The project's stricter check is correct for catching misconfigured contexts, but it means the Starlette `request` parameter is partially redundant (both the `request` kwarg and the context `"request"` key must be present).

**Gap:** The `request` in the context is a `TemplateRequest` shim, but the `request` kwarg to `render_template_response()` is a FastAPI `Request`. Starlette's `TemplateResponse` does `context.setdefault("request", request)` — so if the project passes `request` in context already, the Starlette kwarg `request` won't overwrite it. This is fine, but the Starlette `url_for` global reads `context["request"]`, which means it will receive the `TemplateRequest` not the FastAPI `Request`. Since `TemplateRequest.url_for()` delegates to `raw_request.url_for()`, the Starlette `url_for` will call `TemplateRequest.url_for()`, which works — but it needs `raw_request` set. This chain is fragile.

### 2.5 Debug metadata (`http.response.debug`) — Handled by Starlette

Since `render_template_response()` returns Starlette's own `_TemplateResponse`, debug metadata is sent correctly via Starlette's `_TemplateResponse.__call__()`. No custom debug code is needed in `jinja.py`.

### 2.6 `MakoUndefined` — Reasonable approximation

The `MakoUndefined` class ([jinja.py](../../server/fishtest/http/jinja.py) lines 42-50) overrides `__str__` and `__repr__` to return `"UNDEFINED"`. Jinja2's base `Undefined.__str__()` returns `""` (empty string). Making it return `"UNDEFINED"` matches Mako's `UNDEFINED` sentinel behavior.

**Nitpick:** Jinja2's `Undefined` also has `__iter__`, `__bool__`, and arithmetic operations that raise `UndefinedError`. `MakoUndefined` does not override these, so iterating over or doing arithmetic with an undefined variable will still raise. In Mako, `UNDEFINED` is a plain string, so `for x in UNDEFINED` would iterate over the characters. The divergence is low-risk because templates should not iterate over undefined variables.

### 2.7 Environment globals set before template load — Yes

`default_environment()` sets all globals/filters before returning the `Environment`. `default_templates()` then passes this to `Jinja2Templates(env=env)`, which calls `_setup_env_defaults()`. No templates are loaded until the first `get_template()` call. The environment is stable.

### 2.8 Summary Table (Jinja2 alignment)

| Feature | Starlette Reference | Project Implementation | Status |
|---------|-------------------|----------------------|--------|
| `Jinja2Templates(env=...)` constructor | `templating.py:90` | `jinja.py:default_templates()` | Correct |
| `url_for` via `@pass_context` | `templating.py:101-110` | Injected by Starlette | Correct |
| `context_processors` | `templating.py:142-143` | Not wired (explicit context) | Acceptable |
| `TemplateResponse` signature | `templating.py:117-148` | `render_template_response()` | Correct |
| `request` in context validation | `context.setdefault(...)` | Explicit `ValueError` | Stricter (OK) |
| Debug metadata | `_TemplateResponse.__call__` | Delegated to Starlette | Correct |
| `MakoUndefined` | N/A (custom) | `__str__` returns `"UNDEFINED"` | Reasonable |
| Stable environment before load | N/A (best practice) | Yes | Correct |

---

## 3. Mako `MakoTemplateResponse` Analysis

*Reviewer A (Architecture & Idiom)*

### 3.1 Mirror of `_TemplateResponse` — Faithful with one difference

Comparing [mako_new.py:MakoTemplateResponse](../../server/fishtest/http/mako_new.py) against [___starlette/starlette/templating.py:_TemplateResponse](../../___starlette/starlette/templating.py):

| Aspect | Starlette `_TemplateResponse` | `MakoTemplateResponse` | Match? |
|--------|-------------------------------|------------------------|--------|
| Stores `template` and `context` | Yes (`self.template`, `self.context`) | Yes | Yes |
| Renders via `template.render(context)` | `template.render(context)` (single dict) | `template.render(**context)` (kwargs) | Different (correct for Mako) |
| Inherits from `HTMLResponse` | Yes | Yes | Yes |
| Constructor params | `(template, context, status, headers, media_type, bg)` | `(template, context, *, options=TemplateResponseOptions)` | Pattern differs (see below) |
| Debug metadata in `__call__` | Yes | Yes | Yes (with semantic diff) |

**Render call difference:** Starlette passes `template.render(context)` (one dict arg to Jinja2's `Template.render`). Mako's `Template.render` expects `**kwargs`, so `template.render(**context)` is correct.

**Constructor pattern difference:** Instead of separate kwargs, `MakoTemplateResponse` uses a `TemplateResponseOptions` dataclass. This is a reasonable design choice that bundles HTTP-level options separately from template concerns. However, it means the constructor signature differs from Starlette's `_TemplateResponse`. For code that expects Starlette's signature, this could be surprising.

### 3.2 Debug metadata — Semantic difference in `request.extensions` access

Starlette's `_TemplateResponse.__call__`:
```python
request = self.context.get("request", {})
extensions = request.get("extensions", {})
if "http.response.debug" in extensions:
```

`MakoTemplateResponse.__call__`:
```python
request = self.context.get("request")
extensions = getattr(request, "extensions", None)
if isinstance(extensions, dict) and "http.response.debug" in extensions:
```

**Analysis:** Starlette assumes `request` is a dict-like (or the Starlette `Request`, which supports `.get()`). The project's `request` in context is a `TemplateRequest` dataclass, which does NOT have a `.get()` method. Using `getattr(request, "extensions", None)` is the correct adaptation.

**Minor issue:** `TemplateRequest` does not define an `extensions` attribute. So `getattr(request, "extensions", None)` will always return `None`, and the debug metadata path will never fire. For tests to use `http.response.debug`, they would need to set `extensions` on the `TemplateRequest` or use the raw `Request` in context instead.

> [!WARNING]
> The debug metadata path in `MakoTemplateResponse.__call__` is effectively dead code with the current `TemplateRequest` shim, because `TemplateRequest` has no `extensions` attribute. If debug/test hooks matter (as stated in the M9 iteration plan), this needs to be addressed.

### 3.3 `render_template_response()` in `mako_new.py` — Present but unused

Like the Jinja2 counterpart, `render_template_response()` in `mako_new.py` exists but is not called from the live `views.py` dispatch pipeline. The pipeline uses `render_template()` (string rendering) and wraps in `HTMLResponse`.

### 3.4 Summary Table (Mako alignment)

| Feature | Starlette `_TemplateResponse` | `MakoTemplateResponse` | Status |
|---------|-------------------------------|------------------------|--------|
| Stores `template` + `context` | Yes | Yes | Correct |
| Renders via engine's API | `template.render(context)` | `template.render(**context)` | Correct (Mako API) |
| Inherits `HTMLResponse` | Yes | Yes | Correct |
| Debug metadata (`__call__`) | `request.get("extensions", {})` | `getattr(request, "extensions", None)` | Adapted (correct, but dead code — see warning) |
| ASGI `__call__` signature | `(scope, receive, send)` | `(scope, receive, send)` | Correct |
| Constructors match Starlette | Positional args | `TemplateResponseOptions` | Different pattern (acceptable) |

---

## 4. Shared Helper Base (`template_helpers.py`) Nitpick

*Both reviewers*

### 4.1 Module scope — Well-scoped with clear ownership

[server/fishtest/http/template_helpers.py](../../server/fishtest/http/template_helpers.py) contains:
- Re-exports from `fishtest.util` (formatting, URL helpers)
- Template-specific helpers (`run_tables_prefix`, `clip_long`, `pdf_to_string`, `list_to_string`)
- Stats helpers (`t_conf`, `nelo_pentanomial_summary`, `is_elo_pentanomial_run`)
- View setup helpers (`results_pre_attrs`, `tests_run_setup`)
- A custom `urlencode` filter

**Reviewer A:** The helper set is well-chosen. Moving stats calculations (`t_conf`, `nelo_pentanomial_summary`) into helpers from templates is a clear win for readability and testability. The Elo/stats helpers use `LLRcalc.nelo_divided_by_nt` correctly.

**Reviewer B:** The re-exports from `fishtest.util` are acceptable — they avoid duplicating helper logic. The helpers module does not introduce rebase risk because it is a new file not in `views.py` or `api.py`.

### 4.2 `__all__` completeness

`__all__` lists 22 names. Cross-checking with the actual module contents:

- `diff_url_for_run` is defined but is NOT in `__all__` — **gap**.
- `clip_long` is defined and IS in `__all__` but is not registered in `jinja.py` globals — **gap** (unless unused in Jinja2 templates).

### 4.3 Asymmetry between Jinja2 globals and Mako/helper surface

`jinja.py:default_environment()` registers these globals that are NOT in `template_helpers.py`:

| Global | Source | Why not in helpers? |
|--------|--------|-------------------|
| `copy` | `import copy` | Python stdlib; Mako uses `<%! import copy %>` |
| `datetime` | `import datetime` | Python stdlib; Mako uses `<%! import datetime %>` |
| `math` | `import math` | Python stdlib; Mako uses `<%! import math %>` |
| `float` | builtin | Available in Mako without import |
| `urllib` | `urllib.parse` | Mako uses `<%! from urllib.parse import ... %>` |
| `fishtest` | `import fishtest` | Package reference for `fishtest.__version__` etc. |
| `gh` | `fishtest.github_api` | Used for GitHub API calls in templates |

**Assessment:** This is by design — Mako templates do their own imports in `<%! %>` blocks, while Jinja2 has no import statement and must receive everything via globals. The asymmetry is inherent to the engine difference and is not a bug. However, it means:
- When a new helper is needed, it must be added in TWO places for Jinja2 (helpers module + `jinja.py` globals).
- Mako templates just add `<%! import ... %>`.

This limits the "shared helper base" promise. The helpers are shared for computation logic, but the wiring is engine-specific.

### 4.4 Stats helpers placement

**Reviewer A:** `t_conf()`, `nelo_pentanomial_summary()`, and `is_elo_pentanomial_run()` arguably belong in a stats-specific module rather than a template-helpers module. They perform statistical computation, not template formatting. However, they are only used in templates, so placing them in `template_helpers.py` is pragmatic and reduces file count.

**Reviewer B:** Keeping them in template_helpers avoids adding yet another module. The stats computations use `LLRcalc.nelo_divided_by_nt` and `stat_util.Phi_inv` as authoritative sources, which is correct.

### 4.5 Lint/type status

Both `ruff check --select ALL` and `ty check` pass cleanly on `template_helpers.py`. No issues.

---

## 5. `template_renderer.py` Analysis

*Reviewer B (Rebase Safety & Ops)*

### 5.1 Engine switch mechanism — Functional but with module-level singleton

[server/fishtest/http/template_renderer.py](../../server/fishtest/http/template_renderer.py) uses:
```python
_STATE = _EngineState(engine=DEFAULT_ENGINE)
```

This is a mutable module-level singleton. `set_template_engine()` and `get_template_engine()` act on it.

**Risks:**
- In tests, changing `_STATE.engine` in one test affects subsequent tests unless reset. There is no `reset()` or context-manager interface.
- In multi-worker deployments, each process has its own `_STATE`, which is fine for Uvicorn workers (fork model), but could be surprising if anyone expects shared state.

**Recommendation:** Add a context manager or `reset` function for test safety:
```python
@contextmanager
def override_engine(engine: TemplateEngine):
    prev = _STATE.engine
    _STATE.engine = engine
    try:
        yield
    finally:
        _STATE.engine = prev
```

### 5.2 Eager construction at import time

```python
_MAKO_LOOKUP = mako_renderer.default_template_lookup()
_MAKO_NEW_LOOKUP = mako_new_renderer.default_template_lookup()
_JINJA_TEMPLATES = jinja_renderer.default_templates()
```

All three template engines are constructed at module import time. This means:
- Importing `template_renderer` always builds all three lookups, even if only one is used.
- If any template directory is missing (e.g., `templates_jinja2/` removed), the import fails.
- The Jinja2 `Environment` is created with `MakoUndefined`, and Starlette's `url_for` global is injected — all before any request is served.

**Assessment (Reviewer B):** This is acceptable for production (all three dirs exist). For tests, it means you cannot import `template_renderer` without having all template directories present. Not a blocker, but worth noting.

### 5.3 Unused/dead functions

| Function | Used by | Assessment |
|----------|---------|------------|
| `_jinja_template_exists()` | `assert_jinja_template_exists()` only | May be dead code — search codebase |
| `_mako_new_template_exists()` | `assert_mako_new_template_exists()` only | May be dead code — search codebase |
| `assert_jinja_template_exists()` | Unknown | Likely dead code |
| `assert_mako_new_template_exists()` | Unknown | Likely dead code |
| `render_template_dual()` | Parity tools (compare scripts) | Used — should stay |
| `render_template_legacy_mako()` | Unknown | May be dead code |
| `render_template_mako_new()` | Unknown | May be dead code |
| `render_template_jinja()` | Unknown | May be dead code |

**Reviewer B:** The `assert_*` and engine-specific render functions look like they were added for parity tooling and tests. If they are not imported anywhere, they should be either used or removed. Dead code increases maintenance burden and rebase noise.

### 5.4 `TemplateEngine` literal type

```python
TemplateEngine = Literal["mako", "mako_new", "jinja"]
```

Used consistently in `_EngineState`, `RenderedTemplate`, and the function signatures. This is correct and clean.

### 5.5 `template_engine_for()` ignores its argument

```python
def template_engine_for(_template_name: str) -> TemplateEngine:
    return _STATE.engine
```

The parameter `_template_name` is ignored (prefixed with `_`). The function always returns the global engine. This was likely intended to support per-template engine selection (e.g., "use Jinja2 for `base.mak` but Mako for `tests_view.mak`"). That feature is not implemented.

**Assessment:** The function signature is forward-looking but the implementation is a stub. If per-template selection is not planned, simplify to `get_template_engine()`.

### 5.6 Lint/type status

Both `ruff check --select ALL` and `ty check` pass cleanly.

---

## 6. Whole-Project Status Assessment

*Both reviewers*

### 6.1 What is done correctly

| Area | Status | Evidence |
|------|--------|----------|
| Pyramid runtime removed | Complete | No `pyramid` import in runtime code |
| Worker API parity | Complete | Contract tests cover all worker routes |
| UI parity (HTML rendering) | Complete | Template parity scripts pass for 25 templates |
| Async/blocking boundaries | Complete | All blocking work offloaded to threadpool |
| FastAPI glue maintainable | Complete | Clean `http/` package, 14 modules |
| `api.py` readable | Complete | 21 endpoints, linear flow, 1-hop |
| `views.py` readable | Complete | 30+ endpoints, central dispatch, linear flow |
| HTTP boundary extraction (M7) | Complete | `boundary.py` centralizes plumbing |
| Template parity (M8) | Complete | Legacy/new Mako + Jinja2 all pass normalized parity |
| Shared helper base (M8) | Complete | `template_helpers.py` used by both engines |
| Renderer selection (M8) | Complete | Python variable, no env flags |
| Metrics tooling (M8) | Complete | 3 metrics scripts, comparative metrics |
| Jinja2Templates adoption (M9) | Complete | `jinja.py` uses `Jinja2Templates(env=...)` correctly |
| Mako TemplateResponse wrapper (M9) | Complete | `MakoTemplateResponse` mirrors `_TemplateResponse` |
| ASGI risk documentation (M9) | Complete | Phase 3 in `3.9-ITERATION.md` |
| Lint/type cleanliness | Complete | `ruff check --select ALL` + `ty check` pass on all `http/` support modules |

**Reviewer A:** Code quality is high. The "mechanical port" philosophy has produced a codebase that is genuinely human-readable. The hop count is consistently ≤ 1 for API endpoints and ≤ 2 for UI endpoints (dispatch + view function). The helper call count stays within the ≤ 2 target.

**Reviewer B:** Rebase safety is well-preserved. The mechanical-port hotspots (`api.py`, `views.py`) are in upstream order. Legacy Mako templates are untouched. Parity scripts exist for routes, AST, and template output. The two-step landing strategy is sound.

### 6.2 What can be improved

#### 6.2.1 `TemplateResponse` not wired into the live pipeline

The main gap: [views.py](../../server/fishtest/http/views.py) lines 313-321 still use:
```python
rendered = await run_in_threadpool(
    render_template,
    template_name=renderer,
    context=build_template_context(request, session, context),
)
response = HTMLResponse(rendered.html, status_code=int(status_code))
```

This means:
- Templates are rendered to strings, then wrapped in a plain `HTMLResponse`.
- The response does NOT expose `.template` or `.context` for test/debug hooks.
- The `render_template_response()` functions in `jinja.py` and `mako_new.py` are unused dead code from the pipeline's perspective.

> [!IMPORTANT]
> The M9 decision log states: "any UI endpoint returning HTML must either return a TemplateResponse-style object or render in a threadpool with the same context rules." The current pipeline satisfies the second option (threadpool rendering), but does NOT satisfy the first option (TemplateResponse). The M9 definition of done says "Jinja2 rendering uses Starlette `Jinja2Templates` directly or a compatible wrapper with `TemplateResponse`". This is only partially met.

**Reviewer A's position:** Wire `render_template_response()` into the pipeline to complete M9.

**Reviewer B's position:** The current approach works and does not break parity. Wiring `TemplateResponse` into the pipeline touches `views.py` (a mechanical-port hotspot), which adds rebase risk. Prefer a non-intrusive approach.

#### 6.2.2 `ui_errors.py` does not use the unified renderer

[server/fishtest/http/ui_errors.py](../../server/fishtest/http/ui_errors.py) calls `render_template()` from `template_renderer` but then wraps in a raw `HTMLResponse`. It does not use the `TemplateResponse`-style path either. This is consistent with `views.py` but represents the same gap.

#### 6.2.3 Dual `url_for` paths

As noted in Section 2.2, there are two `url_for` mechanisms:
1. Starlette's `@pass_context url_for` injected into the Jinja2 environment.
2. `TemplateRequest.url_for()` available as `request.url_for()` in all templates.

These both work but could cause confusion. If a Jinja2 template calls `{{ url_for("route") }}` (Starlette global) and also `{{ request.url_for("route") }}` (TemplateRequest method), they should produce the same result, but they are resolved through different code paths.

#### 6.2.4 `MakoTemplateResponse` debug path is dead code

As noted in Section 3.2, `TemplateRequest` has no `extensions` attribute, so the debug metadata path in `MakoTemplateResponse.__call__()` never fires. To make this work:

- Option A: Add `extensions: dict = field(default_factory=dict)` to `TemplateRequest`.
- Option B: Check for the raw `Request` in context instead of `TemplateRequest`.
- Option C: Accept it as documentation-of-intent and mark it clearly.

#### 6.2.5 `template_parity_context.json` coverage

The parity scripts work but depend on a manually crafted context JSON file. Templates that require complex context (e.g., `tests_view.mak` with a full run object, `tests_stats.mak` with Elo data) may have incomplete or minimal test context, which limits parity confidence.

### 6.3 What is still wrong or risky

#### 6.3.1 No response-level parity test

Current parity tools compare **rendered HTML strings**. There is no test that:
- Compares HTTP response status codes between engines.
- Compares response headers (especially `Cache-Control`).
- Verifies that the session cookie is committed identically.
- Verifies that the `TemplateResponse` exposes `.template` and `.context`.

#### 6.3.2 `_dispatch_view` threadpool boundary is correct but not verified per-engine

The `_dispatch_view()` function in `views.py` renders templates via `run_in_threadpool(render_template, ...)`. The `render_template` function in `template_renderer.py` delegates to the active engine. This is correct, but there is no test that verifies each engine behaves identically when rendered through the full dispatch pipeline.

#### 6.3.3 `default_filters=["h"]` in legacy and new Mako but not documented explicitly

Both `mako.py` and `mako_new.py` create `TemplateLookup` with `strict_undefined=False`. The legacy lookup does NOT set `default_filters=["h"]`, while the new one DOES. This means:

- Legacy Mako: no default filter — templates must use `|h` explicitly or rely on `<%page expression_filter="h"/>`.
- New Mako: `default_filters=["h"]` — all expressions are HTML-escaped by default.

This difference is intentional (new Mako is a cleanup), but it means legacy-vs-new-Mako parity depends on all expressions being either explicitly escaped in legacy or safe by context. The parity scripts verify this, but the difference should be documented.

| Setting | Legacy Mako | New Mako | Jinja2 |
|---------|------------|----------|--------|
| Auto-escape | No default filter | `default_filters=["h"]` | `select_autoescape(["html", "xml"])` |
| Explicit no-escape | `\|n` | `\|n` | `\|safe` |
| Strict undefined | `False` | `False` | `MakoUndefined` (lenient) |

---

## 7. Suggestions

### 7.1 Fixing Issues

#### 7.1.1 Wire `TemplateResponse` into the pipeline (minimal change) (done - to be reviewed)

**File:** [server/fishtest/http/views.py](../../server/fishtest/http/views.py), lines 310-321

Instead of modifying `views.py` directly (rebase risk), add a thin adapter in `template_renderer.py` that returns an `HTMLResponse` with `.template` and `.context` attached:

```python
# In template_renderer.py
def render_template_to_response(
    *,
    template_name: str,
    context: Mapping[str, object],
    status_code: int = 200,
) -> HTMLResponse:
    """Render a template and return an HTMLResponse with debug attributes."""
    result = render_template(template_name=template_name, context=context)
    response = HTMLResponse(result.html, status_code=status_code)
    # Attach for test/debug visibility.
    response.template_name = template_name  # type: ignore[attr-defined]
    response.template_context = context  # type: ignore[attr-defined]
    return response
```

Then the `views.py` call site change is a single line swap:
```python
# Before
response = HTMLResponse(rendered.html, status_code=int(status_code))
# After
response = render_template_to_response(...)
```

**Effort:** 1h. **Risk:** Low (additive change, one call-site swap in `views.py`).

#### 7.1.2 Fix `MakoTemplateResponse` debug path (done - to be reviewed)

**File:** [server/fishtest/http/mako_new.py](../../server/fishtest/http/mako_new.py), lines 65-73

Add `extensions` support to `TemplateRequest` or use the raw request:

```python
# Option A: Check raw_request instead of TemplateRequest
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
    request = self.context.get("request")
    raw = getattr(request, "raw_request", None) or request
    extensions = getattr(raw, "extensions", None) or {}
    if isinstance(extensions, dict) and "http.response.debug" in extensions:
        await send(...)
    await super().__call__(scope, receive, send)
```

**Effort:** 30min. **Risk:** Low.

#### 7.1.3 Add `diff_url_for_run` to `__all__` (done - to be reviewed)

**File:** [server/fishtest/http/template_helpers.py](../../server/fishtest/http/template_helpers.py)

```python
# Add to __all__
"diff_url_for_run",
```

**Effort:** 5min.

### 7.2 Improving Code

#### 7.2.1 Add `override_engine` context manager for test safety (done - to be reviewed)

**File:** [server/fishtest/http/template_renderer.py](../../server/fishtest/http/template_renderer.py)

```python
from contextlib import contextmanager

@contextmanager
def override_engine(engine: TemplateEngine):
    """Temporarily override the template engine for testing."""
    prev = _STATE.engine
    _STATE.engine = engine
    try:
        yield
    finally:
        _STATE.engine = prev
```

**Effort:** 15min. **Impact:** Prevents test pollution.

#### 7.2.2 Remove or explicitly mark dead functions in `template_renderer.py` (done - to be reviewed)

The following functions should be either removed or annotated with their intended use:
- `render_template_legacy_mako()` — move to parity tools if only used there
- `render_template_mako_new()` — same
- `render_template_jinja()` — same
- `assert_jinja_template_exists()` — remove if unused
- `assert_mako_new_template_exists()` — remove if unused

**Effort:** 30min. **Impact:** Reduces module surface, makes intent clear.

#### 7.2.3 Simplify `template_engine_for()` (done - to be reviewed)

If per-template engine selection is not planned:

```python
# Before
def template_engine_for(_template_name: str) -> TemplateEngine:
    return _STATE.engine

# After (just use get_template_engine directly)
```

Or document the intent for future per-template selection with a `# TODO`.

#### 7.2.4 Lazy engine construction (done - to be reviewed)

Instead of eagerly constructing all three lookups at import time:

```python
from functools import cache

@cache
def _mako_lookup() -> TemplateLookup:
    return mako_renderer.default_template_lookup()

@cache
def _mako_new_lookup() -> TemplateLookup:
    return mako_new_renderer.default_template_lookup()

@cache
def _jinja_templates() -> Jinja2Templates:
    return jinja_renderer.default_templates()
```

This avoids import-time side effects and makes tests that import only one engine cheaper.

**Effort:** 30min. **Impact:** Test ergonomics, startup clarity.

#### 7.2.5 Document the dual `url_for` in architecture docs (done - to be reviewed)

Add a section to `2-ARCHITECTURE.md` or `3.9-ITERATION.md` explaining:
- Starlette-injected `url_for` in Jinja2 environment
- `TemplateRequest.url_for()` for Mako and explicit Jinja2 usage
- Why both exist and when to use which

**Effort:** 30min.

### 7.3 Improving Parity & Metrics Scripts

#### 7.3.1 Add response-level parity checks (done - to be reviewed)

Create a new script `WIP/tools/compare_template_response_parity.py` that:
- Renders each template through `render_template_to_response()` for each engine.
- Compares status codes, content length, and attached debug metadata.
- Verifies that `.template_name` and `.template_context` are set.

This goes beyond HTML string comparison to verify the response pipeline is identical.

**Effort:** 4h.

#### 7.3.2 Enrich `compare_template_parity.py` with attribute-level HTML comparison (done - to be reviewed)

Current normalization is whitespace-only:
```python
def normalize_html(html: str) -> str:
    value = _TAG_GAP_RE.sub("><", html)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()
```

Add DOM-level normalization:
- Parse with `html.parser` or `lxml.html`.
- Sort attributes alphabetically.
- Normalize self-closing tags.
- Compare semantic DOM tree, not string representation.

This would catch subtle attribute-ordering differences that string comparison misses.

**Effort:** 6h.

#### 7.3.3 Add template context coverage check (done - to be reviewed)

Create a script that:
- Parses each template for variable references (`${...}` in Mako, `{{ ... }}` in Jinja2).
- Compares against the context keys provided in `template_parity_context.json`.
- Reports variables that are referenced in templates but not present in test context.

This would identify templates that might silently render `UNDEFINED` or empty strings due to missing context.

**Effort:** 4h.

#### 7.3.4 Add rendering performance benchmark (done - to be reviewed)

Create `WIP/tools/templates_benchmark.py` that:
- Renders each template 100 times per engine.
- Reports median, p95, and p99 render times.
- Flags templates where Jinja2 is significantly slower/faster than Mako.

This is valuable for the eventual cutover decision.

**Effort:** 3h.

#### 7.3.5 Add `ruff`/`ty` check to parity tool scripts themselves (done - to be reviewed)

The parity tools under `WIP/tools/` use `# ruff: noqa: T201` for print statements but are not routinely lint-checked. Add a CI or Makefile target:

```bash
cd server && uv run ruff check --select ALL ../WIP/tools/*.py
cd server && uv run ty check ../WIP/tools/*.py
```

**Effort:** 30min.

#### 7.3.6 Improve `compare_jinja_mako_parity.py` to run without subprocess (done - to be reviewed)

Currently, `compare_jinja_mako_parity.py` shells out to `compare_template_parity.py` via `subprocess.call()`. This:
- Loses exit code granularity.
- Makes debugging harder.
- Adds subprocess overhead.

Refactor to import and call `compare_template_parity.main()` directly or extract its core logic into a shared function.

**Effort:** 1h.

---

## 8. Reviewer A vs Reviewer B: Points of Disagreement

### 8.1 Should `TemplateResponse` be wired into `views.py`?

**Reviewer A (Architecture):** Yes. The M9 definition of done explicitly mentions `TemplateResponse`. The response objects should expose `.template` and `.context` for test and debug hooks. This is a standard Starlette pattern and should be adopted.

**Reviewer B (Rebase Safety):** Not directly in `views.py`. The dispatch pipeline in `views.py` is a mechanical-port hotspot. Adding a new response type changes the return path and adds rebase risk. Prefer the adapter-in-`template_renderer.py` approach (Section 7.1.1) which keeps the `views.py` change to a single line.

**Resolution:** Use the adapter approach. Add `render_template_to_response()` in `template_renderer.py` (additive, no rebase risk) and swap one line in `views.py`. This satisfies both reviewers.

### 8.2 Should unused functions be removed from `template_renderer.py`?

**Reviewer A:** Yes, dead code violates the "lean" principle. Remove `assert_*`, `render_template_legacy_mako()`, etc.

**Reviewer B:** Keep them if parity tools or tests use them. Dead code in a support module is low risk. Removing code creates diff noise that complicates rebases.

**Resolution:** Search the codebase for usages. Remove only confirmed dead code. Mark uncertain functions with `# Used by: WIP/tools/...` comments.

### 8.3 Should `template_renderer.py` use lazy construction?

**Reviewer A:** Yes, eagerly constructing three engines at import time is wasteful and surprising.

**Reviewer B:** The eager construction guarantees that missing template directories are detected at startup (fail-fast). Lazy construction could mask configuration errors until the first template render.

**Resolution:** Keep eager construction for production safety. Add lazy construction as an opt-in for tests only (e.g., a `_LAZY_INIT` flag or a test-only factory).

### 8.4 Should context processors be adopted?

**Reviewer A:** Yes, eventually. Context processors would reduce boilerplate in `build_template_context()` and align with Starlette best practices.

**Reviewer B:** Not yet. Context processors add implicit behavior that is hard to audit during migration. Explicit context is safer and more readable for the current phase.

**Resolution:** Defer to post-M9. Document as a candidate for a future milestone.

---

## 9. Action Items (Prioritized)

### Critical (before M9 can be marked complete)

| # | Item | File(s) | Effort | Owner |
|---|------|---------|--------|-------|
| 1 | Wire `TemplateResponse`-style response into pipeline (adapter approach) | `template_renderer.py`, `views.py` (1-line swap) | 1h | Either |
| 2 | Fix `MakoTemplateResponse` debug path (use `raw_request`) | `mako_new.py` | 30min | A |

### High Priority (post-M9, before next milestone)

| # | Item | File(s) | Effort | Owner |
|---|------|---------|--------|-------|
| 3 | Add `override_engine` context manager | `template_renderer.py` | 15min | B |
| 4 | Audit and remove dead functions in `template_renderer.py` | `template_renderer.py` | 30min | B |
| 5 | Document dual `url_for` in architecture docs | `2-ARCHITECTURE.md` | 30min | A |
| 6 | Add `diff_url_for_run` to `__all__` | `template_helpers.py` | 5min | Either |
| 7 | Add response-level parity check script | `WIP/tools/` | 4h | B |

### Medium Priority (improvement candidates)

| # | Item | File(s) | Effort | Owner |
|---|------|---------|--------|-------|
| 8 | Add template context coverage check | `WIP/tools/` | 4h | B |
| 9 | Add rendering performance benchmark | `WIP/tools/` | 3h | Either |
| 10 | Enrich parity script with DOM-level normalization | `compare_template_parity.py` | 6h | A |
| 11 | Lint-check parity tools with `ruff --select ALL` | CI / Makefile | 30min | B |
| 12 | Refactor `compare_jinja_mako_parity.py` to avoid subprocess | `WIP/tools/` | 1h | Either |
| 13 | Document `default_filters` difference (legacy vs new Mako) | `3.9-ITERATION.md` or retired new Mako plan | 30min | A |

### Low Priority (future milestones)

| # | Item | File(s) | Effort | Owner |
|---|------|---------|--------|-------|
| 14 | Consider context processors for Jinja2 | `jinja.py`, `boundary.py` | 4h | A |
| 15 | Consider per-template engine selection in `template_engine_for()` | `template_renderer.py` | 2h | Either |
| 16 | Consider lazy engine construction (test-only) | `template_renderer.py` | 1h | B |

---

*Report generated by Claude Opus 4.6 after comprehensive analysis of WIP/docs, current implementation, Starlette reference code, and parity tooling. All code claims verified against source files.*
