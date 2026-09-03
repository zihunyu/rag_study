"""Versioned G1-G4-local FastAPI/OpenAPI v1 adapter."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator

from fastapi import Body, FastAPI, Header, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse

from ragkb.adapters.auth import AuthenticationError, AuthorizationError
from ragkb.api.models import (
    AbortUploadResponse,
    AskRequest,
    AskResponse,
    AuditEventResponse,
    CitationResponse,
    CompleteUploadResponse,
    CreateUploadSessionRequest,
    DefectCreateRequest,
    DefectResponse,
    DeletionResponse,
    DiagnosticsResponse,
    DocumentQualityResponse,
    DocumentResponse,
    DocumentReviewRequest,
    DocumentReviewResponse,
    DocumentVersionResponse,
    ErrorResponse,
    EvidenceIndexRequest,
    EvidenceIndexResponse,
    EvidenceSourceResponse,
    FeedbackRequest,
    FeedbackResponse,
    FinalAcceptanceResponse,
    GovernanceRegisterRequest,
    GovernanceRegisterResponse,
    GovernanceSignoffRequest,
    HealthResponse,
    IncidentCreateRequest,
    IncidentResponse,
    JobResponse,
    LifecycleResponse,
    ObservationCreateRequest,
    ObservationMetricsRequest,
    ObservationResponse,
    PermissionUpdateRequest,
    PilotCreateRequest,
    PilotResponse,
    PilotRollbackRequest,
    ReadinessResponse,
    RollbackRequest,
    RolloutBatchResponse,
    SearchHitResponse,
    SearchRequest,
    SearchResponse,
    SpaceResponse,
    UATCaseCreateRequest,
    UATCaseResponse,
    UATResultRequest,
    UploadSessionResponse,
    UploadSessionStatusResponse,
)
from ragkb.application.lifecycle import (
    CleanupApprovalRequired,
    LifecycleIdempotencyConflict,
    LifecycleStateConflict,
)
from ragkb.application.uploads import MalwareRejectedError, UploadStateError
from ragkb.contracts.jobs import QueueConflictError, QueueJob, QueueLeaseError, QueueStateError
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.lifecycle import LifecycleRecord
from ragkb.domain.rag import AskResult
from ragkb.domain.retrieval import SearchContext, SecurityWatermarkNotReady
from ragkb.domain.state_machines import JobState
from ragkb.domain.uploads import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    ResourceNotFoundError,
)
from ragkb.engineering_security.file_validation import FileValidationError
from ragkb.engineering_security.references import ReferenceTokenError
from ragkb.runtime_components import RuntimeComponents, build_runtime_components

OPENAPI_VERSION = "1.0.0"


def _etag(row_version: int) -> str:
    return f'"{row_version}"'


def _job_etag(state_value: str, attempt: int) -> str:
    return f'"{state_value}:{attempt}"'


def _job_response(job: QueueJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        operation=job.operation,
        state=job.state.value,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        cancel_requested=job.cancel_requested,
        error_code=job.error_code,
    )


def _job_version_id(job: QueueJob) -> str | None:
    value = job.payload.get("document_version_id")
    return str(value) if job.operation == "process_document" and value else None


def _require_job_match(value: str, job: QueueJob) -> None:
    if value.strip() != _job_etag(job.state.value, job.attempt):
        raise OptimisticConcurrencyError(job.id)


def _ask_response(result: AskResult) -> AskResponse:
    return AskResponse(
        rag_run_id=result.rag_run_id,
        status=result.status.value,
        answer=result.answer,
        citations=[
            CitationResponse(
                evidence_id=citation.evidence_id,
                source_url=citation.source_url,
                locator=citation.locator,
            )
            for citation in result.citations
        ],
        warnings=list(result.warnings),
        verified=result.verified,
        real_acceptance=result.real_acceptance,
    )


def _lifecycle_response(record: LifecycleRecord) -> LifecycleResponse:
    return LifecycleResponse(
        document_id=record.document_id,
        active_version_id=record.active_version_id,
        lifecycle_state=record.lifecycle_state.value,
        acl_revision=record.acl_revision,
        visible=record.visible,
        tombstoned=record.tombstoned,
        row_version=record.row_version,
    )


def _pilot_response(item: dict[str, object]) -> PilotResponse:
    raw_blockers = item["blockers"]
    blockers = [str(value) for value in raw_blockers] if isinstance(raw_blockers, list) else []
    return PilotResponse(
        pilot_id=str(item["pilot_id"]),
        name=str(item["name"]),
        state=str(item["state"]),
        feature_flag=str(item["feature_flag"]),
        blockers=blockers,
        revision=int(str(item["revision"])),
        simulated=bool(item["simulated"]),
        real_acceptance=bool(item["real_acceptance"]),
    )


def _observation_response(item: dict[str, object]) -> ObservationResponse:
    raw_metrics = item["metrics"]
    metrics = (
        {str(key): float(str(value)) for key, value in raw_metrics.items()}
        if isinstance(raw_metrics, dict)
        else {}
    )
    return ObservationResponse(
        window_id=str(item["window_id"]),
        name=str(item["name"]),
        starts_at=float(str(item["starts_at"])),
        ends_at=float(str(item["ends_at"])),
        state=str(item["state"]),
        metrics=metrics,
        simulated=bool(item["simulated"]),
        real_acceptance=bool(item["real_acceptance"]),
        row_version=int(str(item["row_version"])),
    )


def _ensure_document_visible(runtime: RuntimeComponents, document_id: str) -> None:
    if not runtime.lifecycle_store.is_accessible(document_id):
        raise ResourceNotFoundError(document_id)


def _ensure_document_readable(
    runtime: RuntimeComponents,
    document_id: str,
    principal: RequestPrincipal,
) -> None:
    record = runtime.lifecycle_store.documents.get(document_id)
    if record is None or runtime.lifecycle_store.is_tombstoned(document_id):
        raise ResourceNotFoundError(document_id)
    if principal.has_role("knowledge_maintainer", "admin"):
        return
    _ensure_document_visible(runtime, document_id)


def _principal(request: Request) -> RequestPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, RequestPrincipal):
        raise AuthenticationError("AUTHENTICATION_REQUIRED")
    return principal


def _require_role(principal: RequestPrincipal, *roles: str) -> None:
    if not principal.has_role(*roles):
        raise AuthorizationError("AUTHORIZATION_REQUIRED")


def _require_local_tenant(runtime: RuntimeComponents, principal: RequestPrincipal) -> None:
    if principal.tenant_id != runtime.tenant_id:
        raise ResourceNotFoundError("tenant resource")


def _if_match(value: str) -> int:
    normalized = value.strip().strip('"')
    try:
        return int(normalized)
    except ValueError as error:
        raise OptimisticConcurrencyError("If-Match must contain an integer row version") from error


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _governance_command(
    runtime: RuntimeComponents,
    principal: RequestPrincipal,
    operation: str,
    key: str,
    payload: dict[str, object],
    execute: Callable[[], dict[str, object]],
) -> dict[str, object]:
    request_hash = runtime.uploads.request_hash(payload)
    replay = runtime.governance_repository.idempotency_response(
        principal.tenant_id, operation, key, request_hash
    )
    if replay is not None:
        return replay
    result = execute()
    runtime.governance_repository.save_idempotency_response(
        principal.tenant_id, operation, key, request_hash, result
    )
    return result


def _error(
    request: Request,
    code: str,
    message: str,
    status_code: int,
    *,
    retryable: bool = False,
) -> JSONResponse:
    payload = ErrorResponse(
        code=code,
        message=message,
        request_id=_request_id(request),
        retryable=retryable,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def create_app(components: RuntimeComponents | None = None) -> FastAPI:
    runtime = components or build_runtime_components()
    app = FastAPI(
        title=runtime.settings.app_name,
        version=OPENAPI_VERSION,
        openapi_version="3.1.0",
        docs_url="/docs",
        debug=runtime.settings.app_debug,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime.settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.components = runtime

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        if request.url.path not in {"/health/live", "/health/ready", "/docs", "/openapi.json"}:
            try:
                request.state.principal = runtime.authenticator.authenticate(
                    request.headers.get("Authorization")
                )
            except AuthenticationError:
                return _error(
                    request,
                    "AUTHENTICATION_REQUIRED",
                    "authentication is required",
                    401,
                )
        response = await call_next(request)
        runtime.observability.request_completed(
            request.state.request_id, request.method, request.url.path, response.status_code
        )
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ResourceNotFoundError)
    async def not_found(request: Request, error: ResourceNotFoundError) -> JSONResponse:
        return _error(request, "NOT_FOUND", "resource was not found", 404)

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(request: Request, error: Exception) -> JSONResponse:
        return _error(request, "CONFLICT_IDEMPOTENCY_KEY", str(error), 409)

    app.add_exception_handler(QueueConflictError, idempotency_conflict)
    app.add_exception_handler(LifecycleIdempotencyConflict, idempotency_conflict)

    @app.exception_handler(LifecycleStateConflict)
    async def lifecycle_conflict(request: Request, error: LifecycleStateConflict) -> JSONResponse:
        return _error(request, "CONFLICT_LIFECYCLE_STATE", str(error), 409)

    @app.exception_handler(CleanupApprovalRequired)
    async def cleanup_blocked(request: Request, error: CleanupApprovalRequired) -> JSONResponse:
        return _error(request, "CLEANUP_PENDING_APPROVAL", str(error), 409)

    @app.exception_handler(OptimisticConcurrencyError)
    async def concurrency(request: Request, error: OptimisticConcurrencyError) -> JSONResponse:
        return _error(request, "CONFLICT_ETAG", str(error), 412)

    @app.exception_handler(UploadStateError)
    async def state_conflict(request: Request, error: Exception) -> JSONResponse:
        return _error(request, "CONFLICT_STATE", str(error), 409)

    app.add_exception_handler(QueueLeaseError, state_conflict)
    app.add_exception_handler(QueueStateError, state_conflict)

    @app.exception_handler(FileValidationError)
    async def invalid_file(request: Request, error: FileValidationError) -> JSONResponse:
        return _error(request, error.code, str(error), 422)

    @app.exception_handler(SecurityWatermarkNotReady)
    async def watermark_not_ready(
        request: Request, error: SecurityWatermarkNotReady
    ) -> JSONResponse:
        return _error(
            request,
            "SECURITY_WATERMARK_NOT_READY",
            "retrieval permission projection is not ready",
            503,
            retryable=True,
        )

    @app.exception_handler(ReferenceTokenError)
    async def invalid_reference(request: Request, error: ReferenceTokenError) -> JSONResponse:
        return _error(request, "SOURCE_REFERENCE_NOT_FOUND", "source was not found", 404)

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, error: AuthenticationError) -> JSONResponse:
        return _error(request, "AUTHENTICATION_REQUIRED", "authentication is required", 401)

    @app.exception_handler(AuthorizationError)
    async def authorization_error(request: Request, error: AuthorizationError) -> JSONResponse:
        return _error(request, "FORBIDDEN", "operation is forbidden", 403)

    @app.exception_handler(MalwareRejectedError)
    async def malware(request: Request, error: MalwareRejectedError) -> JSONResponse:
        return _error(request, error.reason_code, "file was rejected by malware policy", 422)

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(
            status="ok", runtime="g3_native_python", real_service_acceptance=False
        )

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def ready() -> HealthResponse:
        runtime.database.initialize()
        return HealthResponse(
            status="ready", runtime="g3_native_python", real_service_acceptance=False
        )

    @app.get("/api/v1/spaces", response_model=list[SpaceResponse], tags=["spaces"])
    async def spaces(request: Request) -> list[SpaceResponse]:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        return [SpaceResponse.model_validate(item) for item in runtime.repository.list_spaces()]

    @app.post(
        "/api/v1/spaces/{space_id}/upload-sessions",
        response_model=UploadSessionResponse,
        status_code=status.HTTP_201_CREATED,
        responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
        tags=["ingestion"],
    )
    async def create_upload_session(
        space_id: str,
        body: CreateUploadSessionRequest,
        request: Request,
        response: Response,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> UploadSessionResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        session = runtime.uploads.create_session(
            space_id=space_id,
            idempotency_key=idempotency_key,
            **body.model_dump(),
        )
        response.headers["ETag"] = _etag(session.row_version)
        return UploadSessionResponse(
            upload_session_id=session.id,
            space_id=session.space_id,
            filename=session.filename,
            state=session.state.value,
            upload_path=f"/api/v1/upload-sessions/{session.id}/content",
            row_version=session.row_version,
        )

    @app.get(
        "/api/v1/upload-sessions/{session_id}",
        response_model=UploadSessionStatusResponse,
        tags=["ingestion"],
    )
    async def upload_session_status(
        session_id: str, response: Response, request: Request
    ) -> UploadSessionStatusResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        session = runtime.repository.get_session(session_id)
        response.headers["ETag"] = _etag(session.row_version)
        return UploadSessionStatusResponse(
            upload_session_id=session.id,
            state=session.state.value,
            row_version=session.row_version,
            document_id=session.document_id,
            document_version_id=session.document_version_id,
            job_id=session.job_id,
            error_code=session.error_code,
        )

    @app.post(
        "/api/v1/documents/{document_id}/versions/upload-sessions",
        response_model=UploadSessionResponse,
        tags=["ingestion"],
    )
    async def create_version_upload_session(
        document_id: str,
        body: CreateUploadSessionRequest,
        response: Response,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> UploadSessionResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        _ensure_document_readable(runtime, document_id, principal)
        session = runtime.uploads.create_version_session(
            document_id=document_id,
            expected_document_row_version=_if_match(if_match),
            space_id=runtime.space_id,
            idempotency_key=idempotency_key,
            **body.model_dump(),
        )
        response.headers["ETag"] = _etag(session.row_version)
        return UploadSessionResponse(
            upload_session_id=session.id,
            space_id=session.space_id,
            filename=session.filename,
            state=session.state.value,
            upload_path=f"/api/v1/upload-sessions/{session.id}/content",
            row_version=session.row_version,
        )

    @app.put(
        "/api/v1/upload-sessions/{session_id}/content",
        response_model=UploadSessionStatusResponse,
        tags=["ingestion"],
    )
    async def upload_content(
        session_id: str,
        response: Response,
        request: Request,
        content: bytes = Body(media_type="application/octet-stream"),
        if_match: str = Header(alias="If-Match"),
    ) -> UploadSessionStatusResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        session = runtime.uploads.upload_content(
            session_id, content, expected_row_version=_if_match(if_match)
        )
        response.headers["ETag"] = _etag(session.row_version)
        return UploadSessionStatusResponse(
            upload_session_id=session.id,
            state=session.state.value,
            row_version=session.row_version,
        )

    @app.post(
        "/api/v1/upload-sessions/{session_id}:complete",
        response_model=CompleteUploadResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["ingestion"],
    )
    async def complete_upload(
        session_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> CompleteUploadResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        result = runtime.uploads.complete(
            session_id,
            expected_row_version=_if_match(if_match),
            idempotency_key=idempotency_key,
        )
        runtime.lifecycle_store.reload()
        return CompleteUploadResponse.model_validate(result)

    @app.post(
        "/api/v1/upload-sessions/{session_id}:abort",
        response_model=AbortUploadResponse,
        tags=["ingestion"],
    )
    async def abort_upload(
        session_id: str,
        response: Response,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> AbortUploadResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        session = runtime.uploads.abort(
            session_id,
            expected_row_version=_if_match(if_match),
            idempotency_key=idempotency_key,
        )
        response.headers["ETag"] = _etag(session.row_version)
        return AbortUploadResponse(
            upload_session_id=session.id,
            state=session.state.value,
            row_version=session.row_version,
        )

    @app.get(
        "/api/v1/documents/{document_id}",
        response_model=DocumentResponse,
        tags=["documents"],
    )
    async def document(document_id: str, response: Response, request: Request) -> DocumentResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        _ensure_document_readable(runtime, document_id, principal)
        item = runtime.repository.get_document(document_id)
        response.headers["ETag"] = _etag(int(item["row_version"]))
        return DocumentResponse(
            id=str(item["id"]),
            tenant_id=str(item["tenant_id"]),
            source_id=str(item["source_id"]),
            external_key=str(item["external_key"]),
            state=str(item["state"]),
            current_version_id=(
                str(item["current_version_id"]) if item["current_version_id"] is not None else None
            ),
            row_version=int(item["row_version"]),
        )

    @app.get(
        "/api/v1/documents/{document_id}/versions",
        response_model=list[DocumentVersionResponse],
        tags=["documents"],
    )
    async def versions(document_id: str, request: Request) -> list[DocumentVersionResponse]:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        _ensure_document_readable(runtime, document_id, principal)
        runtime.repository.get_document(document_id)
        return [
            DocumentVersionResponse(
                id=str(item["id"]),
                tenant_id=str(item["tenant_id"]),
                document_id=str(item["document_id"]),
                version_no=int(item["version_no"]),
                content_sha256=str(item["content_sha256"]),
                original_key=str(item["original_key"]),
                mime_type=str(item["mime_type"]),
                processing_state=str(item["processing_state"]),
                publication_state=str(item["publication_state"]),
                parser_revision=(
                    str(item["parser_revision"]) if item["parser_revision"] is not None else None
                ),
            )
            for item in runtime.repository.get_versions(document_id)
        ]

    @app.get(
        "/api/v1/document-versions/{version_id}/quality-report",
        response_model=DocumentQualityResponse,
        tags=["validation"],
    )
    async def quality_report(version_id: str, request: Request) -> DocumentQualityResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        version = runtime.repository.get_version(version_id)
        _ensure_document_readable(runtime, str(version["document_id"]), principal)
        report = runtime.repository.get_quality_report(version_id)
        return DocumentQualityResponse(
            document_version_id=version_id,
            source_format=str(report["source_format"]),
            parser_revision=str(report["parser_revision"]),
            node_count=int(report["node_count"]),
            locator_coverage=float(report["locator_coverage"]),
            issue_codes=list(map(str, report["issue_codes"])),
            disposition=str(report["disposition"]),
            real_acceptance=bool(report["real_acceptance"]),
        )

    @app.post(
        "/api/v1/document-versions/{version_id}/review",
        response_model=DocumentReviewResponse,
        tags=["validation"],
    )
    async def review_document_version(
        version_id: str,
        body: DocumentReviewRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> DocumentReviewResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        version = runtime.repository.get_version(version_id)
        _ensure_document_readable(runtime, str(version["document_id"]), principal)
        quality = runtime.repository.get_quality_report(version_id)
        payload = {**body.model_dump(), "reviewer_id": principal.user_id}
        result = runtime.repository.save_document_review(
            version_id=version_id,
            reviewer_id=principal.user_id,
            decision=body.decision,
            comment=body.comment,
            quality_revision=str(quality["parser_revision"]),
            idempotency_key=idempotency_key,
            request_hash=runtime.uploads.request_hash(payload),
        )
        return DocumentReviewResponse.model_validate(result)

    @app.get(
        "/api/v1/ingestion-jobs/{job_id}",
        response_model=JobResponse,
        tags=["jobs"],
    )
    async def job_status(job_id: str, response: Response, request: Request) -> JobResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        job = runtime.queue.get(job_id)
        if job is None:
            raise ResourceNotFoundError(job_id)
        response.headers["ETag"] = _job_etag(job.state.value, job.attempt)
        return _job_response(job)

    @app.post(
        "/api/v1/ingestion-jobs/{job_id}:cancel",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["jobs"],
    )
    async def cancel_job(
        job_id: str,
        response: Response,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> JobResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        operation = f"{runtime.tenant_id}:cancel-ingestion-job:{job_id}"
        command_hash = runtime.uploads.request_hash({"job_id": job_id})
        replay = runtime.repository.idempotency_response(operation, idempotency_key, command_hash)
        if replay is not None:
            result = JobResponse.model_validate(replay)
            response.headers["ETag"] = _job_etag(result.state, result.attempt)
            return result
        job = runtime.queue.get(job_id)
        if job is None:
            raise ResourceNotFoundError(job_id)
        _require_job_match(if_match, job)
        updated = runtime.queue.request_cancel(job_id)
        version_id = _job_version_id(updated)
        if version_id is not None and updated.state is JobState.CANCELLED:
            runtime.repository.mark_version_cancelled(version_id)
        result = _job_response(updated)
        runtime.repository.save_idempotency_response(
            operation, idempotency_key, command_hash, job_id, result.model_dump()
        )
        response.headers["ETag"] = _job_etag(updated.state.value, updated.attempt)
        return result

    @app.post(
        "/api/v1/ingestion-jobs/{job_id}:retry",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["jobs"],
    )
    async def retry_job(
        job_id: str,
        response: Response,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> JobResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        operation = f"{runtime.tenant_id}:retry-ingestion-job:{job_id}"
        command_hash = runtime.uploads.request_hash({"job_id": job_id})
        replay = runtime.repository.idempotency_response(operation, idempotency_key, command_hash)
        if replay is not None:
            result = JobResponse.model_validate(replay)
            response.headers["ETag"] = _job_etag(result.state, result.attempt)
            return result
        job = runtime.queue.get(job_id)
        if job is None:
            raise ResourceNotFoundError(job_id)
        _require_job_match(if_match, job)
        updated = runtime.queue.retry(job_id)
        version_id = _job_version_id(updated)
        if version_id is not None:
            runtime.repository.mark_version_processing(version_id)
        result = _job_response(updated)
        runtime.repository.save_idempotency_response(
            operation, idempotency_key, command_hash, job_id, result.model_dump()
        )
        response.headers["ETag"] = _job_etag(updated.state.value, updated.attempt)
        return result

    @app.post(
        "/api/v1/search",
        response_model=SearchResponse,
        responses={503: {"model": ErrorResponse}},
        tags=["retrieval"],
    )
    async def search(request: Request, body: SearchRequest) -> SearchResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        if body.space_id is not None and body.space_id != runtime.space_id:
            raise ResourceNotFoundError(body.space_id)
        context = SearchContext(
            tenant_id=principal.tenant_id,
            space_ids=(runtime.space_id,),
            subject_scope_tokens=principal.scope_tokens,
            clearance_level=3,
            as_of_epoch=int(time.time()),
            active_generation_id="local-g2-empty",
            active_permission_revision=max(
                (record.acl_revision for record in runtime.lifecycle_store.documents.values()),
                default=0,
            ),
            required_security_watermark=0,
        )
        result = runtime.search_service.search(body.query, context, limit=body.limit)
        return SearchResponse(
            request_id=_request_id(request),
            observed_security_watermark=result.observed_security_watermark,
            hits=[
                SearchHitResponse(
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    document_version_id=hit.document_version_id,
                    text=hit.text,
                    locator=hit.locator,
                    fused_score=hit.fused_score,
                    rerank_position=hit.rerank_position,
                    channels=list(hit.channels),
                    parent_chunk_id=hit.parent_chunk_id,
                    parent_text=hit.parent_text,
                )
                for hit in result.hits
            ],
            real_acceptance=result.real_acceptance,
            degraded=result.degraded,
            warnings=list(result.warnings),
        )

    @app.post(
        "/api/v1/ask",
        response_model=AskResponse,
        tags=["trusted-qa"],
    )
    async def ask(request: Request, body: AskRequest) -> AskResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        result = runtime.qa_service.ask(
            body.question,
            principal.tenant_id,
            principal.user_id,
            subject_scope_tokens=principal.scope_tokens,
        )
        return _ask_response(result)

    @app.post(
        "/api/v1/ask:stream",
        response_class=StreamingResponse,
        tags=["trusted-qa"],
    )
    async def ask_stream(request: Request, body: AskRequest) -> StreamingResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)

        def stream() -> Iterator[str]:
            for stage in ("retrieval_started", "evidence_validation_started"):
                yield f"event: progress\ndata: {json.dumps({'stage': stage})}\n\n"
            result = runtime.qa_service.ask(
                body.question,
                principal.tenant_id,
                principal.user_id,
                subject_scope_tokens=principal.scope_tokens,
            )
            verification = "verified" if result.verified else "verification_failed"
            yield f"event: progress\ndata: {json.dumps({'stage': verification})}\n\n"
            payload = _ask_response(result).model_dump(mode="json")
            yield f"event: result\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get(
        "/api/v1/rag-runs/{run_token}/evidence/{evidence_token}/source",
        response_model=EvidenceSourceResponse,
        tags=["trusted-qa"],
    )
    async def evidence_source(
        run_token: str, evidence_token: str, request: Request
    ) -> EvidenceSourceResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        run_id, evidence_id = runtime.reference_signer.resolve(
            run_token,
            evidence_token,
            principal.tenant_id,
            principal.user_id,
        )
        evidence = runtime.rag_repository.get_evidence(run_id, evidence_id)
        package = runtime.rag_repository.get_package(run_id)
        if (
            evidence is None
            or package is None
            or not evidence.authorized
            or not evidence.current_version
            or not evidence.valid_at(int(time.time()))
            or not runtime.lifecycle_store.is_accessible(evidence.document_id)
            or not runtime.qa_service.permission.recheck(
                (evidence,),
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                subject_scope_tokens=principal.scope_tokens,
                permission_revision=package.permission_revision,
                at_epoch=int(time.time()),
            )
        ):
            raise ResourceNotFoundError(evidence_id)
        return EvidenceSourceResponse(
            evidence_id=evidence.evidence_id,
            text=evidence.text,
            locator=evidence.locator,
        )

    @app.post(
        "/api/v1/rag-runs/{rag_run_id}/feedback",
        response_model=FeedbackResponse,
        tags=["trusted-qa"],
    )
    async def feedback(
        rag_run_id: str, body: FeedbackRequest, request: Request
    ) -> FeedbackResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = runtime.qa_service.feedback(
                rag_run_id,
                principal.user_id,
                body.rating,
                body.reason_code,
                body.comment,
            )
        except KeyError as error:
            raise ResourceNotFoundError(rag_run_id) from error
        return FeedbackResponse(
            rag_run_id=item.rag_run_id,
            accepted=True,
            index_generation_id=item.index_generation_id,
            retrieval_revision=item.retrieval_revision,
            prompt_revision=item.prompt_revision,
            model_revision=item.model_revision,
        )

    @app.post(
        "/api/v1/document-versions/{version_id}:publish",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def publish_version(
        version_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        runtime.lifecycle_store.reload()
        version = runtime.repository.get_version(version_id)
        document_id = str(version["document_id"])
        if document_id not in runtime.lifecycle_store.documents:
            runtime.lifecycle_service.register_document(
                document_id, version_id, trace_id=_request_id(request)
            )
        record = runtime.lifecycle_service.publish(
            document_id,
            version_id,
            event_id=idempotency_key,
            trace_id=_request_id(request),
        )
        return _lifecycle_response(record)

    @app.post(
        "/api/v1/documents/{document_id}:rollback",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def rollback_document(
        document_id: str,
        body: RollbackRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        runtime.lifecycle_store.reload()
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        record = runtime.lifecycle_service.rollback(
            document_id,
            body.version_id,
            event_id=idempotency_key,
            trace_id=_request_id(request),
        )
        return _lifecycle_response(record)

    @app.put(
        "/api/v1/resources/document/{document_id}/permissions",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def update_document_permissions(
        document_id: str,
        body: PermissionUpdateRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        record = runtime.lifecycle_service.update_acl(
            document_id,
            body.target_acl_revision,
            body.required_watermark,
            body.observed_watermark,
            projection_ok=body.projection_ok,
            event_id=idempotency_key,
            trace_id=_request_id(request),
        )
        return _lifecycle_response(record)

    @app.delete(
        "/api/v1/documents/{document_id}",
        response_model=DeletionResponse,
        tags=["lifecycle"],
    )
    async def delete_document(
        document_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> DeletionResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        tombstone = runtime.lifecycle_service.delete(
            document_id, event_id=idempotency_key, trace_id=_request_id(request)
        )
        runtime.reference_signer.revoke_document(document_id)
        record = runtime.lifecycle_store.documents[document_id]
        return DeletionResponse(
            document_id=document_id,
            lifecycle_state=record.lifecycle_state.value,
            visible=record.visible,
            cleanup={name: value.value for name, value in tombstone.cleanup.items()},
        )

    @app.post(
        "/api/v1/documents/{document_id}:revoke",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def revoke_document(
        document_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        record = runtime.lifecycle_service.revoke(
            document_id, event_id=idempotency_key, trace_id=_request_id(request)
        )
        runtime.reference_signer.revoke_document(document_id)
        return _lifecycle_response(record)

    @app.post(
        "/api/v1/documents/{document_id}/cleanup/{target_store}:run",
        response_model=DeletionResponse,
        tags=["lifecycle"],
    )
    async def run_document_cleanup(
        document_id: str,
        target_store: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> DeletionResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.tombstones:
            raise ResourceNotFoundError(document_id)
        runtime.lifecycle_service.run_cleanup(
            document_id,
            target_store,
            trace_id=_request_id(request),
            event_id=idempotency_key,
        )
        tombstone = runtime.lifecycle_store.tombstones[document_id]
        record = runtime.lifecycle_store.documents[document_id]
        return DeletionResponse(
            document_id=document_id,
            lifecycle_state=record.lifecycle_state.value,
            visible=record.visible,
            cleanup={name: value.value for name, value in tombstone.cleanup.items()},
        )

    @app.get(
        "/api/v1/admin/audit-events",
        response_model=list[AuditEventResponse],
        tags=["admin"],
    )
    async def audit_events(request: Request) -> list[AuditEventResponse]:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        return [
            AuditEventResponse(
                sequence=event.sequence,
                action=event.action,
                resource_id=event.resource_id,
                trace_id=event.trace_id,
                governance_revision=event.governance_revision,
                previous_hash=event.previous_hash,
                event_hash=event.event_hash,
            )
            for event in runtime.lifecycle_store.audit_events
        ]

    @app.get(
        "/api/v1/admin/diagnostics",
        response_model=DiagnosticsResponse,
        tags=["operations"],
    )
    async def diagnostics(request: Request) -> DiagnosticsResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        return DiagnosticsResponse.model_validate(runtime.observability.diagnostics())

    @app.get("/api/v1/admin/alerts", tags=["operations"])
    async def alerts(request: Request) -> list[dict[str, object]]:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        return runtime.observability.alerts()

    @app.post(
        "/api/v1/admin/evidence-index",
        response_model=EvidenceIndexResponse,
        tags=["operations"],
    )
    async def add_evidence_index(
        body: EvidenceIndexRequest, request: Request
    ) -> EvidenceIndexResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = runtime.governance_repository.add_evidence(
                body.category, body.revision, body.metadata
            )
        except ValueError as error:
            raise LifecycleStateConflict(str(error)) from error
        return EvidenceIndexResponse(
            evidence_id=str(item["evidence_id"]),
            category=str(item["category"]),
            revision=str(item["revision"]),
            content_hash=str(item["content_hash"]),
        )

    @app.post(
        "/api/v1/admin/governance-register",
        response_model=GovernanceRegisterResponse,
        tags=["operations"],
    )
    async def add_governance_register(
        body: GovernanceRegisterRequest, request: Request
    ) -> GovernanceRegisterResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = runtime.governance_repository.add_register_record(
                body.category,
                body.title,
                body.owner,
                body.state,
                body.revision,
                body.metadata,
            )
        except ValueError as error:
            raise LifecycleStateConflict(str(error)) from error
        return GovernanceRegisterResponse.model_validate(item)

    @app.post("/api/v1/governance/pilots", response_model=PilotResponse, tags=["pilot"])
    async def create_pilot(
        body: PilotCreateRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> PilotResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        item = _governance_command(
            runtime,
            principal,
            "pilot.create",
            idempotency_key,
            body.model_dump(),
            lambda: runtime.governance_repository.create_pilot(body.name, body.feature_flag),
        )
        return _pilot_response(item)

    @app.get("/api/v1/governance/pilots/{pilot_id}", response_model=PilotResponse, tags=["pilot"])
    async def get_pilot(pilot_id: str, request: Request) -> PilotResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            return _pilot_response(runtime.governance_repository.get_pilot(pilot_id))
        except KeyError as error:
            raise ResourceNotFoundError(pilot_id) from error

    @app.post("/api/v1/governance/pilots/{pilot_id}/signoffs", tags=["pilot"])
    async def pilot_signoff(
        pilot_id: str,
        body: GovernanceSignoffRequest,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> dict[str, object]:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            pilot = runtime.governance_repository.get_pilot(pilot_id)
        except KeyError as error:
            raise ResourceNotFoundError(pilot_id) from error
        if int(str(pilot["revision"])) != _if_match(if_match):
            raise OptimisticConcurrencyError(pilot_id)
        return _governance_command(
            runtime,
            principal,
            f"pilot.signoff:{pilot_id}:{body.role}",
            idempotency_key,
            body.model_dump(),
            lambda: runtime.governance_repository.add_signoff(
                "pilot", pilot_id, body.role, body.decision, principal.user_id, body.comment
            ),
        )

    @app.post(
        "/api/v1/governance/pilots/{pilot_id}:evaluate",
        response_model=ReadinessResponse,
        tags=["pilot"],
    )
    async def evaluate_pilot(
        pilot_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> ReadinessResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            expected_revision = _if_match(if_match)
            payload = {"pilot_id": pilot_id, "expected_revision": expected_revision}
            item = _governance_command(
                runtime,
                principal,
                f"pilot.evaluate:{pilot_id}",
                idempotency_key,
                payload,
                lambda: {
                    **runtime.governance_service.evaluate_pilot(
                        pilot_id, expected_revision
                    ).__dict__
                },
            )
        except KeyError as error:
            raise ResourceNotFoundError(pilot_id) from error
        raw_blockers = item["blockers"]
        item["blockers"] = list(raw_blockers) if isinstance(raw_blockers, (list, tuple)) else []
        return ReadinessResponse.model_validate(item)

    @app.post("/api/v1/governance/pilots/{pilot_id}:canary", tags=["pilot"])
    async def canary_pilot(
        pilot_id: str,
        request: Request,
        seed: int = 20260901,
        request_count: int = 20,
        threshold: int = 2,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> dict[str, object]:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            runtime.governance_repository.get_pilot(pilot_id)
        except KeyError as error:
            raise ResourceNotFoundError(pilot_id) from error
        if request_count < 1 or threshold < 0:
            raise LifecycleStateConflict("CANARY_PARAMETERS_INVALID")
        payload = {
            "pilot_id": pilot_id,
            "seed": seed,
            "request_count": request_count,
            "threshold": threshold,
        }
        return _governance_command(
            runtime,
            principal,
            f"pilot.canary:{pilot_id}",
            idempotency_key,
            payload,
            lambda: runtime.governance_service.run_canary(
                pilot_id, seed, request_count, threshold, _if_match(if_match)
            ),
        )

    @app.post(
        "/api/v1/governance/pilots/{pilot_id}:rollout",
        response_model=list[RolloutBatchResponse],
        tags=["pilot"],
    )
    async def rollout_pilot(
        pilot_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> list[RolloutBatchResponse]:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            expected_revision = _if_match(if_match)
            item = _governance_command(
                runtime,
                principal,
                f"pilot.rollout:{pilot_id}",
                idempotency_key,
                {"pilot_id": pilot_id, "expected_revision": expected_revision},
                lambda: {
                    "batches": runtime.governance_service.plan_rollout(pilot_id, expected_revision)
                },
            )
        except KeyError as error:
            raise ResourceNotFoundError(pilot_id) from error
        except ValueError as error:
            raise LifecycleStateConflict(str(error)) from error
        raw_batches = item["batches"]
        batches = raw_batches if isinstance(raw_batches, list) else []
        return [RolloutBatchResponse.model_validate(batch) for batch in batches]

    @app.post(
        "/api/v1/governance/pilots/{pilot_id}:rollback",
        response_model=PilotResponse,
        tags=["pilot"],
    )
    async def rollback_pilot(
        pilot_id: str,
        body: PilotRollbackRequest,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> PilotResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = _governance_command(
                runtime,
                principal,
                f"pilot.rollback:{pilot_id}",
                idempotency_key,
                body.model_dump(),
                lambda: runtime.governance_service.rollback_pilot(
                    pilot_id, body.trigger, _if_match(if_match)
                ),
            )
            return _pilot_response(item)
        except KeyError as error:
            raise ResourceNotFoundError(pilot_id) from error

    @app.post("/api/v1/governance/uat-cases", response_model=UATCaseResponse, tags=["uat"])
    async def create_uat(
        body: UATCaseCreateRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> UATCaseResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        try:
            runtime.governance_repository.get_pilot(body.pilot_id)
        except KeyError as error:
            raise ResourceNotFoundError(body.pilot_id) from error
        item = _governance_command(
            runtime,
            principal,
            f"uat.create:{body.pilot_id}",
            idempotency_key,
            body.model_dump(),
            lambda: runtime.governance_repository.create_uat_case(
                body.pilot_id, body.title, body.steps, body.expected
            ),
        )
        return UATCaseResponse.model_validate(item)

    @app.put(
        "/api/v1/governance/uat-cases/{case_id}/result",
        response_model=UATCaseResponse,
        tags=["uat"],
    )
    async def update_uat(
        case_id: str,
        body: UATResultRequest,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> UATCaseResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        try:
            current = runtime.governance_repository.get_uat_case(case_id)
        except KeyError as error:
            raise ResourceNotFoundError(case_id) from error
        raw_steps = current["steps"]
        if not isinstance(raw_steps, list) or len(body.step_results) != len(raw_steps):
            raise LifecycleStateConflict("UAT_STEP_RESULTS_INCOMPLETE")
        raw_expected = current["expected"]
        if body.result == "PASSED" and (
            not isinstance(raw_expected, list) or body.step_results != raw_expected
        ):
            raise LifecycleStateConflict("UAT_EXPECTED_RECONCILIATION_FAILED")
        evidence = [item.model_dump() for item in body.evidence]
        if body.result == "PASSED" and not evidence:
            raise LifecycleStateConflict("UAT_PASSED_REQUIRES_EVIDENCE")
        if any(
            not runtime.governance_repository.evidence_reference_exists(item) for item in evidence
        ):
            raise LifecycleStateConflict("UAT_EVIDENCE_REFERENCE_INVALID")
        item = _governance_command(
            runtime,
            principal,
            f"uat.result:{case_id}",
            idempotency_key,
            body.model_dump(mode="json"),
            lambda: runtime.governance_repository.update_uat_case(
                case_id, body.result, evidence, body.step_results, _if_match(if_match)
            ),
        )
        return UATCaseResponse.model_validate(item)

    @app.post("/api/v1/governance/defects", response_model=DefectResponse, tags=["governance"])
    async def create_defect(
        body: DefectCreateRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> DefectResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        item = _governance_command(
            runtime,
            principal,
            f"defect.create:{body.scope_type}:{body.scope_id}",
            idempotency_key,
            body.model_dump(),
            lambda: runtime.governance_repository.add_defect(
                body.scope_type, body.scope_id, body.severity, body.title
            ),
        )
        return DefectResponse.model_validate(item)

    @app.put(
        "/api/v1/governance/defects/{defect_id}:resolve",
        response_model=DefectResponse,
        tags=["governance"],
    )
    async def resolve_defect(
        defect_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> DefectResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = _governance_command(
                runtime,
                principal,
                f"defect.resolve:{defect_id}",
                idempotency_key,
                {"defect_id": defect_id},
                lambda: runtime.governance_repository.resolve_defect(
                    defect_id, _if_match(if_match)
                ),
            )
        except KeyError as error:
            raise ResourceNotFoundError(defect_id) from error
        return DefectResponse.model_validate(item)

    @app.post(
        "/api/v1/governance/observations",
        response_model=ObservationResponse,
        tags=["acceptance"],
    )
    async def create_observation(
        body: ObservationCreateRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> ObservationResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        item = _governance_command(
            runtime,
            principal,
            "observation.create",
            idempotency_key,
            body.model_dump(),
            lambda: runtime.governance_service.create_observation(body.name),
        )
        return _observation_response(item)

    @app.get(
        "/api/v1/governance/observations/{window_id}",
        response_model=ObservationResponse,
        tags=["acceptance"],
    )
    async def get_observation(window_id: str, request: Request) -> ObservationResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            return _observation_response(runtime.governance_repository.get_observation(window_id))
        except KeyError as error:
            raise ResourceNotFoundError(window_id) from error

    @app.put(
        "/api/v1/governance/observations/{window_id}/metrics",
        response_model=ObservationResponse,
        tags=["acceptance"],
    )
    async def observation_metrics(
        window_id: str,
        body: ObservationMetricsRequest,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> ObservationResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = _governance_command(
                runtime,
                principal,
                f"observation.metrics:{window_id}",
                idempotency_key,
                body.model_dump(),
                lambda: runtime.governance_repository.record_observation_metrics(
                    window_id, body.metrics, _if_match(if_match)
                ),
            )
        except KeyError as error:
            raise ResourceNotFoundError(window_id) from error
        return _observation_response(item)

    @app.post("/api/v1/governance/observations/{window_id}/signoffs", tags=["acceptance"])
    async def observation_signoff(
        window_id: str,
        body: GovernanceSignoffRequest,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> dict[str, object]:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            observation = runtime.governance_repository.get_observation(window_id)
        except KeyError as error:
            raise ResourceNotFoundError(window_id) from error
        if int(str(observation["row_version"])) != _if_match(if_match):
            raise OptimisticConcurrencyError(window_id)
        return _governance_command(
            runtime,
            principal,
            f"observation.signoff:{window_id}:{body.role}",
            idempotency_key,
            body.model_dump(),
            lambda: runtime.governance_repository.add_signoff(
                "observation",
                window_id,
                body.role,
                body.decision,
                principal.user_id,
                body.comment,
            ),
        )

    @app.post(
        "/api/v1/governance/observations/{window_id}/incidents",
        response_model=IncidentResponse,
        tags=["acceptance"],
    )
    async def create_incident(
        window_id: str,
        body: IncidentCreateRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> IncidentResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            runtime.governance_repository.get_observation(window_id)
        except KeyError as error:
            raise ResourceNotFoundError(window_id) from error
        item = _governance_command(
            runtime,
            principal,
            f"incident.create:{window_id}",
            idempotency_key,
            body.model_dump(),
            lambda: runtime.governance_repository.add_incident(
                window_id, body.severity, body.title
            ),
        )
        return IncidentResponse.model_validate(item)

    @app.put(
        "/api/v1/governance/incidents/{incident_id}:resolve",
        response_model=IncidentResponse,
        tags=["acceptance"],
    )
    async def resolve_incident(
        incident_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> IncidentResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = _governance_command(
                runtime,
                principal,
                f"incident.resolve:{incident_id}",
                idempotency_key,
                {"incident_id": incident_id},
                lambda: runtime.governance_repository.resolve_incident(
                    incident_id, _if_match(if_match)
                ),
            )
        except KeyError as error:
            raise ResourceNotFoundError(incident_id) from error
        return IncidentResponse.model_validate(item)

    @app.post(
        "/api/v1/governance/observations/{window_id}:close",
        response_model=ObservationResponse,
        tags=["acceptance"],
    )
    async def close_observation(
        window_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> ObservationResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = _governance_command(
                runtime,
                principal,
                f"observation.close:{window_id}",
                idempotency_key,
                {"window_id": window_id},
                lambda: runtime.governance_service.close_observation(
                    window_id, _if_match(if_match)
                ),
            )
        except KeyError as error:
            raise ResourceNotFoundError(window_id) from error
        except ValueError as error:
            raise LifecycleStateConflict(str(error)) from error
        return _observation_response(item)

    @app.post(
        "/api/v1/governance/observations/{window_id}:evaluate",
        response_model=ReadinessResponse,
        tags=["acceptance"],
    )
    async def evaluate_observation(
        window_id: str,
        request: Request,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> ReadinessResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            observation = runtime.governance_repository.get_observation(window_id)
            expected = _if_match(if_match)
            if int(str(observation["row_version"])) != expected:
                raise OptimisticConcurrencyError(window_id)
            item = _governance_command(
                runtime,
                principal,
                f"observation.evaluate:{window_id}",
                idempotency_key,
                {"window_id": window_id, "row_version": expected},
                lambda: {**runtime.governance_service.evaluate_observation(window_id).__dict__},
            )
        except KeyError as error:
            raise ResourceNotFoundError(window_id) from error
        raw_blockers = item["blockers"]
        item["blockers"] = list(raw_blockers) if isinstance(raw_blockers, (list, tuple)) else []
        return ReadinessResponse.model_validate(item)

    @app.get(
        "/api/v1/governance/observations/{window_id}/final-acceptance-report",
        response_model=FinalAcceptanceResponse,
        tags=["acceptance"],
    )
    async def final_acceptance_report(window_id: str, request: Request) -> FinalAcceptanceResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            report = runtime.governance_service.final_acceptance_report(window_id)
        except KeyError as error:
            raise ResourceNotFoundError(window_id) from error
        return FinalAcceptanceResponse.model_validate(report)

    def custom_openapi() -> dict[str, object]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        for path, methods in schema.get("paths", {}).items():
            if path.startswith("/health/"):
                continue
            for operation in methods.values():
                if isinstance(operation, dict) and "responses" in operation:
                    operation["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app
