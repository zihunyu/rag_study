"""Search-backed local evidence assembly for trusted QA."""

from __future__ import annotations

import time
from collections.abc import Callable

from ragkb.application.search import HybridSearchService
from ragkb.contracts.ports import RetrievalReleasePort
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import Evidence, EvidencePackage
from ragkb.domain.retrieval import SearchContext


class SearchBackedEvidenceProvider:
    revision = "search-backed-evidence:g3-v1"

    def __init__(
        self,
        search_service: HybridSearchService,
        *,
        space_id: str,
        active_generation_id: str,
        active_permission_revision: Callable[[], int],
        required_security_watermark: Callable[[], int],
        prompt_revision: str,
        model_revision: str,
        final_evidence_count: int,
        verifier_revision: str = "",
        release_provider: RetrievalReleasePort | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.search_service = search_service
        self.space_id = space_id
        self.active_generation_id = active_generation_id
        self.active_permission_revision = active_permission_revision
        self.required_security_watermark = required_security_watermark
        self.prompt_revision = prompt_revision
        self.model_revision = model_revision
        self.final_evidence_count = final_evidence_count
        self.verifier_revision = verifier_revision
        self.release_provider = release_provider
        self.clock = clock

    def build_package(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
        clearance_level: int = 0,
        space_id: str | None = None,
    ) -> EvidencePackage:
        query_time = int(self.clock())
        selected_space_id = space_id or self.space_id
        release = (
            self.release_provider.current_release(tenant_id, selected_space_id)
            if self.release_provider is not None
            else None
        )
        permission_revision = (
            release.active_permission_revision
            if release is not None
            else self.active_permission_revision()
        )
        active_generation_id = (
            release.active_generation_id if release is not None else self.active_generation_id
        )
        required_watermark = (
            release.security_watermark
            if release is not None
            else self.required_security_watermark()
        )
        context = SearchContext(
            tenant_id=tenant_id,
            space_ids=(selected_space_id,),
            subject_scope_tokens=subject_scope_tokens,
            clearance_level=clearance_level,
            as_of_epoch=query_time,
            active_generation_id=active_generation_id,
            active_permission_revision=permission_revision,
            required_security_watermark=required_watermark,
        )
        result = self.search_service.search(
            question,
            context,
            limit=self.final_evidence_count,
        )
        evidence = tuple(
            Evidence(
                evidence_id=f"E{index}",
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                document_version_id=hit.document_version_id,
                text=(f"{hit.parent_text}\n{hit.text}" if hit.parent_text else hit.text),
                locator=hit.locator,
                valid_from_epoch=hit.valid_from_epoch,
                valid_to_epoch=hit.valid_to_epoch,
                authority_rank=max(1, self.final_evidence_count - index + 1),
                permission_revision=hit.permission_revision,
                authorized=True,
                current_version=hit.current_version,
            )
            for index, hit in enumerate(result.hits, start=1)
        )
        return EvidencePackage(
            rag_run_id=new_uuid7(),
            tenant_id=tenant_id,
            user_id=user_id,
            query=question,
            query_time_epoch=query_time,
            index_generation_id=active_generation_id,
            retrieval_revision=self.search_service.revision,
            prompt_revision=self.prompt_revision,
            model_revision=self.model_revision,
            permission_revision=permission_revision,
            evidence=evidence,
            verifier_revision=self.verifier_revision,
            real_acceptance=result.real_acceptance,
        )
