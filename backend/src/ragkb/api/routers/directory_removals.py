"""Knowledge managers can inspect and resolve source removal reviews."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ragkb.api.support import principal
from ragkb.infrastructure.directory_removal_service import DirectoryRemovalService

if TYPE_CHECKING:
    from ragkb.infrastructure.acceptance_service import AcceptanceService


class RemovalReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    action: Literal["withdraw", "keep", "reopen"]
    note: str = Field(min_length=1, max_length=2000, pattern=r".*\S.*")


def build_directory_removals_router(acceptance: AcceptanceService) -> APIRouter:
    router = APIRouter(prefix="/api/spaces/{space_id}/directory-removals", tags=["directory-sync"])
    service = DirectoryRemovalService(acceptance.runtime)

    @router.get("")
    def list_reviews(space_id: str, request: Request) -> list[dict[str, Any]]:
        acceptance.authorize(principal(request), space_id)
        return service.list(space_id)

    @router.post("/{identity}/review")
    def review(
        space_id: str, identity: str, body: RemovalReview, request: Request
    ) -> dict[str, Any]:
        subject = principal(request)
        acceptance.authorize(subject, space_id)
        try:
            return service.review(
                space_id, identity, subject.user_id, body.revision, body.action, body.note
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
