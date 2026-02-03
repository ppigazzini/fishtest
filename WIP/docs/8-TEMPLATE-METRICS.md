# Template metrics (legacy Mako vs new Mako vs Jinja2)

Date: 2026-02-05

This document records the current template metrics and the scripts used to generate them.
All commands run from [WIP/docs](WIP/docs) and invoke the template-focused tooling under [WIP/tools](../tools).

## Scripts (run from WIP/docs)

Legacy Mako metrics:

```
/home/usr00/_git/fishtest-fastapi/server/.venv/bin/python ../tools/templates_mako_metrics.py \
  --templates-dir ../../server/fishtest/templates --json
```

New Mako metrics:

```
/home/usr00/_git/fishtest-fastapi/server/.venv/bin/python ../tools/templates_mako_metrics.py \
  --templates-dir ../../server/fishtest/templates_mako --json
```

Jinja2 metrics:

```
/home/usr00/_git/fishtest-fastapi/server/.venv/bin/python ../tools/templates_jinja_metrics.py \
  --templates-dir ../../server/fishtest/templates_jinja2 --json
```

Comparative metrics (nesting, script interpolation, escaping heuristics):

```
/home/usr00/_git/fishtest-fastapi/server/.venv/bin/python ../tools/templates_comparative_metrics.py --json
```

## Metrics snapshot (2026-02-05)

Totals (templates, lines, statements, code tags, expressions, score):

- Legacy Mako: templates=26, lines=5549, statements=349, code_tags=77, expressions=491, score=1692
- New Mako: templates=26, lines=5529, statements=347, code_tags=73, expressions=491, score=1678
- Jinja2: templates=26, lines=5538, statements=716, code_tags=N/A, expressions=489, score=2637

Heuristic complexity metrics:

- Max nesting (any template): legacy Mako=5, new Mako=5, Jinja2=6
- Avg max nesting: legacy Mako=1.58, new Mako=1.58, Jinja2=2.46
- Script interpolation lines: legacy Mako=56, new Mako=56, Jinja2=55
- Unescaped occurrences (|n / |safe): legacy Mako=14, new Mako=14, Jinja2=0

Notes:
- The score is the same formula used by the analysis scripts: statements*3 + code_tags*2 + expressions.
- Code tags are not applicable to Jinja2 templates and are reported as N/A in summaries.
- Script interpolation lines count template expressions inside <script> blocks.
- Unescaped occurrences are counts of |n (Mako) or |safe (Jinja2) usage.
