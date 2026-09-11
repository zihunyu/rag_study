"""Storage port for directory manifests and recoverable upload intents."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol


class DirectorySyncLedgerPort(Protocol):
    path: Path

    def get(self, key: str) -> dict[str, Any]: ...
    def put(self, key: str, value: dict[str, Any]) -> None: ...
    def snapshots(self, scope: str) -> Sequence[dict[str, Any]]: ...
    def lock(self, scope: str) -> AbstractContextManager[Any]: ...
