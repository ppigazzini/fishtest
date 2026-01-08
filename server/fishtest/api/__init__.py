"""FastAPI API routers (machine-facing endpoints).

This package used to expose the legacy Pyramid `fishtest.api` module.
Some internal code/tests still import `WORKER_VERSION` from here, so we
re-export it from the canonical location.
"""

from fishtest.versions import WORKER_VERSION

__all__ = [
    "WORKER_VERSION",
]
