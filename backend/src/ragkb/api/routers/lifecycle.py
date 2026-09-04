"""Lifecycle API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request

from ragkb.api.models import (
    DeletionResponse,
    LifecycleResponse,
    PermissionUpdateRequest,
    RollbackRequest,
)
from ragkb.api.support import (
    lifecycle_response as _lifecycle_response,
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
from ragkb.domain.uploads import (
    ResourceNotFoundError,
)
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_lifecycle_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/v1/document-versions/{version_id}:publish",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def publish_version(
        version_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        runtime.lifecycle_store.reload()
        version = runtime.repository.get_version(version_id)
        document_id = str(version["document_id"])
        if document_id not in runtime.lifecycle_store.documents:
            runtime.lifecycle_service.register_document(
                document_id, version_id, trace_id=_request_id(request)
            )
        record = runtime.lifecycle_service.publish(
            document_id,
            version_id,
            event_id=idempotency_key,
            trace_id=_request_id(request),
        )
        set_current_version = getattr(runtime.repository, "set_document_current_version", None)
        if callable(set_current_version) and not getattr(
            runtime.lifecycle_store, "durable_publication_intents", False
        ):
            set_current_version(document_id, version_id)
        return _lifecycle_response(record)

    @router.post(
        "/api/v1/documents/{document_id}:rollback",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def rollback_document(
        document_id: str,
        body: RollbackRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        runtime.lifecycle_store.reload()
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        record = runtime.lifecycle_service.rollback(
            document_id,
            body.version_id,
            event_id=idempotency_key,
            trace_id=_request_id(request),
        )
        set_current_version = getattr(runtime.repository, "set_document_current_version", None)
        if callable(set_current_version) and not getattr(
            runtime.lifecycle_store, "durable_publication_intents", False
        ):
            set_current_version(document_id, body.version_id)
        return _lifecycle_response(record)

    @router.put(
        "/api/v1/resources/document/{document_id}/permissions",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def update_document_permissions(
        document_id: str,
        body: PermissionUpdateRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        record = runtime.lifecycle_service.update_acl(
            document_id,
            body.target_acl_revision,
            body.required_watermark,
            body.observed_watermark,
            projection_ok=body.projection_ok,
            event_id=idempotency_key,
            trace_id=_request_id(request),
        )
        return _lifecycle_response(record)

    @router.delete(
        "/api/v1/documents/{document_id}",
        response_model=DeletionResponse,
        tags=["lifecycle"],
    )
    async def delete_document(
        document_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> DeletionResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        tombstone = runtime.lifecycle_service.delete(
            document_id, event_id=idempotency_key, trace_id=_request_id(request)
        )
        runtime.reference_signer.revoke_document(document_id)
        record = runtime.lifecycle_store.documents[document_id]
        return DeletionResponse(
            document_id=document_id,
            lifecycle_state=record.lifecycle_state.value,
            visible=record.visible,
            cleanup={name: value.value for name, value in tombstone.cleanup.items()},
        )

    @router.post(
        "/api/v1/documents/{document_id}:revoke",
        response_model=LifecycleResponse,
        tags=["lifecycle"],
    )
    async def revoke_document(
        document_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> LifecycleResponse:
        principal = _principal(request)
        _require_role(principal, "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.documents:
            raise ResourceNotFoundError(document_id)
        record = runtime.lifecycle_service.revoke(
            document_id, event_id=idempotency_key, trace_id=_request_id(request)
        )
        runtime.reference_signer.revoke_document(document_id)
        return _lifecycle_response(record)

    @router.post(
        "/api/v1/documents/{document_id}/cleanup/{target_store}:run",
        response_model=DeletionResponse,
        tags=["lifecycle"],
    )
    async def run_document_cleanup(
        document_id: str,
        target_store: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> DeletionResponse:
        principal = _principal(request)
        _require_role(principal, "admin")
        _require_local_tenant(runtime, principal)
        if document_id not in runtime.lifecycle_store.tombstones:
            raise ResourceNotFoundError(document_id)
        runtime.lifecycle_service.run_cleanup(
            document_id,
            target_store,
            trace_id=_request_id(request),
            event_id=idempotency_key,
        )
        tombstone = runtime.lifecycle_store.tombstones[document_id]
        record = runtime.lifecycle_store.documents[document_id]
        return DeletionResponse(
            document_id=document_id,
            lifecycle_state=record.lifecycle_state.value,
            visible=record.visible,
            cleanup={name: value.value for name, value in tombstone.cleanup.items()},
        )

    return router
