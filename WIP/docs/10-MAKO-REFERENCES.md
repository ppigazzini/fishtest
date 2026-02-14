# Mako references (legacy parity templates)

Date: 2026-02-09

This doc captures the minimal, authoritative guidance for the legacy Mako
templates that remain in-tree for parity tooling. Runtime rendering is Jinja2
only; Mako is used by WIP/tools to compare output and to keep rebase safety with
upstream Pyramid.

For active template authoring and updates, use
[WIP/docs/2.3-JINJA2.md](WIP/docs/2.3-JINJA2.md) and
[WIP/docs/11.2-MAKO-JINJA2-RULES.md](WIP/docs/11.2-MAKO-JINJA2-RULES.md).

## Status (authoritative)

- Legacy Mako templates live in [server/fishtest/templates](server/fishtest/templates).
- Legacy Mako is read-only and used only by parity scripts under WIP/tools.
- Runtime template rendering uses Jinja2 only.

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

## Project usage (parity only)

- Rendering for parity runs uses `TemplateLookup` in WIP/tools.
- Default escaping behavior must remain compatible with legacy output.
- Parity scripts normalize HTML before diffing; whitespace-only diffs are ignored.

## Project-specific rules

- Keep legacy templates unchanged except when upstream changes require updates.
- Use `strict_undefined=True` only in tests; parity runs stay lenient.
- Do not add new Mako renderers or new template directories.

## Tooling

- HTML parity: [WIP/tools/compare_template_parity.py](WIP/tools/compare_template_parity.py)
- Response parity: [WIP/tools/compare_template_response_parity.py](WIP/tools/compare_template_response_parity.py)
- Context coverage: [WIP/tools/template_context_coverage.py](WIP/tools/template_context_coverage.py)
- Metrics: [WIP/docs/11.3-TEMPLATES-METRICS.md](WIP/docs/11.3-TEMPLATES-METRICS.md)
