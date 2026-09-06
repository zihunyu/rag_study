"""Search-backed local evidence assembly for trusted QA."""

from __future__ import annotations

import time
from collections.abc import Callable

from ragkb.application.question_assessment import ConservativeQuestionAssessor
from ragkb.application.search import HybridSearchService
from ragkb.contracts.ports import RetrievalReleasePort
from ragkb.contracts.rag import QuestionAssessmentPort
from ragkb.domain.errors import (
    InvalidProviderResponse,
    QuestionAssessmentFailed,
    TransientProviderError,
)
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import Evidence, EvidencePackage, QuestionAssessment, QuestionDisposition
from ragkb.domain.retrieval import SearchContext, SearchHit, SearchSource


class SearchBackedEvidenceProvider:
    revision = "search-backed-evidence:question-assessment:v4"

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
        question_assessor: QuestionAssessmentPort | None = None,
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
        self.question_assessor = question_assessor or ConservativeQuestionAssessor()

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
        try:
            assessment = self.question_assessor.assess(question)
            if not isinstance(assessment, QuestionAssessment):
                raise ValueError("QUESTION_ASSESSMENT_INVALID")
        except TransientProviderError as error:
            raise QuestionAssessmentFailed(
                "QUESTION_ASSESSOR_UNAVAILABLE", retryable=True
            ) from error
        except (InvalidProviderResponse, ValueError) as error:
            raise QuestionAssessmentFailed(
                "QUESTION_ASSESSOR_PROTOCOL_INVALID", retryable=False
            ) from error
        query_time = int(self.clock())
        if assessment.disposition is not QuestionDisposition.ANSWERABLE:
            return EvidencePackage(
                rag_run_id=new_uuid7(),
                tenant_id=tenant_id,
                user_id=user_id,
                query=question,
                query_time_epoch=query_time,
                index_generation_id="not_retrieved",
                retrieval_revision=self.search_service.revision,
                prompt_revision=self.prompt_revision,
                model_revision=self.model_revision,
                permission_revision=0,
                evidence=(),
                verifier_revision=self.verifier_revision,
                disposition=assessment.disposition,
                disposition_reason=assessment.reason_code,
                clarification_fields=assessment.clarification_fields,
                question_assessor_revision=self.question_assessor.revision,
            )
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
        evidence: list[Evidence] = []
        seen_sources: set[tuple[str, str, str]] = set()

        def append_source(source: SearchHit | SearchSource, *, review_only: bool = False) -> None:
            key = (source.document_id, source.document_version_id, source.chunk_id)
            if key in seen_sources:
                return
            seen_sources.add(key)
            parts: list[str] = []
            section_path = str(source.locator.get("section_path") or "").strip()
            if section_path and section_path != "root":
                parts.append(f"SECTION_PATH: {section_path}")
            parts.append(source.retrieval_text or source.display_text)
            evidence.append(
                Evidence(
                    evidence_id=f"E{len(evidence) + 1}",
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    document_version_id=source.document_version_id,
                    text="\n".join(parts),
                    display_text=source.display_text,
                    locator=source.locator,
                    valid_from_epoch=source.valid_from_epoch,
                    valid_to_epoch=source.valid_to_epoch,
                    # No governance-backed authority metadata is currently available.
                    # Retrieval order is relevance, never institutional precedence.
                    authority_rank=0,
                    permission_revision=source.permission_revision,
                    authorized=True,
                    current_version=source.current_version,
                    source_role=(
                        "conflict_context"
                        if review_only
                        else "hit"
                        if isinstance(source, SearchHit)
                        else "parent_context"
                    ),
                    parent_chunk_id=(
                        source.parent_chunk_id if isinstance(source, SearchHit) else None
                    ),
                )
            )

        # Preserve hit ordering/IDs, then add each parent once with its own location.
        # A parent that is also a hit already has a citable evidence ID.
        for hit in result.hits:
            append_source(hit)
        hit_texts = {
            (hit.document_id, hit.document_version_id, hit.retrieval_text or hit.display_text)
            for hit in result.hits
        }
        for hit in result.hits:
            parent = hit.parent_source
            if (
                parent is not None
                and (
                    parent.document_id,
                    parent.document_version_id,
                    parent.retrieval_text or parent.display_text,
                )
                not in hit_texts
            ):
                append_source(parent)
        for source in result.review_sources:
            append_source(source, review_only=True)
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
            evidence=tuple(evidence),
            verifier_revision=self.verifier_revision,
            real_acceptance=result.real_acceptance,
            retrieval_health=result.retrieval_health,
            retrieval_warnings=result.warnings,
            disposition_reason=assessment.reason_code,
            question_assessor_revision=self.question_assessor.revision,
        )
