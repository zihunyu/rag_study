"""Local content store with partitioning, containment and atomic writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

ALLOWED_PARTITIONS = frozenset({"original", "artifacts", "quarantine", "temp", "audit"})


class StoragePathError(ValueError):
    """Raised when a caller attempts to escape the configured storage partition."""


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def ensure_layout(self) -> None:
        for partition in sorted(ALLOWED_PARTITIONS):
            (self.root / partition).mkdir(parents=True, exist_ok=True)

    def _safe_path(self, partition: str, key: str) -> Path:
        if partition not in ALLOWED_PARTITIONS:
            raise StoragePathError(f"unsupported storage partition: {partition}")
        if not key or "\x00" in key:
            raise StoragePathError("storage key must be non-empty and contain no NUL")
        candidate_key = Path(key)
        if candidate_key.is_absolute():
            raise StoragePathError("absolute storage keys are forbidden")
        partition_root = (self.root / partition).resolve()
        candidate = (partition_root / candidate_key).resolve()
        try:
            candidate.relative_to(partition_root)
        except ValueError as error:
            raise StoragePathError("storage key escapes its partition") from error
        return candidate

    def write_bytes(self, partition: str, key: str, content: bytes) -> Path:
        target = self._safe_path(partition, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def read_bytes(self, partition: str, key: str) -> bytes:
        return self._safe_path(partition, key).read_bytes()
