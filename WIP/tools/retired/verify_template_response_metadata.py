#!/usr/bin/env python3
# ruff: noqa: T201
"""Verify TemplateResponse debug metadata for a simple template."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from fishtest.http import template_renderer  # noqa: E402

DEFAULT_CONTEXT = REPO_ROOT / "WIP" / "tools" / "template_parity_context.json"
TEMPLATE_NAME = "pagination.html.j2"


@dataclass(frozen=True)
class MetadataCheck:
    """Simple metadata verification result."""

    template: str
    has_template: bool
    has_context: bool


def _request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("localhost", 80),
        "scheme": "http",
    }
    return Request(scope)


def _load_pages(context_path: Path) -> list[dict[str, object]]:
    data = json.loads(context_path.read_text(encoding="utf-8"))
    pagination = data.get("pagination.mak", {})
    return list(pagination.get("pages", []))


def main() -> int:
    """Render a template and verify debug metadata fields."""
    request = _request()
    pages = _load_pages(DEFAULT_CONTEXT)
    context = {
        "request": request,
        "pages": pages,
    }
    response = template_renderer.render_template_to_response(
        request=request,
        template_name=TEMPLATE_NAME,
        context=context,
    )
    check = MetadataCheck(
        template=TEMPLATE_NAME,
        has_template=hasattr(response, "template"),
        has_context=hasattr(response, "context"),
    )
    print(check)
    if not (check.has_template and check.has_context):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
