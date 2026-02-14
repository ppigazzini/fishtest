#!/usr/bin/env python3
# ruff: noqa: T201, E402
"""Generate a parity diff summary for Mako vs Jinja2 templates.

Goal:
    Render templates with both engines using the parity context and emit a JSON
    summary that includes:
    - raw/normalized/minified equality
    - minified similarity score (SequenceMatcher ratio)
    - output lengths per engine
    - small normalized diff snippets for quick inspection

Usage:
    python WIP/tools/compare_template_parity_summary.py
    python WIP/tools/compare_template_parity_summary.py --templates
    tests_view.html.j2,tests.html.j2
    python WIP/tools/compare_template_parity_summary.py --output
    WIP/tools/template_parity_diff_summary.json
    python WIP/tools/compare_template_parity_summary.py --catalog-order

Exit status:
    0 if the summary is generated
    1 on missing template or render error
"""

from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from WIP.tools import compare_template_parity as parity

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
DEFAULT_OUTPUT = REPO_ROOT / "WIP" / "tools" / "template_parity_diff_summary.json"

CATALOG_TEMPLATES: list[str] = [
    "base.mak",
    "elo_results.mak",
    "pagination.mak",
    "actions.mak",
    "contributors.mak",
    "login.mak",
    "machines.mak",
    "nn_upload.mak",
    "nns.mak",
    "notfound.mak",
    "rate_limits.mak",
    "run_table.mak",
    "run_tables.mak",
    "signup.mak",
    "sprt_calc.mak",
    "tasks.mak",
    "tests.mak",
    "tests_finished.mak",
    "tests_live_elo.mak",
    "tests_run.mak",
    "tests_stats.mak",
    "tests_user.mak",
    "tests_view.mak",
    "user.mak",
    "user_management.mak",
    "workers.mak",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context",
        type=Path,
        default=DEFAULT_CONTEXT,
        help="Path to template context JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to JSON summary output.",
    )
    parser.add_argument(
        "--templates",
        type=str,
        default="",
        help="Comma-separated list of template names.",
    )
    parser.add_argument(
        "--catalog-order",
        action="store_true",
        help="Use the 2.3-JINJA2.md catalog order for templates.",
    )
    parser.add_argument(
        "--max-snippets",
        type=int,
        default=3,
        help="Max diff snippets per template.",
    )
    parser.add_argument(
        "--snippet-window",
        type=int,
        default=60,
        help="Characters of context to include around a diff snippet.",
    )
    return parser.parse_args()


def _resolve_templates(args: argparse.Namespace) -> list[str]:
    if args.templates:
        return [
            parity._logical_name(item.strip())  # noqa: SLF001
            for item in args.templates.split(",")
            if item.strip()
        ]
    if args.catalog_order:
        return CATALOG_TEMPLATES
    return parity._collect_templates(  # noqa: SLF001
        left_dir=parity.DEFAULT_MAKO_DIR,
        right_dir=parity.DEFAULT_JINJA_DIR,
        left_engine="mako",
        right_engine="jinja",
        names=None,
    )


def _build_context(
    name: str,
    context_map: dict[str, dict[str, Any]],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    context = dict(defaults)
    decoded = parity._decode_special(context_map.get(name, {}))  # noqa: SLF001
    if isinstance(decoded, dict):
        context.update(decoded)
    context = parity._with_request_stub(context)  # noqa: SLF001
    return parity._with_helpers(context)  # noqa: SLF001


def _diff_snippets(
    left_norm: str,
    right_norm: str,
    *,
    max_snippets: int,
    window: int,
) -> list[dict[str, str]]:
    matcher = SequenceMatcher(None, left_norm, right_norm)
    snippets: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        left_snip = left_norm[max(i1 - window, 0) : min(i2 + window, len(left_norm))]
        right_snip = right_norm[max(j1 - window, 0) : min(j2 + window, len(right_norm))]
        snippets.append({"tag": tag, "left": left_snip, "right": right_snip})
        if len(snippets) >= max_snippets:
            break
    return snippets


def main() -> int:
    """Run the parity summary generator."""
    args = _parse_args()
    templates = _resolve_templates(args)

    if not templates:
        print("No templates to compare.")
        return 0

    context_map, defaults = parity._load_context_bundle(args.context)  # noqa: SLF001
    mako_lookup = parity.TemplateLookup(
        directories=[str(parity.DEFAULT_MAKO_DIR)],
        input_encoding="utf-8",
        output_encoding=None,
        strict_undefined=False,
    )
    jinja_env = parity._build_jinja_env(parity.DEFAULT_JINJA_DIR)  # noqa: SLF001

    results: list[dict[str, Any]] = []

    for name in templates:
        if name == "base.mak":
            results.append(
                {
                    "template": name,
                    "note": "skipped (base template not compared by parity tool)",
                },
            )
            continue

        context = _build_context(name, context_map, defaults)
        try:
            left_html = parity._render_engine(  # noqa: SLF001
                engine="mako",
                name=name,
                context=context,
                mako_lookup=mako_lookup,
                jinja_env=None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: mako render {name}: {exc}")
            return 1

        try:
            right_html = parity._render_engine(  # noqa: SLF001
                engine="jinja",
                name=name,
                context=context,
                mako_lookup=None,
                jinja_env=jinja_env,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: jinja render {name}: {exc}")
            return 1

        left_norm = parity.normalize_html(left_html)
        right_norm = parity.normalize_html(right_html)
        left_min = parity.minify_html(left_html)
        right_min = parity.minify_html(right_html)

        results.append(
            {
                "template": name,
                "raw_equal": left_html == right_html,
                "normalized_equal": left_norm == right_norm,
                "minified_equal": left_min == right_min,
                "minified_score": SequenceMatcher(None, left_min, right_min).ratio(),
                "left_len": len(left_html),
                "right_len": len(right_html),
                "diff_snippets": _diff_snippets(
                    left_norm,
                    right_norm,
                    max_snippets=args.max_snippets,
                    window=args.snippet_window,
                ),
            },
        )

    args.output.write_text(
        json.dumps(results, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
