"""Local content store with partitioning, containment and atomic writes."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
from collections.abc import AsyncIterable
from hashlib import sha256
from pathlib import Path

from ragkb.contracts.ports import StorageIntegrityError, StreamWriteResult

ALLOWED_PARTITIONS = frozenset({"original", "artifacts", "quarantine", "temp", "audit"})


class StoragePathError(ValueError):
    """Raised when a caller attempts to escape the configured storage partition."""


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._quota_lock = threading.Lock()
        self._reserved_quarantine_bytes = 0

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

    @staticmethod
    def _directory_size(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(
            item.stat().st_size
            for item in path.rglob("*")
            if item.is_file() and not item.name.endswith(".uploading")
        )

    def _reserve_quarantine(
        self,
        target: Path,
        *,
        requested_bytes: int,
        quota_bytes: int,
    ) -> int:
        with self._quota_lock:
            partition_root = self.root / "quarantine"
            self.cleanup_stale_uploads()
            existing_target = target.stat().st_size if target.is_file() else 0
            committed = max(0, self._directory_size(partition_root) - existing_target)
            if committed + self._reserved_quarantine_bytes + requested_bytes > quota_bytes:
                raise StorageIntegrityError("UPLOAD_QUARANTINE_QUOTA_EXCEEDED")
            self._reserved_quarantine_bytes += requested_bytes
            return requested_bytes

    def cleanup_stale_uploads(self, max_age_seconds: float = 3600) -> int:
        # Streams expire after 300 s; uncompleted upload sessions after 24 h.
        # Never touch original/artifact partitions, symlinks, or external paths.
        root = (self.root / "quarantine").resolve()
        cutoff = time.time() - max_age_seconds
        removed = 0
        for path in root.rglob("*"):
            if removed >= 100:
                break
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            try:
                expiry = cutoff if path.name.endswith(".uploading") else time.time() - 86400
                if path.stat().st_mtime < expiry:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    def _grow_quarantine_reservation(
        self,
        target: Path,
        reservation: int,
        required: int,
        quota_bytes: int,
    ) -> int:
        if required <= reservation:
            return reservation
        additional = required - reservation
        with self._quota_lock:
            partition_root = self.root / "quarantine"
            existing_target = target.stat().st_size if target.is_file() else 0
            committed = max(0, self._directory_size(partition_root) - existing_target)
            if committed + self._reserved_quarantine_bytes + additional > quota_bytes:
                raise StorageIntegrityError("UPLOAD_QUARANTINE_QUOTA_EXCEEDED")
            self._reserved_quarantine_bytes += additional
        return required

    def _release_quarantine(self, reservation: int) -> None:
        with self._quota_lock:
            self._reserved_quarantine_bytes = max(0, self._reserved_quarantine_bytes - reservation)

    async def write_stream(
        self,
        partition: str,
        key: str,
        chunks: AsyncIterable[bytes],
        *,
        max_bytes: int,
        quota_bytes: int,
        content_length: int | None = None,
    ) -> StreamWriteResult:
        if partition != "quarantine":
            raise StoragePathError("stream writes are restricted to quarantine")
        if max_bytes < 1 or quota_bytes < 1:
            raise StorageIntegrityError("UPLOAD_STREAM_LIMIT_INVALID")
        if content_length is not None and (content_length < 0 or content_length > max_bytes):
            raise StorageIntegrityError("DOC_SIZE_LIMIT")
        target = self._safe_path(partition, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        initial_reservation = content_length if content_length is not None else max_bytes
        reservation = await asyncio.to_thread(
            self._reserve_quarantine,
            target,
            requested_bytes=initial_reservation,
            quota_bytes=quota_bytes,
        )
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".uploading", dir=target.parent
            )
        except Exception:
            self._release_quarantine(reservation)
            raise
        temporary = Path(temporary_name)
        digest = sha256()
        size = 0
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise StorageIntegrityError("UPLOAD_STREAM_CHUNK_INVALID")
                    size += len(chunk)
                    if size > max_bytes:
                        raise StorageIntegrityError("DOC_SIZE_LIMIT")
                    reservation = await asyncio.to_thread(
                        self._grow_quarantine_reservation,
                        target, reservation, size, quota_bytes
                    )
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
                await asyncio.to_thread(handle.flush)
                await asyncio.to_thread(os.fsync, handle.fileno())
            await asyncio.to_thread(os.replace, temporary, target)
            return StreamWriteResult(target, size, digest.hexdigest())
        finally:
            temporary.unlink(missing_ok=True)
            self._release_quarantine(reservation)

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
