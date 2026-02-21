# Report: Jinja2 migration status (Milestone 10, Iteration 10)

Date: 2026-02-08
Scope: analyze WIP/docs, ___starlette repo templates docs/tests, ___jinja repo docs/samples, current refactor status, and produce an exhaustive, parity-safe change map toward idiomatic Starlette + Jinja2 (Python 3.14).

## Sources reviewed

WIP/docs (authoritative project context):
- 3.10-ITERATION.md (Milestone 10 plan and progress)
- 3-MILESTONES.md (roadmap and constraints)
- 11.1-JINJA2-CONTEXT-CONTRACTS.md (explicit template context contract)
- 2.3-JINJA2.md (migration plan and best practices)
- 7-STARLETTE-REFERENCES.md (Starlette guidance)
- 9-JINJA2-REFERENCES.md (Jinja2 guidance)
- 11.3-TEMPLATES-METRICS.md (template metrics and parity tools)

___starlette repo:
- docs/templates.md (templating API and guidance)
- tests/test_templates.py (expected TemplateResponse behavior)

___jinja repo:
- docs/templates.rst (Jinja2 template design guide, autoescape, macros, scoping)
- docs/switching.rst (Mako to Jinja2 differences, no embedded Python)
- docs/tricks.rst (practical patterns: active menu, cycle, inheritance)
- docs/integration.rst (Babel extraction, template scanning)

Code (current refactor status):
- server/fishtest/http/jinja.py
- server/fishtest/http/template_renderer.py
- server/fishtest/http/template_helpers.py
- server/fishtest/http/boundary.py
- server/fishtest/http/views.py
- server/fishtest/http/api.py

## Key facts from Starlette and Jinja2 repos

Starlette templating (docs/templates.md, tests/test_templates.py):
- Jinja2Templates signature: Jinja2Templates(directory, context_processors=None, **env_options)
- Must pass either directory or env, not both.
- TemplateResponse(request, name, context=...) requires request in context.
- url_for is automatically available in templates when using Jinja2Templates.
- Context processors are sync only; async processors are not supported.
- TemplateResponse in tests exposes response.template and response.context.
- Asynchronous template rendering is supported by Jinja2 but Starlette recommends keeping templates free of I/O and doing all I/O in the view.

Jinja2 templating (docs/templates.rst, switching.rst, tricks.rst):
- Any file extension can be a template; autoescape can be configured by extension.
- No embedded Python in templates; move logic to Python or filters/globals.
- Inheritance uses extends + blocks; macros replace Mako defs.
- Includes default to with context; imports default to without context (unless explicitly overridden).
- Scoping rules: set in loops does not escape scope unless using namespace objects.
- Autoescape: enable for HTML; use |safe only for trusted HTML; prefer Markup for safe strings.
- Whitespace control: trim_blocks, lstrip_blocks, and -%} for local control.
- Useful idioms: loop.cycle for alternating rows, set active menu in child templates, required blocks for enforcement.
- Mako-like syntax can be configured but embedded Python is not supported.

## Current refactor status (code analysis)

Runtime templating and environment
- server/fishtest/http/jinja.py defines a custom Jinja2 Environment with:
  - autoescape for html/xml/j2
  - MakoUndefined to mimic legacy UNDEFINED behavior
  - helpers registered as filters and globals (fishtest, gh, urllib, math, helpers)
- Jinja2Templates is created from the custom Environment (env=...)
- render_template renders via templates.get_template(...).render(**context)
- render_template_response exists but is not used by views

Template rendering path (UI)
- server/fishtest/http/views.py calls render_template_to_response
- server/fishtest/http/template_renderer.py uses Jinja2Templates + template.render
  - calls jinja.render_template_response and returns a TemplateResponse with debug attributes
  - shares the real FastAPI request plus TemplateRequest shim in the context
- build_template_context (server/fishtest/http/boundary.py) injects:
  - request (TemplateRequest shim), static_url, url_for
  - csrf_token, current_user, theme, pending_users_count, flash, urls
  - explicit base context required by base layout
- rendering is off the event loop via run_in_threadpool in views

Parity tooling and constraints
- WIP/docs/3.10-ITERATION.md requires:
  - Jinja2-only runtime, legacy Mako read-only for parity scripts
  - minimal changes in http/api.py and http/views.py
  - parity tools in WIP/tools
  - explicit base context and per-template context contracts
- WIP/docs/11.3-TEMPLATES-METRICS.md shows Jinja2 has higher statement/nesting metrics
- WIP/docs/3.10-ITERATION.md notes remaining missing keys in coverage and parity diffs

