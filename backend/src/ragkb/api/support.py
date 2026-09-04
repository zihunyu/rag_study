"""Shared API presentation, authorization, idempotency, and error helpers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from ragkb.adapters.auth import AuthenticationError, AuthorizationError
from ragkb.api.models import (
    AskResponse,
    CitationResponse,
    ErrorResponse,
    JobResponse,
    LifecycleResponse,
    ObservationResponse,
    PilotResponse,
)
from ragkb.contracts.jobs import QueueJob
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.lifecycle import LifecycleRecord
from ragkb.domain.rag import AskResult
from ragkb.domain.uploads import OptimisticConcurrencyError, ResourceNotFoundError
from ragkb.runtime_components import RuntimeComponents


def etag(row_version: int) -> str:
    return f'"{row_version}"'


def job_etag(state_value: str, attempt: int) -> str:
    return f'"{state_value}:{attempt}"'


def job_response(job: QueueJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        operation=job.operation,
        state=job.state.value,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        cancel_requested=job.cancel_requested,
        error_code=job.error_code,
    )


def job_version_id(job: QueueJob) -> str | None:
    value = job.payload.get("document_version_id")
    return str(value) if job.operation == "process_document" and value else None


def require_job_match(value: str, job: QueueJob) -> None:
    if value.strip() != job_etag(job.state.value, job.attempt):
        raise OptimisticConcurrencyError(job.id)


def ask_response(result: AskResult) -> AskResponse:
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


def lifecycle_response(record: LifecycleRecord) -> LifecycleResponse:
    return LifecycleResponse(
        document_id=record.document_id,
        active_version_id=record.active_version_id,
        lifecycle_state=record.lifecycle_state.value,
        acl_revision=record.acl_revision,
        visible=record.visible,
        tombstoned=record.tombstoned,
        row_version=record.row_version,
    )


def pilot_response(item: dict[str, object]) -> PilotResponse:
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


def observation_response(item: dict[str, object]) -> ObservationResponse:
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


def ensure_document_visible(runtime: RuntimeComponents, document_id: str) -> None:
    runtime.lifecycle_store.reload()
    if not runtime.lifecycle_store.is_accessible(document_id):
        raise ResourceNotFoundError(document_id)


def ensure_document_readable(
    runtime: RuntimeComponents,
    document_id: str,
    principal: RequestPrincipal,
) -> None:
    runtime.lifecycle_store.reload()
    record = runtime.lifecycle_store.documents.get(document_id)
    if record is None or runtime.lifecycle_store.is_tombstoned(document_id):
        raise ResourceNotFoundError(document_id)
    if principal.has_role("knowledge_maintainer", "admin"):
        return
    ensure_document_visible(runtime, document_id)


def principal(request: Request) -> RequestPrincipal:
    value = getattr(request.state, "principal", None)
    if not isinstance(value, RequestPrincipal):
        raise AuthenticationError("AUTHENTICATION_REQUIRED")
    return value


def require_role(value: RequestPrincipal, *roles: str) -> None:
    if not value.has_role(*roles):
        raise AuthorizationError("AUTHORIZATION_REQUIRED")


def require_local_tenant(runtime: RuntimeComponents, value: RequestPrincipal) -> None:
    if value.tenant_id != runtime.tenant_id:
        raise ResourceNotFoundError("tenant resource")


def if_match(value: str) -> int:
    normalized = value.strip().strip('"')
    try:
        return int(normalized)
    except ValueError as error:
        raise OptimisticConcurrencyError("If-Match must contain an integer row version") from error


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def governance_command(
    runtime: RuntimeComponents,
    value: RequestPrincipal,
    operation: str,
    key: str,
    payload: dict[str, object],
    execute: Callable[[], dict[str, object]],
) -> dict[str, object]:
    request_hash = runtime.uploads.request_hash(payload)
    replay = runtime.governance_repository.idempotency_response(
        value.tenant_id, operation, key, request_hash
    )
    if replay is not None:
        return replay
    result = execute()
    runtime.governance_repository.save_idempotency_response(
        value.tenant_id, operation, key, request_hash, result
    )
    return result


def error_response(
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
        request_id=request_id(request),
        retryable=retryable,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())
