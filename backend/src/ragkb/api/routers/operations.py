"""operations governance routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ragkb.api.models import (
    AuditEventResponse,
    DiagnosticsResponse,
    EvidenceIndexRequest,
    EvidenceIndexResponse,
    GovernanceRegisterRequest,
    GovernanceRegisterResponse,
)
from ragkb.api.support import (
    principal as _principal,
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
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_operations_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/v1/admin/audit-events",
        response_model=list[AuditEventResponse],
        tags=["admin"],
    )
    def audit_events(request: Request) -> list[AuditEventResponse]:
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

    @router.get(
        "/api/v1/admin/diagnostics",
        response_model=DiagnosticsResponse,
        tags=["operations"],
    )
    def diagnostics(request: Request) -> DiagnosticsResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        return DiagnosticsResponse.model_validate(runtime.observability.diagnostics())

    @router.get("/api/v1/admin/alerts", tags=["operations"])
    def alerts(request: Request) -> list[dict[str, object]]:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        return runtime.observability.alerts()

    @router.post(
        "/api/v1/admin/evidence-index",
        response_model=EvidenceIndexResponse,
        tags=["operations"],
    )
    def add_evidence_index(body: EvidenceIndexRequest, request: Request) -> EvidenceIndexResponse:
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

    @router.post(
        "/api/v1/admin/governance-register",
        response_model=GovernanceRegisterResponse,
        tags=["operations"],
    )
    def add_governance_register(
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

    return router
