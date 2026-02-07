#!/usr/bin/env python3
"""Collect comparative template metrics for legacy Mako and Jinja2.

Goal:
    Produce a single JSON snapshot comparing totals and complexity signals across
    all template engines to track drift during migration.

Usage:
    python WIP/tools/templates_comparative_metrics.py --json
    python WIP/tools/templates_comparative_metrics.py --templates-dir /path/to/dir

Exit status:
    0 always (informational)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server" / "fishtest"

ENGINE_DIRS = {
    "mako_legacy": SERVER_ROOT / "templates",
    "jinja2": SERVER_ROOT / "templates_jinja2",
}

MAKO_STATEMENT_RE = re.compile(r"^\s*%")
MAKO_CODE_TAG_RE = re.compile(r"<%")
MAKO_EXPR_RE = re.compile(r"\$\{")
MAKO_OPEN_RE = re.compile(r"^\s*%\s*(if|for|try|while|with)\b")
MAKO_CLOSE_RE = re.compile(r"^\s*%\s*end(if|for|try|while|with)\b")

JINJA_STATEMENT_RE = re.compile(r"\{%")
JINJA_EXPR_RE = re.compile(r"\{\{")
JINJA_OPEN_RE = re.compile(r"\{%\s*(if|for|with|macro|block)\b")
JINJA_CLOSE_RE = re.compile(r"\{%\s*end(if|for|with|macro|block)\b")

SCRIPT_OPEN_RE = re.compile(r"<script\b", re.IGNORECASE)
SCRIPT_CLOSE_RE = re.compile(r"</script>", re.IGNORECASE)


@dataclass(frozen=True)
class Totals:
    """Aggregate metrics for one template engine."""

    templates: int
    statements: int
    code_tags: int
    expressions: int
    lines: int
    score: int
    max_nesting_any: int
    avg_max_nesting: float
    script_interpolation_lines: int
    unescaped_occurrences: int


@dataclass(frozen=True)
class Summary:
    """Summary of totals for a single engine."""

    engine: str
    totals: Totals


def iter_templates(path: Path, *, engine: str) -> Iterable[Path]:
    """Yield template files for an engine directory."""
    pattern = "*.mak" if engine.startswith("mako") else "*.html.j2"
    return sorted(p for p in path.glob(pattern) if p.is_file())


def score(statements: int, code_tags: int, expressions: int) -> int:
    """Compute a weighted complexity score."""
    return statements * 3 + code_tags * 2 + expressions


def max_nesting(
    lines: list[str],
    open_re: re.Pattern[str],
    close_re: re.Pattern[str],
) -> int:
    """Return maximum nesting depth for a template."""
    depth = 0
    max_depth = 0
    for line in lines:
        if open_re.search(line):
            depth += 1
            max_depth = max(max_depth, depth)
        if close_re.search(line):
            depth = max(0, depth - 1)
    return max_depth


def script_interpolation_lines(lines: list[str], expr_re: re.Pattern[str]) -> int:
    """Count script lines that interpolate template expressions."""
    count = 0
    in_script = False
    for line in lines:
        if SCRIPT_OPEN_RE.search(line):
            in_script = True
        if in_script and expr_re.search(line):
            count += 1
        if SCRIPT_CLOSE_RE.search(line):
            in_script = False
    return count


def unescaped_count(text: str, engine: str) -> int:
    """Count explicit unescaped output markers per engine."""
    if engine.startswith("mako"):
        return len(re.findall(r"\|n\b", text))
    return len(re.findall(r"\|safe\b", text))


def collect(engine: str, path: Path) -> Summary:
    """Compute metrics for a single engine directory."""
    if engine.startswith("mako"):
        statement_re = MAKO_STATEMENT_RE
        code_tag_re = MAKO_CODE_TAG_RE
        expr_re = MAKO_EXPR_RE
        open_re = MAKO_OPEN_RE
        close_re = MAKO_CLOSE_RE
    else:
        statement_re = JINJA_STATEMENT_RE
        code_tag_re = None
        expr_re = JINJA_EXPR_RE
        open_re = JINJA_OPEN_RE
        close_re = JINJA_CLOSE_RE

    templates = list(iter_templates(path, engine=engine))
    templates_count = len(templates)
    statements_total = 0
    code_tags_total = 0
    expressions_total = 0
    lines_total = 0
    score_total = 0
    script_interpolation_total = 0
    unescaped_total = 0

    max_depths: list[int] = []
    for template in templates:
        text = template.read_text(encoding="utf-8")
        lines = text.splitlines()
        statements = sum(1 for line in lines if statement_re.search(line))
        code_tags = sum(1 for line in lines if code_tag_re and code_tag_re.search(line))
        expressions = len(expr_re.findall(text))

        statements_total += statements
        code_tags_total += code_tags
        expressions_total += expressions
        lines_total += len(lines)
        score_total += score(statements, code_tags, expressions)

        depth = max_nesting(lines, open_re, close_re)
        max_depths.append(depth)

        script_interpolation_total += script_interpolation_lines(
            lines,
            expr_re,
        )
        unescaped_total += unescaped_count(text, engine)

    max_nesting_any = max(max_depths) if max_depths else 0
    avg_max_nesting = sum(max_depths) / len(max_depths) if max_depths else 0.0

    return Summary(
        engine=engine,
        totals=Totals(
            templates=templates_count,
            statements=statements_total,
            code_tags=code_tags_total,
            expressions=expressions_total,
            lines=lines_total,
            score=score_total,
            max_nesting_any=max_nesting_any,
            avg_max_nesting=avg_max_nesting,
            script_interpolation_lines=script_interpolation_total,
            unescaped_occurrences=unescaped_total,
        ),
    )


def _write_line(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def main() -> int:
    """Emit comparative metrics for legacy Mako and Jinja2."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    summaries = [collect(name, path) for name, path in ENGINE_DIRS.items()]

    if args.json:
        payload = {item.engine: asdict(item.totals) for item in summaries}
        _write_line(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for item in summaries:
        totals = item.totals
        _write_line(item.engine)
        _write_line(f"  templates: {totals.templates}")
        _write_line(f"  lines: {totals.lines}")
        _write_line(f"  statements: {totals.statements}")
        _write_line(f"  code_tags: {totals.code_tags}")
        _write_line(f"  expressions: {totals.expressions}")
        _write_line(f"  score: {totals.score}")
        _write_line(f"  max_nesting_any: {totals.max_nesting_any}")
        _write_line(f"  avg_max_nesting: {totals.avg_max_nesting:.2f}")
        _write_line(
            f"  script_interpolation_lines: {totals.script_interpolation_lines}",
        )
        _write_line(f"  unescaped_occurrences: {totals.unescaped_occurrences}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
