"""Local content store with partitioning, containment and atomic writes."""

from __future__ import annotations

import os
import tempfile
from hashlib import sha256
from pathlib import Path

from ragkb.contracts.ports import StorageIntegrityError

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

    def path_for(self, partition: str, key: str) -> Path:
        return self._safe_path(partition, key)

    def exists(self, partition: str, key: str) -> bool:
        return self._safe_path(partition, key).is_file()

    def size(self, partition: str, key: str) -> int:
        return self._safe_path(partition, key).stat().st_size

    def delete(self, partition: str, key: str) -> bool:
        target = self._safe_path(partition, key)
        if not target.exists():
            return False
        target.unlink()
        return True

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def promote(
        self,
        source_partition: str,
        source_key: str,
        target_key: str,
        expected_sha256: str,
    ) -> Path:
        source = self._safe_path(source_partition, source_key)
        target = self._safe_path("original", target_key)
        if not source.is_file():
            if target.is_file():
                if self._sha256(target) != expected_sha256.casefold():
                    raise StorageIntegrityError("DOC_ORIGINAL_HASH_MISMATCH")
                return target
            raise FileNotFoundError(source)
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        return target
