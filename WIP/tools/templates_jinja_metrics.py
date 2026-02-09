#!/usr/bin/env python3
# ruff: noqa: T201
"""Analyze Jinja2 templates and emit migration metrics.

Goal:
    Count statements/expressions and feature usage to track Jinja2 complexity.

Usage:
    python WIP/tools/templates_jinja_metrics.py --json
    python WIP/tools/templates_jinja_metrics.py --templates-dir /path/to/dir

Exit status:
    0 always (informational)
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES_DIR = REPO_ROOT / "server" / "fishtest" / "templates_jinja2"

STATEMENT_RE = re.compile(r"\{%")
EXPR_RE = re.compile(r"\{\{")

FEATURE_PATTERNS: dict[str, re.Pattern[str]] = {
    "extends": re.compile(r"\{%\s*extends\b"),
    "include": re.compile(r"\{%\s*include\b"),
    "import": re.compile(r"\{%\s*import\b"),
    "from": re.compile(r"\{%\s*from\b"),
    "macro": re.compile(r"\{%\s*macro\b"),
    "block": re.compile(r"\{%\s*block\b"),
    "filter_safe": re.compile(r"\|safe\b"),
    "filter_e": re.compile(r"\|e\b"),
    "filter_urlencode": re.compile(r"\|urlencode\b"),
}


@dataclass(frozen=True)
class TemplateStats:
    """Per-template metrics snapshot."""

    name: str
    path: str
    statements: int
    code_tags: int
    expressions: int
    lines: int
    score: int
    features: list[str]


@dataclass(frozen=True)
class Summary:
    """Summary totals for all templates."""

    templates: list[TemplateStats]
    totals: dict[str, int]


def _score(statements: int, code_tags: int, expressions: int) -> int:
    return statements * 3 + code_tags * 2 + expressions


def _extract_features(text: str) -> list[str]:
    found = [name for name, pat in FEATURE_PATTERNS.items() if pat.search(text)]
    return sorted(found)


def analyze_template(path: Path) -> TemplateStats:
    """Parse a Jinja2 template and compute metrics."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    statements = sum(1 for line in lines if STATEMENT_RE.search(line))
    expressions = len(EXPR_RE.findall(text))
    score = _score(statements, 0, expressions)
    return TemplateStats(
        name=path.name,
        path=str(path),
        statements=statements,
        code_tags=0,
        expressions=expressions,
        lines=len(lines),
        score=score,
        features=_extract_features(text),
    )


def summarize(stats: list[TemplateStats]) -> Summary:
    """Summarize metrics across templates."""
    totals = {
        "templates": len(stats),
        "statements": sum(item.statements for item in stats),
        "code_tags": sum(item.code_tags for item in stats),
        "expressions": sum(item.expressions for item in stats),
        "lines": sum(item.lines for item in stats),
        "score": sum(item.score for item in stats),
    }
    return Summary(templates=stats, totals=totals)


def _print_text(summary: Summary) -> None:
    print("Jinja2 template analysis")
    for item in summary.templates:
        print(
            f"- {item.name}: statements={item.statements}, code_tags={item.code_tags}, "
            f"expressions={item.expressions}, lines={item.lines}, score={item.score}",
        )
    print("Totals:")
    for key, value in summary.totals.items():
        print(f"  {key}: {value}")


def _threshold_failed(item: TemplateStats, args: argparse.Namespace) -> bool:
    return (
        (args.max_score is not None and item.score > args.max_score)
        or (args.max_statements is not None and item.statements > args.max_statements)
        or (
            args.max_expressions is not None and item.expressions > args.max_expressions
        )
        or (args.max_code_tags is not None and item.code_tags > args.max_code_tags)
    )


def main() -> int:
    """Run Jinja2 template metrics analysis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=DEFAULT_TEMPLATES_DIR,
        help="Template directory to analyze.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument("--max-score", type=int, default=None)
    parser.add_argument("--max-statements", type=int, default=None)
    parser.add_argument("--max-expressions", type=int, default=None)
    parser.add_argument("--max-code-tags", type=int, default=None)
    args = parser.parse_args()

    templates = sorted(args.templates_dir.glob("*.html.j2"))
    stats = [analyze_template(path) for path in templates]
    summary = summarize(stats)

    if args.json:
        payload = {
            "templates": [asdict(item) for item in summary.templates],
            "totals": summary.totals,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    _print_text(summary)

    failed = [item for item in summary.templates if _threshold_failed(item, args)]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
