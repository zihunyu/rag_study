"""OpenAPI v1 request and response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateUploadSessionRequest(StrictModel):
    filename: str = Field(min_length=1, max_length=255)
    expected_size: int = Field(ge=0)
    expected_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    declared_mime: str = Field(min_length=1, max_length=255)


class UploadSessionResponse(StrictModel):
    upload_session_id: str
    space_id: str
    filename: str
    state: str
    upload_path: str
    row_version: int
    real_acceptance: bool = False


class UploadSessionStatusResponse(StrictModel):
    upload_session_id: str
    state: str
    row_version: int
    document_id: str | None = None
    document_version_id: str | None = None
    job_id: str | None = None
    error_code: str | None = None


class CompleteUploadResponse(StrictModel):
    upload_session_id: str
    document_id: str
    document_version_id: str
    job_id: str
    status: str
    real_acceptance: bool


class AbortUploadResponse(StrictModel):
    upload_session_id: str
    state: str
    row_version: int


class SpaceResponse(StrictModel):
    id: str
    tenant_id: str
    name: str
    status: str


class DocumentResponse(StrictModel):
    id: str
    tenant_id: str
    source_id: str
    external_key: str
    state: str
    current_version_id: str | None
    row_version: int


class DocumentVersionResponse(StrictModel):
    id: str
    tenant_id: str
    document_id: str
    version_no: int
    content_sha256: str
    original_key: str
    mime_type: str
    processing_state: str
    publication_state: str
    parser_revision: str | None


class JobResponse(StrictModel):
    id: str
    operation: str
    state: str
    attempt: int
    max_attempts: int
    cancel_requested: bool
    error_code: str | None


class HealthResponse(StrictModel):
    status: str
    runtime: str
    real_service_acceptance: bool


class ErrorResponse(StrictModel):
    code: str
    message: str
    request_id: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)
