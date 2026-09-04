"""acceptance governance routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request

from ragkb.api.models import (
    FinalAcceptanceResponse,
    GovernanceSignoffRequest,
    IncidentCreateRequest,
    IncidentResponse,
    ObservationCreateRequest,
    ObservationMetricsRequest,
    ObservationResponse,
    ReadinessResponse,
)
from ragkb.api.support import (
    governance_command as _governance_command,
)
from ragkb.api.support import (
    if_match as _if_match,
)
from ragkb.api.support import (
    observation_response as _observation_response,
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
from ragkb.domain.uploads import (
    OptimisticConcurrencyError,
    ResourceNotFoundError,
)
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_acceptance_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.post(
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

    @router.get(
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

    @router.put(
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

    @router.post("/api/v1/governance/observations/{window_id}/signoffs", tags=["acceptance"])
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

    @router.post(
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

    @router.put(
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

    @router.post(
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

    @router.post(
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

    @router.get(
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

    return router
