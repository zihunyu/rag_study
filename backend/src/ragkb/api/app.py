"""Frozen G1 FastAPI/OpenAPI v1 adapter."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Body, FastAPI, Header, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ragkb.api.models import (
    AbortUploadResponse,
    CompleteUploadResponse,
    CreateUploadSessionRequest,
    DocumentResponse,
    DocumentVersionResponse,
    ErrorResponse,
    HealthResponse,
    JobResponse,
    SpaceResponse,
    UploadSessionResponse,
    UploadSessionStatusResponse,
)
from ragkb.application.uploads import MalwareRejectedError, UploadStateError
from ragkb.contracts.jobs import QueueConflictError, QueueLeaseError
from ragkb.domain.uploads import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    ResourceNotFoundError,
)
from ragkb.engineering_security.file_validation import FileValidationError
from ragkb.runtime_components import RuntimeComponents, build_runtime_components

OPENAPI_VERSION = "1.0.0"


def _etag(row_version: int) -> str:
    return f'"{row_version}"'


def _job_etag(state_value: str, attempt: int) -> str:
    return f'"{state_value}:{attempt}"'


def _if_match(value: str) -> int:
    normalized = value.strip().strip('"')
    try:
        return int(normalized)
    except ValueError as error:
        raise OptimisticConcurrencyError("If-Match must contain an integer row version") from error


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ResourceNotFoundError)
    async def not_found(request: Request, error: ResourceNotFoundError) -> JSONResponse:
        return _error(request, "NOT_FOUND", "resource was not found", 404)

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(request: Request, error: Exception) -> JSONResponse:
        return _error(request, "CONFLICT_IDEMPOTENCY_KEY", str(error), 409)

    app.add_exception_handler(QueueConflictError, idempotency_conflict)

    @app.exception_handler(OptimisticConcurrencyError)
    async def concurrency(request: Request, error: OptimisticConcurrencyError) -> JSONResponse:
        return _error(request, "CONFLICT_ETAG", str(error), 412)

    @app.exception_handler(UploadStateError)
    async def state_conflict(request: Request, error: Exception) -> JSONResponse:
        return _error(request, "CONFLICT_STATE", str(error), 409)

    app.add_exception_handler(QueueLeaseError, state_conflict)

    @app.exception_handler(FileValidationError)
    async def invalid_file(request: Request, error: FileValidationError) -> JSONResponse:
        return _error(request, error.code, str(error), 422)

    @app.exception_handler(MalwareRejectedError)
    async def malware(request: Request, error: MalwareRejectedError) -> JSONResponse:
        return _error(request, error.reason_code, "file was rejected by malware policy", 422)

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(
            status="ok", runtime="g1_native_python", real_service_acceptance=False
        )

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def ready() -> HealthResponse:
        runtime.database.initialize()
        return HealthResponse(
            status="ready", runtime="g1_native_python", real_service_acceptance=False
        )

    @app.get("/api/v1/spaces", response_model=list[SpaceResponse], tags=["spaces"])
    async def spaces() -> list[SpaceResponse]:
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
        response: Response,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> UploadSessionResponse:
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
        session_id: str, response: Response
    ) -> UploadSessionStatusResponse:
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

    @app.put(
        "/api/v1/upload-sessions/{session_id}/content",
        response_model=UploadSessionStatusResponse,
        tags=["ingestion"],
    )
    async def upload_content(
        session_id: str,
        response: Response,
        content: bytes = Body(media_type="application/octet-stream"),
        if_match: str = Header(alias="If-Match"),
    ) -> UploadSessionStatusResponse:
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
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> CompleteUploadResponse:
        result = runtime.uploads.complete(
            session_id,
            expected_row_version=_if_match(if_match),
            idempotency_key=idempotency_key,
        )
        return CompleteUploadResponse.model_validate(result)

    @app.post(
        "/api/v1/upload-sessions/{session_id}:abort",
        response_model=AbortUploadResponse,
        tags=["ingestion"],
    )
    async def abort_upload(
        session_id: str,
        response: Response,
        if_match: str = Header(alias="If-Match"),
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> AbortUploadResponse:
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
    async def document(document_id: str, response: Response) -> DocumentResponse:
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
    async def versions(document_id: str) -> list[DocumentVersionResponse]:
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
        "/api/v1/ingestion-jobs/{job_id}",
        response_model=JobResponse,
        tags=["jobs"],
    )
    async def job_status(job_id: str, response: Response) -> JobResponse:
        job = runtime.queue.get(job_id)
        if job is None:
            raise ResourceNotFoundError(job_id)
        response.headers["ETag"] = _job_etag(job.state.value, job.attempt)
        return JobResponse(
            id=job.id,
            operation=job.operation,
            state=job.state.value,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            cancel_requested=job.cancel_requested,
            error_code=job.error_code,
        )

    return app
