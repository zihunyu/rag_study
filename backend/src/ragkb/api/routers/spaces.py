"""Spaces API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from ragkb.api.models import (
    CreateSpaceRequest,
    KnowledgeDocumentResponse,
    SpaceResponse,
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
from ragkb.domain.errors import SchemaMismatch
from ragkb.domain.retrieval import RetrievalRelease
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_spaces_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    def require_space(tenant_id: str, space_id: str) -> None:
        if not any(
            str(item["id"]) == space_id and str(item["tenant_id"]) == tenant_id
            for item in runtime.repository.list_spaces()
        ):
            raise ResourceNotFoundError(space_id)

    @router.get("/api/v1/spaces", response_model=list[SpaceResponse], tags=["spaces"])
    async def spaces(request: Request) -> list[SpaceResponse]:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        return [
            SpaceResponse(
                id=str(item["id"]),
                tenant_id=str(item["tenant_id"]),
                name=str(item["name"]),
                status=str(item["status"]),
            )
            for item in runtime.repository.list_spaces()
        ]

    @router.post(
        "/api/v1/spaces",
        response_model=SpaceResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["spaces"],
    )
    async def create_space(body: CreateSpaceRequest, request: Request) -> SpaceResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        item = runtime.repository.create_space(principal.tenant_id, body.name)
        created_space_id = str(item["id"])
        try:
            runtime.retrieval_release.current_release(principal.tenant_id, created_space_id)
        except (KeyError, SchemaMismatch):
            runtime.retrieval_release.set_release(
                RetrievalRelease(
                    tenant_id=principal.tenant_id,
                    space_id=created_space_id,
                    active_generation_id=runtime.settings.retrieval_active_generation_id,
                    active_permission_revision=0,
                    security_watermark=0,
                )
            )
        return SpaceResponse(
            id=created_space_id,
            tenant_id=str(item["tenant_id"]),
            name=str(item["name"]),
            status=str(item["status"]),
        )

    @router.get(
        "/api/v1/spaces/{space_id}/documents",
        response_model=list[KnowledgeDocumentResponse],
        tags=["spaces", "documents"],
    )
    async def documents(space_id: str, request: Request) -> list[KnowledgeDocumentResponse]:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        require_space(principal.tenant_id, space_id)
        return [
            KnowledgeDocumentResponse.model_validate(item)
            for item in runtime.repository.list_documents(space_id)
        ]

    return router
