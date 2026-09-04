"""Uploads API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response, status

from ragkb.api.models import (
    AbortUploadResponse,
    CompleteUploadResponse,
    CreateUploadSessionRequest,
    ErrorResponse,
    UploadSessionResponse,
    UploadSessionStatusResponse,
)
from ragkb.api.support import (
    ensure_document_readable as _ensure_document_readable,
)
from ragkb.api.support import (
    etag as _etag,
)
from ragkb.api.support import (
    if_match as _if_match,
)
from ragkb.api.support import (
    principal as _principal,
)
from ragkb.api.support import (
    request_id as _request_id,
)
from ragkb.api.support import (
    require_local_tenant as _require_local_tenant,
)
from ragkb.api.support import (
    require_role as _require_role,
)
from ragkb.engineering_security.file_validation import FileValidationError
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_uploads_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.post(
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

    @router.get(
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

    @router.post(
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

    @router.put(
        "/api/v1/upload-sessions/{session_id}/content",
        response_model=UploadSessionStatusResponse,
        tags=["ingestion"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
    )
    async def upload_content(
        session_id: str,
        response: Response,
        request: Request,
        if_match: str = Header(alias="If-Match"),
    ) -> UploadSessionStatusResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        raw_content_length = request.headers.get("content-length")
        try:
            content_length = int(raw_content_length) if raw_content_length is not None else None
        except ValueError as error:
            raise FileValidationError(
                "DOC_CONTENT_LENGTH_INVALID", "Content-Length must be an integer"
            ) from error
        session = await runtime.uploads.upload_content_stream(
            session_id,
            request.stream(),
            expected_row_version=_if_match(if_match),
            content_length=content_length,
        )
        response.headers["ETag"] = _etag(session.row_version)
        return UploadSessionStatusResponse(
            upload_session_id=session.id,
            state=session.state.value,
            row_version=session.row_version,
        )

    @router.post(
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
        if result["document_id"] not in runtime.lifecycle_store.documents:
            runtime.lifecycle_service.register_document(
                str(result["document_id"]),
                str(result["document_version_id"]),
                trace_id=_request_id(request),
            )
        return CompleteUploadResponse.model_validate(result)

    @router.post(
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

    return router
