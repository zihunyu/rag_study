"""Knowledge managers triage low ratings and attach acceptance case/retest receipts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ragkb.api.support import principal
from ragkb.domain.acceptance import AcceptanceCase
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.feedback_workflow import FeedbackWorkflow

if TYPE_CHECKING:
    from ragkb.infrastructure.acceptance_service import AcceptanceService


class FeedbackAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    action: Literal[
        "claim",
        "create_case",
        "link_case",
        "ready_for_retest",
        "record_retest",
        "dismiss",
        "reopen",
    ]
    note: str = Field(min_length=1, max_length=2000, pattern=r".*\S.*")
    case_id: str = Field(default="", max_length=191)
    attempt_id: str = Field(default="", max_length=191)


def build_feedback_workflow_router(acceptance: AcceptanceService) -> APIRouter:
    router = APIRouter(prefix="/api/spaces/{space_id}/feedback-work-items", tags=["feedback"])
    service = FeedbackWorkflow(acceptance)

    @router.get("")
    def list_items(
        space_id: str,
        request: Request,
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        acceptance.authorize(principal(request), space_id)
        return service.list(space_id, offset=offset, limit=limit)

    @router.get("/{identity}")
    def detail(space_id: str, identity: str, request: Request) -> dict[str, Any]:
        acceptance.authorize(principal(request), space_id)
        return service.detail(space_id, identity)

    @router.post("/{identity}/actions")
    def act(space_id: str, identity: str, body: FeedbackAction, request: Request) -> dict[str, Any]:
        subject = principal(request)
        acceptance.authorize(subject, space_id)
        try:
            item = service.detail(space_id, identity)
            if body.action in {"link_case", "ready_for_retest"}:
                case_id = body.case_id if body.action == "link_case" else item["payload"]["case_id"]
                with service.db.connection() as c:
                    case = service._case(c, space_id, case_id)
                acceptance.validate_case(
                    subject, space_id, AcceptanceCase.model_validate(case["payload"])
                )
            if body.action == "record_retest":
                with service.db.connection() as c:
                    row = service.db.one(
                        c,
                        "SELECT a.run_id FROM acceptance_attempts a JOIN acceptance_runs r "
                        "ON r.id=a.run_id WHERE a.id=? AND r.tenant_id=? AND r.space_id=?",
                        (body.attempt_id, service.tenant, space_id),
                    )
                if not row:
                    raise ResourceNotFoundError(body.attempt_id)
                run = acceptance.repository.run(space_id, row["run_id"])
                if (
                    acceptance.snapshot(subject, space_id, run["payload"]["cases"])
                    != run["payload"]["snapshot"]
                ):
                    raise ValueError("FEEDBACK_RETEST_ENVIRONMENT_CHANGED")
            return service.update(
                space_id,
                identity,
                subject.user_id,
                body.revision,
                body.action,
                body.note,
                case_id=body.case_id,
                attempt_id=body.attempt_id,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
