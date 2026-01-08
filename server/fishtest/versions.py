"""Version constants shared across frontends.

Kept separate from Pyramid/FastAPI implementations so both can import these
constants without pulling in framework-specific dependencies.
"""

from __future__ import annotations

# Bump this when the worker protocol changes.
WORKER_VERSION = 307
