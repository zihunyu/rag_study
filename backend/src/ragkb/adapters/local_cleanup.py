"""Controlled local-file cleanup with an explicit deletion postcondition."""

from __future__ import annotations

from typing import Protocol

from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.contracts.lifecycle import CleanupExecutionResult


class LocalContentRepositoryPort(Protocol):
    def list_local_content_lineage(self, document_id: str) -> tuple[tuple[str, str], ...]: ...


class LocalOriginalCleanupExecutor:
    revision = "local-content-lineage-cleanup:g3-v2"
    allowed_partitions = frozenset({"original", "artifacts", "quarantine", "temp"})

    def __init__(self, storage: LocalFileStorage, repository: LocalContentRepositoryPort) -> None:
        self.storage = storage
        self.repository = repository

    def execute(self, document_id: str) -> CleanupExecutionResult:
        lineage = self.repository.list_local_content_lineage(document_id)
        if any(partition not in self.allowed_partitions for partition, _ in lineage):
            return CleanupExecutionResult(False, False, "LOCAL_LINEAGE_PARTITION_BLOCKED")
        try:
            for partition, key in lineage:
                self.storage.delete(partition, key)
            postcondition = all(
                not self.storage.exists(partition, key) for partition, key in lineage
            )
        except OSError:
            return CleanupExecutionResult(False, False, "LOCAL_FILE_DELETE_FAILED")
        return CleanupExecutionResult(
            executed=True,
            postcondition_met=postcondition,
            error_code=None if postcondition else "LOCAL_FILE_POSTCONDITION_FAILED",
        )
