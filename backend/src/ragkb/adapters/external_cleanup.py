"""Idempotent postcondition-checked cleanup executors for external projections."""

from __future__ import annotations

from typing import Protocol

from redis.exceptions import RedisError

from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
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


class RedisDocumentCleanupExecutor:
    """Invalidate shared verified-answer caches after deletion and verify the postcondition."""

    revision = "redis-document-cache-cleanup:v2"

    def __init__(self, redis: RedisCacheRateLimitAdapter) -> None:
        self.redis = redis

    def execute(self, document_id: str) -> CleanupExecutionResult:
        if not document_id:
            return CleanupExecutionResult(False, False, "REDIS_DOCUMENT_ID_REQUIRED")
        try:
            client = self.redis._connected()
            pattern = self.redis._key("verified-answer", "*")
            keys = tuple(client.scan_iter(match=pattern, count=500))
            if keys:
                client.delete(*keys)
            remaining = next(iter(client.scan_iter(match=pattern, count=1)), None)
        except RedisError:
            return CleanupExecutionResult(False, False, "REDIS_CLEANUP_UNAVAILABLE")
        return CleanupExecutionResult(
            True,
            remaining is None,
            None if remaining is None else "REDIS_CLEANUP_POSTCONDITION_FAILED",
        )
