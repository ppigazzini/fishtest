"""Minimal Pyramid compatibility stubs for unit tests.

The legacy fishtest unit tests historically depend on Pyramid interfaces
(e.g. `pyramid.testing.DummyRequest`, `pyramid.httpexceptions`).

This FastAPI port intentionally does NOT depend on Pyramid at runtime.
These stubs exist so the legacy modules under `server/fishtest/` and
unit tests under `server/tests/` remain importable in CI.

Only the tiny surface area used by the tests is implemented.
"""

from __future__ import annotations

from . import testing  # re-export for `from pyramid import testing`

__all__ = ["testing"]
