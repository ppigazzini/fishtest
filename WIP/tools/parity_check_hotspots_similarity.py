#!/usr/bin/env python3
# ruff: noqa: T201
"""Parity helper: crude similarity metrics for mechanical-port hotspots.

Goal: keep diffs small and localized. This script is intentionally simple: it
prints normalized line counts and a difflib similarity ratio for key spec↔http
files. It is not a correctness checker; it flags accidental large-churn edits.

Metrics reported:
- non-empty line counts for spec vs http files
- similarity ratio from difflib.SequenceMatcher

Usage:
    python WIP/parity_check_hotspots_similarity.py
    python WIP/parity_check_hotspots_similarity.py --show

Exit status:
    0 always (informational)
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PAIRS: list[tuple[str, Path, Path]] = [
    (
        "UI views",
        REPO_ROOT / "server" / "fishtest" / "views.py",
        REPO_ROOT / "server" / "fishtest" / "http" / "views.py",
    ),
    (
        "API routes",
        REPO_ROOT / "server" / "fishtest" / "api.py",
        REPO_ROOT / "server" / "fishtest" / "http" / "api.py",
    ),
]


def _read_norm_lines(path: Path) -> list[str]:
    # Normalize lightly: strip trailing whitespace and drop empty lines.
    # (Do NOT drop comments; comment churn is still churn.)
    lines = path.read_text().splitlines()
    return [ln.rstrip() for ln in lines if ln.strip()]


def main() -> int:
    """Print basic similarity metrics for key spec↔http hotspots."""
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--show",
        action="store_true",
        help="Show a unified diff (can be large).",
    )
    args = ap.parse_args()

    for label, spec, http in PAIRS:
        if not spec.exists() or not http.exists():
            print(f"{label}: missing file(s): {spec} {http}")
            continue

        a = _read_norm_lines(spec)
        b = _read_norm_lines(http)

        ratio = difflib.SequenceMatcher(a=a, b=b).ratio()
        print(f"{label}:")
        print(f"  spec lines (non-empty): {len(a)}")
        print(f"  http lines (non-empty): {len(b)}")
        print(f"  similarity ratio: {ratio:.4f}")

        if args.show:
            diff = difflib.unified_diff(
                a,
                b,
                fromfile=str(spec.relative_to(REPO_ROOT)),
                tofile=str(http.relative_to(REPO_ROOT)),
                lineterm="",
            )
            print("\n".join(diff))
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
