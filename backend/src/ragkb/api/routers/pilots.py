"""pilots governance routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request

from ragkb.api.models import (
    DefectCreateRequest,
    DefectResponse,
    GovernanceSignoffRequest,
    PilotCreateRequest,
    PilotResponse,
    PilotRollbackRequest,
    ReadinessResponse,
    RolloutBatchResponse,
    UATCaseCreateRequest,
    UATCaseResponse,
    UATResultRequest,
)
from ragkb.api.support import (
    governance_command as _governance_command,
)
from ragkb.api.support import (
    if_match as _if_match,
)
from ragkb.api.support import (
    pilot_response as _pilot_response,
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


def build_pilots_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/governance/pilots", response_model=PilotResponse, tags=["pilot"])
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

    @router.get(
        "/api/v1/governance/pilots/{pilot_id}", response_model=PilotResponse, tags=["pilot"]
    )
    async def get_pilot(pilot_id: str, request: Request) -> PilotResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        try:
            return _pilot_response(runtime.governance_repository.get_pilot(pilot_id))
        except KeyError as error:
            raise ResourceNotFoundError(pilot_id) from error

    @router.post("/api/v1/governance/pilots/{pilot_id}/signoffs", tags=["pilot"])
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

    @router.post(
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

    @router.post("/api/v1/governance/pilots/{pilot_id}:canary", tags=["pilot"])
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

    @router.post(
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

    @router.post(
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

    @router.post("/api/v1/governance/uat-cases", response_model=UATCaseResponse, tags=["uat"])
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

    @router.put(
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

    @router.post("/api/v1/governance/defects", response_model=DefectResponse, tags=["governance"])
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

    @router.put(
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

    return router
