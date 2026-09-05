"""Upload session contract for the local quarantine workflow."""

from __future__ import annotations

from dataclasses import dataclass

from ragkb.domain.state_machines import UploadSessionState


@dataclass(frozen=True)
class UploadSession:
    id: str
    tenant_id: str
    space_id: str
    filename: str
    expected_size: int
    expected_sha256: str
    declared_mime: str
    state: UploadSessionState
    quarantine_key: str
    original_key: str | None
    detected_format: str | None
    detected_mime: str | None
    document_id: str | None
    document_version_id: str | None
    target_document_id: str | None
    target_document_row_version: int | None
    job_id: str | None
    error_code: str | None
    row_version: int
    created_at: float | None = None


class IdempotencyConflictError(ValueError):
    pass


class OptimisticConcurrencyError(RuntimeError):
    pass


class ResourceNotFoundError(KeyError):
    pass
