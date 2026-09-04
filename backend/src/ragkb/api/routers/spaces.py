"""Spaces API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ragkb.api.models import (
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
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_spaces_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/spaces", response_model=list[SpaceResponse], tags=["spaces"])
    async def spaces(request: Request) -> list[SpaceResponse]:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        return [SpaceResponse.model_validate(item) for item in runtime.repository.list_spaces()]

    return router
