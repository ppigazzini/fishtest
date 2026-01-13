"""Runtime settings for the FastAPI server.

This module centralizes environment parsing and derived runtime flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT_DEPTH: Final[int] = 3


def env_int(name: str, *, default: int) -> int:
    """Parse an environment variable as an integer, with a fallback default."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parents[REPO_ROOT_DEPTH]


def default_static_dir() -> Path:
    """Return the default static directory path for `/static` mounting."""
    return repo_root() / "server" / "fishtest" / "static"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Derived runtime settings for the FastAPI server process."""

    port: int
    primary_port: int
    is_primary_instance: bool

    @classmethod
    def from_env(cls) -> AppSettings:
        """Build settings from environment variables."""
        port = env_int("FISHTEST_PORT", default=-1)
        primary_port = env_int("FISHTEST_PRIMARY_PORT", default=-1)

        # Match Pyramid behavior: if the port number cannot be determined,
        # assume the instance is primary for backward compatibility.
        if port < 0 or primary_port < 0:
            is_primary_instance = True
        else:
            is_primary_instance = port == primary_port

        return cls(
            port=port,
            primary_port=primary_port,
            is_primary_instance=is_primary_instance,
        )
