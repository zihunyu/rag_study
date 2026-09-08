"""Rag API routes."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Request, Response
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
    SearchSourceResponse,
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
from ragkb.application.reading_scope import reading_scope
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
        if runtime.repository.get_space(space_id)["tenant_id"] != tenant_id:
            raise ResourceNotFoundError(space_id)
        return space_id

    @router.post(
        "/api/search",
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
                    duplicate_sources=[
                        SearchSourceResponse(
                            chunk_id=source.chunk_id,
                            document_id=source.document_id,
                            document_version_id=source.document_version_id,
                            display_text=source.display_text,
                            retrieval_text=source.retrieval_text,
                            locator=source.locator,
                        )
                        for source in hit.duplicate_sources
                    ],
                    parent_source=(
                        SearchSourceResponse(
                            chunk_id=hit.parent_source.chunk_id,
                            document_id=hit.parent_source.document_id,
                            document_version_id=hit.parent_source.document_version_id,
                            display_text=hit.parent_source.display_text,
                            retrieval_text=hit.parent_source.retrieval_text,
                            locator=hit.parent_source.locator,
                        )
                        if hit.parent_source is not None
                        else None
                    ),
                )
                for hit in result.hits
            ],
            real_acceptance=result.real_acceptance,
            degraded=result.degraded,
            warnings=list(result.warnings),
            retrieval_health=result.retrieval_health,
        )

    @router.post(
        "/api/ask",
        response_model=AskResponse,
        tags=["trusted-qa"],
    )
    def ask(request: Request, body: AskRequest) -> AskResponse:
        principal = _principal(request)
        _require_role(principal, "reader", "knowledge_maintainer", "admin")
        _require_local_tenant(runtime, principal)
        space_id = selected_space(principal.tenant_id, body.space_id)
        runtime.lifecycle_store.reload()
        with (
            reading_scope(body.reading),
            request_deadline(
                runtime.settings.overview_timeout_seconds if body.reading.mode != "fact" else 120
            ),
        ):
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
        "/api/ask:stream",
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
            with (
                reading_scope(body.reading),
                request_deadline(
                    runtime.settings.overview_timeout_seconds
                    if body.reading.mode != "fact"
                    else 120
                ),
            ):
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
        "/api/rag-runs/{run_token}/evidence/{evidence_token}/source",
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
            text=evidence.display_text or evidence.text,
            locator=evidence.locator,
            visuals=source_visuals(evidence, run_token, evidence_token, run_id),
        )

    def source_visuals(
        evidence: Any, run_token: str, evidence_token: str, run_id: str
    ) -> list[dict[str, Any]]:
        from ragkb.infrastructure.visual_assets import VisualAssetStore

        store = VisualAssetStore(runtime.storage)
        visuals = []
        for identity in evidence.locator.get("visual_asset_ids", []):
            try:
                asset = store.get(evidence.document_version_id, identity)
                if asset.get("status") != "verified":
                    raise ResourceNotFoundError(identity)
                public = store.public(asset, evidence.document_version_id)
                public["image_url"] = (
                    f"/api/rag-runs/{run_token}/evidence/{evidence_token}/visuals/{identity}/image"
                )
                result = runtime.rag_repository.get_result(run_id)
                from ragkb.infrastructure.graph_citations import cited_graph_targets

                public["focus_targets"] = cited_graph_targets(evidence, asset, result)
                if result and result.answer:
                    from ragkb.document_processing.local_visual_check import (
                        compact,
                        mentions_region,
                    )

                    paragraphs = "\n".join(
                        p for p in result.answer.split("\n\n") if f"[{evidence.evidence_id}]" in p
                    )
                    regions = asset.get("regions", [])
                    public["focus_region_ids"] = [
                        r["id"]
                        for r in regions
                        if len(compact(r["text"])) > 1
                        and r.get("score", 0) >= 0.9
                        and mentions_region(paragraphs, r["text"])
                        and sum(compact(other["text"]) == compact(r["text"]) for other in regions)
                        == 1
                    ][:24]
                visuals.append(public)
            except (FileNotFoundError, ValueError) as error:
                raise ResourceNotFoundError(identity) from error
        return visuals

    @router.get("/api/rag-runs/{run_token}/evidence/{evidence_token}/visuals/{asset_id}/image")
    def cited_image(
        run_token: str,
        evidence_token: str,
        asset_id: str,
        request: Request,
        normalized: bool = False,
    ) -> Response:
        from ragkb.api.routers.visuals import image_response
        from ragkb.infrastructure.visual_assets import VisualAssetStore

        source = evidence_source(run_token, evidence_token, request)
        if asset_id not in source.locator.get("visual_asset_ids", []):
            raise ResourceNotFoundError(asset_id)
        subject = _principal(request)
        run_id, identity = runtime.reference_signer.resolve(
            run_token, evidence_token, subject.tenant_id, subject.user_id
        )
        evidence = runtime.rag_repository.get_evidence(run_id, identity)
        assert evidence is not None
        return image_response(
            VisualAssetStore(runtime.storage),
            evidence.document_version_id,
            asset_id,
            normalized=normalized,
        )

    @router.post(
        "/api/rag-runs/{rag_run_id}/feedback",
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
