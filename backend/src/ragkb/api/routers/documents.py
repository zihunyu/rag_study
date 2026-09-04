"""Documents API routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Header, Request, Response, status

from ragkb.api.models import (
    DocumentQualityResponse,
    DocumentResponse,
    DocumentReviewRequest,
    DocumentReviewResponse,
    DocumentVersionResponse,
    JobResponse,
)
from ragkb.api.support import (
    ensure_document_readable as _ensure_document_readable,
)
from ragkb.api.support import (
    etag as _etag,
)
from ragkb.api.support import (
    job_etag as _job_etag,
)
from ragkb.api.support import (
    job_response as _job_response,
)
from ragkb.api.support import (
    job_version_id as _job_version_id,
)
from ragkb.api.support import (
    principal as _principal,
)
from ragkb.api.support import (
    request_id as _request_id,
)
from ragkb.api.support import (
    require_job_match as _require_job_match,
)
from ragkb.api.support import (
    require_local_tenant as _require_local_tenant,
)
from ragkb.api.support import (
    require_role as _require_role,
)
from ragkb.application.lifecycle import (
    LifecycleStateConflict,
)
from ragkb.domain.retrieval import SecurityProjection
from ragkb.domain.state_machines import JobState
from ragkb.domain.uploads import (
    ResourceNotFoundError,
)
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_documents_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.get(
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

    @router.get(
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

    @router.get(
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

    @router.post(
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
        now = int(time.time())
        security = None
        security_body = body.security_projection
        if body.decision == "APPROVED" and security_body is not None:
            record = runtime.lifecycle_store.documents.get(str(version["document_id"]))
            if record is None:
                raise LifecycleStateConflict("DOCUMENT_NOT_REGISTERED")
            security = SecurityProjection(
                visibility=security_body.visibility,
                classification_level=security_body.classification_level,
                acl_scope_tokens=tuple(security_body.acl_scope_tokens),
                lifecycle_projection="STAGED",
                permission_revision=record.acl_revision,
                valid_from_epoch=now,
                valid_to_epoch=security_body.valid_to_epoch,
            )
        payload = {**body.model_dump(mode="json"), "reviewer_id": principal.user_id}
        result = runtime.repository.save_document_review(
            version_id=version_id,
            reviewer_id=principal.user_id,
            decision=body.decision,
            comment=body.comment,
            quality_revision=str(quality["parser_revision"]),
            security_revision="reviewed-security:v1" if security is not None else None,
            security_projection=(
                {
                    "visibility": security.visibility,
                    "classification_level": security.classification_level,
                    "acl_scope_tokens": list(security.acl_scope_tokens),
                    "lifecycle_projection": security.lifecycle_projection,
                    "permission_revision": security.permission_revision,
                    "valid_from_epoch": security.valid_from_epoch,
                    "valid_to_epoch": security.valid_to_epoch,
                }
                if security is not None
                else None
            ),
            idempotency_key=idempotency_key,
            request_hash=runtime.uploads.request_hash(payload),
        )
        if security is not None:
            runtime.lifecycle_service.set_reviewed_security_projection(
                str(version["document_id"]),
                version_id,
                security,
                trace_id=_request_id(request),
            )
        return DocumentReviewResponse.model_validate(result)

    @router.get(
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

    @router.post(
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

    @router.post(
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

    return router