Summary status vs Milestone 10 target
- Jinja2 runtime is active and Mako runtime wiring was removed.
- Legacy Mako templates remain for parity tooling.
- Base context is explicit and shared.
- UI rendering now uses TemplateResponse so Starlette tests and helpers see `response.template`/`response.context`.
- Templates have been renamed to .html.j2 in templates_jinja2 (done).
- Parity tooling is operational, but diffs remain for many templates.

## All ways to move from legacy Mako to idiomatic Starlette Jinja2

A) Syntax and structure conversion (template-side)
- Replace Mako inheritance (<%inherit>) with Jinja extends + blocks.
- Replace Mako defs (<%def>) with Jinja macros and imports.
- Replace Mako control lines (% for, % if) with Jinja block tags.
- Replace Mako ${...} with Jinja {{ ... }} and filters.
- Replace Mako % include / namespace with Jinja include/import.
- Use Jinja block scoping (scoped/required) instead of Mako-specific layout patterns.
Status: done for all templates in server/fishtest/templates_jinja2 (.html.j2). Additional cleanup: replaced document.title scripts with block titles and removed direct request access in templates.

B) Remove embedded Python from templates
- Move inline Python into view-level context builders or helpers.
- Replace Mako inline code blocks with:
  - precomputed values in context
  - helper functions registered as Jinja filters or globals
- Avoid data access from templates; keep them declarative.
Status: done for all templates in server/fishtest/templates_jinja2 (.html.j2). Jinja2 templates contain no embedded Python blocks; request access was removed and context-driven values are used.

C) Adopt Starlette-first rendering patterns
- Prefer Jinja2Templates + TemplateResponse to ensure url_for, context processors, and response.template/context in tests.
- Keep rendering sync and off the event loop (threadpool boundary preserved).
- Use request in context only through the TemplateRequest shim and explicit helpers.
Status: done – UI rendering now uses TemplateResponse via the shared Jinja2Templates environment; request shims remain in the context and rendering stays sync off the event loop.

D) Explicit context and safety
- Build explicit per-template context (see 11.1-JINJA2-CONTEXT-CONTRACTS.md).
- Preformat strings (labels, times, URLs) in views/helpers.
- Use |tojson for JS payloads and avoid string interpolation in scripts.
- Use autoescape; use |safe only for audited HTML.
- Use StrictUndefined in tests to detect missing keys.
Status: done for the shared base context; helpers now precompute labels/URLs and `|tojson` is used for JS payloads so templates stay declarative.

E) Jinja2 idioms for maintainability
- Use macros for repeated UI fragments (tables, badges, labels, forms).
- Use includes for shared partials with deliberate context visibility.
- Use loop.cycle for alternating row styles.
- Use namespace objects to carry values out of loops when needed.
- Use required blocks to enforce layout overrides.
Status: done for templates in server/fishtest/templates_jinja2 (.html.j2). Existing macros/includes are retained (run tables, stats helpers), block titles are used for page titles, and templates avoid implicit request access. No further structural changes were made to preserve parity.

F) Naming and file conventions
- Rename templates from .mak to .html.j2. (done)
- Align autoescape configuration with new extensions. (done via server/fishtest/http/jinja.py autoescape list)

G) Reduce legacy Mako coupling
Status: done — runtime Jinja environment now only supplies Jinja-safe globals/filters and templates exclusively use Jinja super()/blocks instead of ${self.*} constructs.

H) Parity-friendly migration tactics
Status: ongoing — logic already moved into helpers, IDs/fields unchanged, parity scripts now run via WIP/tools, but some diff normalization work remains (see Phase 3 tasks).
Latest parity (2026-02-08): normalized matches only for `elo_results` and `pagination`; all other templates differ. Context coverage still missing `static_url` for login, signup, sprt_calc, tests, tests_live_elo, tests_run, tests_view, and user; `nn_upload` is missing `cc0_url/nn_stats_url/testing_guidelines_url/upload_url`; `nns` is missing `filters/network_name_filter/pages/user_filter`; `tests` is missing `height/max_height/min_height/nps_m`.

## Exhaustive change list needed to reach Milestone 10 goals

1) Template runtime alignment
- Switch runtime rendering to Starlette TemplateResponse (instead of HTMLResponse) so url_for and response.template/context are standard and tests align with Starlette expectations.
- Ensure TemplateResponse(request=..., name=..., context=...) is used consistently with keyword args.

