"""Rag API routes."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ragkb.api.models import (
    AskRequest,
    AskResponse,
    ErrorResponse,
    EvidenceSourceResponse,
    FeedbackRequest,
    FeedbackResponse,
    SearchHitResponse,
    SearchRequest,
    SearchResponse,
)
from ragkb.api.support import (
    ask_response as _ask_response,
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
from ragkb.application.deadlines import request_deadline
from ragkb.domain.retrieval import SearchContext
from ragkb.domain.uploads import (
    ResourceNotFoundError,
)
from ragkb.runtime_components import RuntimeComponents

OPENAPI_VERSION = "1.0.0"


def build_rag_router(runtime: RuntimeComponents) -> APIRouter:
    router = APIRouter()

    def selected_space(tenant_id: str, requested_space_id: str | None) -> str:
        space_id = requested_space_id or runtime.space_id
        if not any(
            str(item["id"]) == space_id and str(item["tenant_id"]) == tenant_id
            for item in runtime.repository.list_spaces()
        ):
            raise ResourceNotFoundError(space_id)
        return space_id

    @router.post(
        "/api/v1/search",
        response_model=SearchResponse,
        responses={503: {"model": ErrorResponse}},
        tags=["retrieval"],
    )
    @request_deadline()
    def search(request: Request, body: SearchRequest) -> SearchResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        space_id = selected_space(principal.tenant_id, body.space_id)
        runtime.lifecycle_store.reload()
        release = runtime.retrieval_release.current_release(principal.tenant_id, space_id)
        context = SearchContext(
            tenant_id=principal.tenant_id,
            space_ids=(space_id,),
            subject_scope_tokens=principal.scope_tokens,
            clearance_level=principal.clearance_level,
            as_of_epoch=int(time.time()),
            active_generation_id=release.active_generation_id,
            active_permission_revision=release.active_permission_revision,
            required_security_watermark=release.security_watermark,
        )
        result = runtime.search_service.search(body.query, context, limit=body.limit)
        return SearchResponse(
            request_id=_request_id(request),
            observed_security_watermark=result.observed_security_watermark,
            hits=[
                SearchHitResponse(
                    chunk_id=hit.chunk_id,
                    document_id=hit.document_id,
                    document_version_id=hit.document_version_id,
                    text=hit.text,
                    display_text=hit.display_text,
                    retrieval_text=hit.retrieval_text,
                    generation_context=hit.generation_context,
                    locator=hit.locator,
                    fused_score=hit.fused_score,
                    rerank_position=hit.rerank_position,
                    channels=list(hit.channels),
                    parent_chunk_id=hit.parent_chunk_id,
                    parent_text=hit.parent_text,
                )
                for hit in result.hits
            ],
            real_acceptance=result.real_acceptance,
            degraded=result.degraded,
            warnings=list(result.warnings),
        )

    @router.post(
        "/api/v1/ask",
        response_model=AskResponse,
        tags=["trusted-qa"],
    )
    @request_deadline()
    def ask(request: Request, body: AskRequest) -> AskResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        space_id = selected_space(principal.tenant_id, body.space_id)
        runtime.lifecycle_store.reload()
        result = runtime.qa_service.ask(
            body.question,
            principal.tenant_id,
            principal.user_id,
            subject_scope_tokens=principal.scope_tokens,
            clearance_level=principal.clearance_level,
            space_id=space_id,
        )
        return _ask_response(result)

    @router.post(
        "/api/v1/ask:stream",
        response_class=StreamingResponse,
        tags=["trusted-qa"],
    )
    def ask_stream(request: Request, body: AskRequest) -> StreamingResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        space_id = selected_space(principal.tenant_id, body.space_id)
        runtime.lifecycle_store.reload()

        def stream() -> Iterator[str]:
            for stage in ("retrieval_started", "evidence_validation_started"):
                yield f"event: progress\ndata: {json.dumps({'stage': stage})}\n\n"
            with request_deadline():
                result = runtime.qa_service.ask(
                    body.question,
                    principal.tenant_id,
                    principal.user_id,
                    subject_scope_tokens=principal.scope_tokens,
                    clearance_level=principal.clearance_level,
                    space_id=space_id,
                )
            verification = "verified" if result.verified else "verification_failed"
            yield f"event: progress\ndata: {json.dumps({'stage': verification})}\n\n"
            payload = _ask_response(result).model_dump(mode="json")
            yield f"event: result\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.get(
        "/api/v1/rag-runs/{run_token}/evidence/{evidence_token}/source",
        response_model=EvidenceSourceResponse,
        tags=["trusted-qa"],
    )
    def evidence_source(
        run_token: str, evidence_token: str, request: Request
    ) -> EvidenceSourceResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        run_id, evidence_id = runtime.reference_signer.resolve(
            run_token,
            evidence_token,
            principal.tenant_id,
            principal.user_id,
        )
        evidence = runtime.rag_repository.get_evidence(run_id, evidence_id)
        package = runtime.rag_repository.get_package(run_id)
        runtime.lifecycle_store.reload()
        if (
            evidence is None
            or package is None
            or not evidence.authorized
            or not evidence.current_version
            or not evidence.valid_at(int(time.time()))
            or not runtime.lifecycle_store.is_accessible(evidence.document_id)
            or not runtime.qa_service.permission.recheck(
                (evidence,),
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                subject_scope_tokens=principal.scope_tokens,
                permission_revision=package.permission_revision,
                at_epoch=int(time.time()),
                clearance_level=principal.clearance_level,
                generation_id=package.index_generation_id,
            )
        ):
            raise ResourceNotFoundError(evidence_id)
        return EvidenceSourceResponse(
            evidence_id=evidence.evidence_id,
            text=evidence.text,
            locator=evidence.locator,
        )

    @router.post(
        "/api/v1/rag-runs/{rag_run_id}/feedback",
        response_model=FeedbackResponse,
        tags=["trusted-qa"],
    )
    def feedback(rag_run_id: str, body: FeedbackRequest, request: Request) -> FeedbackResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        try:
            item = runtime.qa_service.feedback(
                rag_run_id,
                principal.user_id,
                body.rating,
                body.reason_code,
                body.comment,
            )
        except KeyError as error:
            raise ResourceNotFoundError(rag_run_id) from error
        return FeedbackResponse(
            rag_run_id=item.rag_run_id,
            accepted=True,
            index_generation_id=item.index_generation_id,
            retrieval_revision=item.retrieval_revision,
            prompt_revision=item.prompt_revision,
            model_revision=item.model_revision,
        )

    return router
