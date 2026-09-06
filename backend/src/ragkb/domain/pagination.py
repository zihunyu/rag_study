"""Keyset pages retain the last scanned position even when authorization removes rows."""

from dataclasses import dataclass
from typing import Any

PageKey = tuple[int, str]


@dataclass(frozen=True)
class RepositoryPage:
    items: list[dict[str, Any]]
    next_key: PageKey | None