2) Template file naming and autoescape
- Rename templates in server/fishtest/templates_jinja2 to .html.j2. (done)
- Update autoescape configuration to cover the new extensions. (done)
- Update template name references in views and parity tooling (mapping layer in tools). (done)

3) Explicit context contract enforcement
- For every template, align view contexts to 11.1-JINJA2-CONTEXT-CONTRACTS.md.
- Fix remaining missing keys flagged by template context coverage tools (noted in 3.10-ITERATION.md).
- Ensure shared base context always includes: urls, csrf_token, current_user, theme, pending_users_count, flash, static_url, url_for.

4) Remove request-side dependencies from templates
- Replace any implicit request usage with explicit context fields (prebuilt URLs, labels, booleans).
- Ensure templates do not access request internals beyond url_for/static_url helpers.

5) Legacy Mako removal from runtime (already mostly done)
- Verify no runtime imports of Mako or Mako renderers remain in http/api.py or http/views.py.
- Keep legacy templates read-only and used only by parity scripts in WIP/tools.

6) Jinja2 idiomatic refactors (template-side)
- Replace heavy inline logic blocks with helper-prepared data.
- Convert repeated layout fragments to macros/includes.
- Reduce template nesting depth where possible (metrics show higher nesting in Jinja2).
- Remove any unsafe HTML insertion; use |safe only on trusted, pre-sanitized strings.

7) Parity tooling and metrics
- Run WIP/tools/template_context_coverage.py --engine jinja for each template touched.
- Run WIP/tools/compare_template_parity.py and compare_template_response_parity.py for all touched endpoints.
- Update WIP/docs/11.3-TEMPLATES-METRICS.md if metrics materially change.
- Track whitespace-minified parity scores from compare_template_parity.py to separate formatting drift from content drift.

8) Views and API stability constraints (must remain minimal)
- Keep changes in server/fishtest/http/views.py and server/fishtest/http/api.py small and localized.
- Avoid large refactors or route reshaping; preserve parity with legacy behaviors.

9) External helper parity (non-negotiable)
- Maintain parity with helper functions registered in the Jinja2 environment:
  - template_helpers functions, fishtest, gh, urllib, math.
- Keep filter mapping stable (urlencode, split, string), or update templates and parity scripts in lockstep.
- Preserve semantics of any helper outputs used in templates (e.g., results_pre_attrs, nelo_pentanomial_summary).

10) Test and tooling alignment
- Ensure TemplateResponse-based tests (response.template/response.context) remain valid.
- Ensure parity scripts use the same template name mapping as runtime once extensions change.

## Constraints that must not be violated

- Legacy Mako templates remain untouched in server/fishtest/templates for parity and rebase safety.
- Rendering stays off the event loop (threadpool boundary preserved).
- Small, surgical changes only in http/api.py and http/views.py to keep parity metrics stable.
- External function parity for template rendering must be preserved (helpers and globals).
- ASCII-only sources where practical; use HTML entities for non-ASCII display.

## Recommended next steps (parity-safe, minimal changes)

1) Decide whether to switch UI rendering to TemplateResponse now or after template renames.
2) Fix remaining missing context keys reported by template_context_coverage tooling.
3) Convert high-logic templates to helper-driven contexts (actions, tasks, machines, tests_*), preserving existing IDs and form fields.
4) Verify parity scripts with the .html.j2 mapping layer after the rename.

## Code-verified status and remaining tasks (as of 2026-02-08)

Verified against current code:
- UI rendering now uses Starlette TemplateResponse via render_template_to_response.
- The context includes the real FastAPI request plus TemplateRequest under template_request.
- Base context includes urls, csrf_token, current_user, theme, pending_users_count, flash, static_url, url_for.
- Jinja2 environment autoescape covers html/xml/j2 and registers helper filters/globals.
- UI rendering remains off the event loop via run_in_threadpool.

Remaining tasks to complete Milestone 10:
- Close template context coverage gaps (static_url and any page-specific keys/filters reported by the coverage tool).
- Resolve remaining template parity diffs beyond elo_results and pagination (raw + normalized) by aligning context and helper outputs.
- Re-run parity tooling (template_context_coverage, compare_template_parity, compare_template_response_parity) after each context fix.
- Update WIP/docs/11.3-TEMPLATES-METRICS.md if template metrics change during parity fixes (latest snapshot is 2026-02-08).
- Confirm parity tooling uses the same template name mapping as runtime for .html.j2 templates.

End of report.
