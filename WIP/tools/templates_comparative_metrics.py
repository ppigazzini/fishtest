#!/usr/bin/env python3
"""Collect comparative template metrics for legacy Mako, new Mako, and Jinja2.

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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server" / "fishtest"

ENGINE_DIRS = {
    "mako_legacy": SERVER_ROOT / "templates",
    "mako_new": SERVER_ROOT / "templates_mako",
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
    engine: str
    totals: Totals


def iter_templates(path: Path) -> Iterable[Path]:
    return sorted(p for p in path.glob("*.mak") if p.is_file())


def score(statements: int, code_tags: int, expressions: int) -> int:
    return statements * 3 + code_tags * 2 + expressions


def max_nesting(
    lines: list[str], open_re: re.Pattern[str], close_re: re.Pattern[str]
) -> int:
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
    if engine.startswith("mako"):
        return len(re.findall(r"\|n\b", text))
    return len(re.findall(r"\|safe\b", text))


def collect(engine: str, path: Path) -> Summary:
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

    templates = list(iter_templates(path))
    totals = {
        "templates": len(templates),
        "statements": 0,
        "code_tags": 0,
        "expressions": 0,
        "lines": 0,
        "score": 0,
        "max_nesting_any": 0,
        "avg_max_nesting": 0.0,
        "script_interpolation_lines": 0,
        "unescaped_occurrences": 0,
    }

    max_depths: list[int] = []
    for template in templates:
        text = template.read_text(encoding="utf-8")
        lines = text.splitlines()
        statements = sum(1 for line in lines if statement_re.search(line))
        code_tags = sum(1 for line in lines if code_tag_re and code_tag_re.search(line))
        expressions = len(expr_re.findall(text))

        totals["statements"] += statements
        totals["code_tags"] += code_tags
        totals["expressions"] += expressions
        totals["lines"] += len(lines)
        totals["score"] += score(statements, code_tags, expressions)

        depth = max_nesting(lines, open_re, close_re)
        max_depths.append(depth)

        totals["script_interpolation_lines"] += script_interpolation_lines(
            lines, expr_re
        )
        totals["unescaped_occurrences"] += unescaped_count(text, engine)

    totals["max_nesting_any"] = max(max_depths) if max_depths else 0
    totals["avg_max_nesting"] = sum(max_depths) / len(max_depths) if max_depths else 0.0

    return Summary(engine=engine, totals=Totals(**totals))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    summaries = [collect(name, path) for name, path in ENGINE_DIRS.items()]

    if args.json:
        payload = {item.engine: asdict(item.totals) for item in summaries}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for item in summaries:
        totals = item.totals
        print(item.engine)
        print(f"  templates: {totals.templates}")
        print(f"  lines: {totals.lines}")
        print(f"  statements: {totals.statements}")
        print(f"  code_tags: {totals.code_tags}")
        print(f"  expressions: {totals.expressions}")
        print(f"  score: {totals.score}")
        print(f"  max_nesting_any: {totals.max_nesting_any}")
        print(f"  avg_max_nesting: {totals.avg_max_nesting:.2f}")
        print(f"  script_interpolation_lines: {totals.script_interpolation_lines}")
        print(f"  unescaped_occurrences: {totals.unescaped_occurrences}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
