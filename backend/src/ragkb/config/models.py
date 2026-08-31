"""Data structures that deliberately never contain secret values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SecretStatus:
    """Presence metadata for a named secret environment variable."""

    name: str
    configured: bool
    source: str
    priority: str
    blocking_gate: str
    required_when: str


@dataclass(frozen=True)
class LoadedConfiguration:
    """User and effective configuration without any secret material."""

    repository_root: Path
    user_path: Path
    user: Mapping[str, Any]
    effective: Mapping[str, Any]
    stubbed_paths: frozenset[str]
    schema_errors: tuple[dict[str, str], ...]
    secret_statuses: tuple[SecretStatus, ...]
