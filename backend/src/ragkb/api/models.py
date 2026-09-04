"""OpenAPI v1 request and response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class DocumentQualityResponse(StrictModel):
    document_version_id: str
    source_format: str
    parser_revision: str
    node_count: int
    locator_coverage: float
    issue_codes: list[str]
    disposition: str
    real_acceptance: bool


class SecurityProjectionRequest(StrictModel):
    visibility: Literal["TENANT", "RESTRICTED"]
    classification_level: int = Field(ge=0, le=3)
    acl_scope_tokens: list[str] = Field(default_factory=list, max_length=100)
    valid_to_epoch: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_acl(self) -> SecurityProjectionRequest:
        normalized = [item.strip() for item in self.acl_scope_tokens if item.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("ACL scope tokens must be unique and non-empty")
        if self.visibility == "RESTRICTED" and not normalized:
            raise ValueError("restricted visibility requires ACL scope tokens")
        if self.visibility == "TENANT" and normalized:
            raise ValueError("tenant visibility cannot carry ACL scope tokens")
        self.acl_scope_tokens = normalized
        return self


class DocumentReviewRequest(StrictModel):
    decision: Literal["APPROVED", "NEEDS_REWORK", "REJECTED"]
    comment: str = Field(default="", max_length=2000)
    security_projection: SecurityProjectionRequest | None = None

    @model_validator(mode="after")
    def approved_requires_security(self) -> DocumentReviewRequest:
        if self.decision == "APPROVED" and self.security_projection is None:
            raise ValueError("approved reviews require a security projection")
        return self


class DocumentReviewResponse(StrictModel):
    review_id: str
    document_version_id: str
    reviewer_id: str
    decision: str
    comment: str
    quality_revision: str
    security_revision: str | None = None
    security_projection: dict[str, Any] | None = None
    real_acceptance: bool


class JobResponse(StrictModel):
    id: str
    operation: str
    state: str
    attempt: int
    max_attempts: int
    cancel_requested: bool
    error_code: str | None


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=2000)
    space_id: str | None = None
    limit: int | None = Field(default=None, ge=1, le=50)


class SearchHitResponse(StrictModel):
    chunk_id: str
    document_id: str
    document_version_id: str
    text: str
    display_text: str
    retrieval_text: str
    generation_context: str
    locator: dict[str, Any]
    fused_score: float
    rerank_position: int
    channels: list[str]
    parent_chunk_id: str | None = None
    parent_text: str | None = None


class SearchResponse(StrictModel):
    request_id: str
    observed_security_watermark: int
    hits: list[SearchHitResponse]
    real_acceptance: bool
    degraded: bool
    warnings: list[str]


class AskRequest(StrictModel):
    question: str = Field(min_length=1, max_length=4000)


class CitationResponse(StrictModel):
    evidence_id: str
    source_url: str
    locator: dict[str, Any]


class AskResponse(StrictModel):
    rag_run_id: str
    status: str
    answer: str | None
    citations: list[CitationResponse]
    warnings: list[str]
    verified: bool
    real_acceptance: bool


class EvidenceSourceResponse(StrictModel):
    evidence_id: str
    text: str
    locator: dict[str, Any]


class FeedbackRequest(StrictModel):
    rating: int = Field(ge=1, le=5)
    reason_code: str = Field(min_length=1, max_length=64)
    comment: str = Field(default="", max_length=2000)


class FeedbackResponse(StrictModel):
    rag_run_id: str
    accepted: bool
    index_generation_id: str
    retrieval_revision: str
    prompt_revision: str
    model_revision: str


class RollbackRequest(StrictModel):
    version_id: str = Field(min_length=1)


class PermissionUpdateRequest(StrictModel):
    target_acl_revision: int = Field(ge=1)
    required_watermark: int = Field(ge=0)
    observed_watermark: int = Field(ge=0)
    projection_ok: bool = True


class LifecycleResponse(StrictModel):
    document_id: str
    active_version_id: str | None
    lifecycle_state: str
    acl_revision: int
    visible: bool
    tombstoned: bool
    row_version: int


class DeletionResponse(StrictModel):
    document_id: str
    lifecycle_state: str
    visible: bool
    cleanup: dict[str, str]


class AuditEventResponse(StrictModel):
    sequence: int
    action: str
    resource_id: str
    trace_id: str
    governance_revision: str
    previous_hash: str
    event_hash: str


class HealthResponse(StrictModel):
    status: str
    runtime: str
    real_service_acceptance: bool
    dependencies: dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)


class ErrorResponse(StrictModel):
    code: str
    message: str
    request_id: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsResponse(StrictModel):
    revision: str
    event_count: int
    events_by_severity: dict[str, int]
    queue_by_state: dict[str, int]
    adapter: str
    otel_export_performed: bool
    prometheus_export_performed: bool
    simulated: bool
    real_acceptance: bool
    rag_tracing: dict[str, Any]


class EvidenceIndexRequest(StrictModel):
    category: str = Field(min_length=1, max_length=64)
    revision: str = Field(min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceIndexResponse(StrictModel):
    evidence_id: str
    category: str
    revision: str
    content_hash: str
    simulated: bool = True
    real_acceptance: bool = False


class GovernanceRegisterRequest(StrictModel):
    category: Literal["RISK", "EXCEPTION"]
    title: str = Field(min_length=1, max_length=300)
    owner: str = Field(min_length=1, max_length=100)
    state: Literal["OPEN", "ACCEPTED", "CLOSED"]
    revision: str = Field(min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GovernanceRegisterResponse(StrictModel):
    record_id: str
    category: str
    title: str
    owner: str
    state: str
    revision: str
    metadata: dict[str, Any]
    simulated: bool


class PilotCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    feature_flag: str = Field(min_length=1, max_length=100)


class PilotResponse(StrictModel):
    pilot_id: str
    name: str
    state: str
    feature_flag: str
    blockers: list[str]
    revision: int
    simulated: bool
    real_acceptance: bool


class GovernanceSignoffRequest(StrictModel):
    role: str = Field(min_length=1, max_length=64)
    decision: Literal["APPROVE", "VETO"]
    comment: str = Field(default="", max_length=2000)


class ReadinessResponse(StrictModel):
    scope_id: str
    state: str
    blockers: list[str]
    simulated: bool
    real_acceptance: bool


class RolloutBatchResponse(StrictModel):
    batch_id: str
    pilot_id: str
    ordinal: int
    percentage: int
    state: str
    simulated: bool


class PilotRollbackRequest(StrictModel):
    trigger: str = Field(min_length=1, max_length=500)


class UATCaseCreateRequest(StrictModel):
    pilot_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    steps: list[str] = Field(min_length=1)
    expected: list[str] = Field(min_length=1)


class UATEvidenceReference(StrictModel):
    category: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class UATResultRequest(StrictModel):
    result: Literal["PASSED", "FAILED", "BLOCKED"]
    evidence: list[UATEvidenceReference] = Field(default_factory=list)
    step_results: list[str] = Field(default_factory=list)


class UATCaseResponse(StrictModel):
    case_id: str
    pilot_id: str
    title: str
    steps: list[str]
    expected: list[str]
    result: str
    evidence: list[dict[str, str]]
    step_results: list[str]
    simulated: bool
    row_version: int
    created_at: float
    updated_at: float


class DefectCreateRequest(StrictModel):
    scope_type: Literal["pilot", "uat", "observation"]
    scope_id: str = Field(min_length=1)
    severity: Literal["P0", "P1", "P2", "P3"]
    title: str = Field(min_length=1, max_length=300)


class DefectResponse(StrictModel):
    defect_id: str
    scope_type: str
    scope_id: str
    severity: str
    title: str
    state: str
    simulated: bool
    row_version: int = 1
    created_at: float | None = None
    updated_at: float | None = None


class ObservationCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)


class ObservationMetricsRequest(StrictModel):
    metrics: dict[str, float]


class ObservationResponse(StrictModel):
    window_id: str
    name: str
    starts_at: float
    ends_at: float
    state: str
    metrics: dict[str, float]
    simulated: bool
    real_acceptance: bool
    row_version: int


class IncidentCreateRequest(StrictModel):
    severity: Literal["P0", "P1", "P2", "P3"]
    title: str = Field(min_length=1, max_length=300)


class IncidentResponse(StrictModel):
    incident_id: str
    window_id: str
    severity: str
    title: str
    state: str
    simulated: bool
    row_version: int = 1
    created_at: float | None = None
    updated_at: float | None = None


class FinalAcceptanceResponse(StrictModel):
    window_id: str
    status: str
    blockers: list[str]
    synthetic_readiness_state: str
    simulated: bool
    real_acceptance: bool
    generator_revision: str
