"""Idempotent postcondition-checked cleanup executors for external projections."""

from __future__ import annotations

from typing import Protocol

from ragkb.contracts.lifecycle import CleanupExecutionResult
from ragkb.contracts.ports import DocumentProjectionPort
from ragkb.domain.errors import RAGError


class ProjectionInspectorPort(Protocol):
    def document_projection_exists(self, document_id: str) -> bool: ...


class ExternalProjectionCleanupExecutor:
    revision = "external-projection-cleanup:v1"

    def __init__(
        self,
        projection: DocumentProjectionPort,
        inspector: ProjectionInspectorPort,
        *,
        store_name: str,
    ) -> None:
        self.projection = projection
        self.inspector = inspector
        self.store_name = store_name

    def execute(self, document_id: str) -> CleanupExecutionResult:
        try:
            self.projection.delete_document_projection(document_id)
            exists = self.inspector.document_projection_exists(document_id)
        except RAGError:
            return CleanupExecutionResult(
                False, False, f"{self.store_name.upper()}_CLEANUP_UNAVAILABLE"
            )
        return CleanupExecutionResult(
            True,
            not exists,
            None if not exists else f"{self.store_name.upper()}_CLEANUP_POSTCONDITION_FAILED",
        )


class EmptyRedisDocumentCleanupExecutor:
    """Redis stores no unique document data; revisioned cache keys expire independently."""

    revision = "redis-no-unique-document-cleanup:v1"

    def execute(self, document_id: str) -> CleanupExecutionResult:
        return CleanupExecutionResult(bool(document_id), bool(document_id))
