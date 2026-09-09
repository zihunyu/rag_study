"""Search-backed local evidence assembly for trusted QA."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import replace

from ragkb.application.provider_budget import ConservativeTokenCounter
from ragkb.application.question_assessment import ConservativeQuestionAssessor
from ragkb.application.reading_scope import is_overview, options
from ragkb.application.search import HybridSearchService
from ragkb.contracts.ports import RetrievalReleasePort
from ragkb.contracts.rag import EvidenceSelectorPort, OverviewReadingPort, QuestionAssessmentPort
from ragkb.domain.errors import (
    InvalidProviderResponse,
    QuestionAssessmentFailed,
    TransientProviderError,
)
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import Evidence, EvidencePackage, QuestionAssessment, QuestionDisposition
from ragkb.domain.retrieval import (
    RetrievalHealth,
    SearchContext,
    SearchHit,
    SearchResult,
    SearchSource,
    SecurityWatermarkNotReady,
)


class SearchBackedEvidenceProvider:
    revision = "search-backed-evidence:staged-visual-facts-v3"

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
        evidence_selector: EvidenceSelectorPort | None = None,
        visual_enricher: Callable[[str, tuple[Evidence, ...]], tuple[Evidence, ...]] | None = None,
        overview_reader: OverviewReadingPort | None = None,
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
        self.evidence_selector = evidence_selector
        self.visual_enricher = visual_enricher
        self.overview_reader = overview_reader

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
        # A standalone name/topic can be resolved against the selected knowledge
        # base. Unresolved pronouns and external-operation refusals remain early exits.
        if (
            assessment.disposition is QuestionDisposition.NEEDS_CLARIFICATION
            and assessment.clarification_fields == ("subject",)
            and re.fullmatch(r"[\w\s\-·“”\"《》]{1,80}", question.strip())
            and not re.search(
                r"它|这个|那个|该产品|\b(it|its|that|this|they|their)\b", question, re.I
            )
            and self.evidence_selector is not None
        ):
            assessment = QuestionAssessment()
        if (
            self.overview_reader
            and is_overview(question)
            and assessment.disposition is QuestionDisposition.NEEDS_CLARIFICATION
            and not re.search(r"它|那个|上面|\b(it|that)\b", question, re.I)
        ):
            assessment = QuestionAssessment()
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
        if self.overview_reader and is_overview(question):
            if self.search_service.index.observed_security_watermark(context) < required_watermark:
                raise SecurityWatermarkNotReady("SECURITY_WATERMARK_NOT_READY")
            contents, report = self.overview_reader.read(question, context)
            return EvidencePackage(
                rag_run_id=new_uuid7(),
                tenant_id=tenant_id,
                user_id=user_id,
                query=question,
                query_time_epoch=query_time,
                index_generation_id=active_generation_id,
                retrieval_revision=self.search_service.revision + ":chapter-reading-v1",
                prompt_revision=self.prompt_revision + ":overview-v1",
                model_revision=self.model_revision,
                permission_revision=permission_revision,
                evidence=contents,
                verifier_revision=self.verifier_revision,
                coverage_report=report,
                coverage="complete" if report["complete"] else "partial",
                retrieval_warnings=() if report["complete"] else ("DOCUMENT_COVERAGE_INCOMPLETE",),
                retrieval_queries=(question,),
            )
        result = self.search_service.search(
            question,
            context,
            limit=self.final_evidence_count,
        )
        evidence: list[Evidence] = []
        seen_sources: set[tuple[str, str, str]] = set()
        parent_ids: set[str] = set()

        def append_source(source: SearchHit | SearchSource, *, review_only: bool = False) -> None:
            if options.get().document_ids and source.document_id not in options.get().document_ids:
                return
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

        def collect(found: SearchResult) -> None:
            for hit in found.hits:
                append_source(hit)
            for hit in found.hits:
                parent = hit.parent_source
                if parent is not None:
                    parent_ids.add(parent.chunk_id)
                    if parent.retrieval_text not in {entry.retrieval_text for entry in found.hits}:
                        append_source(parent)
            for source in found.review_sources:
                append_source(source, review_only=True)
            if self.evidence_selector is not None:
                for parent in self.search_service.expand_parents(found.review_sources, context):
                    parent_ids.add(parent.chunk_id)
                    append_source(parent, review_only=True)

        visual_session_factory = getattr(self.visual_enricher, "session", None)
        visual_session = (
            visual_session_factory(question) if callable(visual_session_factory) else None
        )

        deferred_visuals: list[Evidence] = []
        figure_reference_sources: dict[tuple[str, str], Evidence] = {}

        def check_visuals(*, final: bool = False) -> None:
            nonlocal evidence
            for item in deferred_visuals:
                if not any(
                    (e.document_version_id, e.chunk_id) == (item.document_version_id, item.chunk_id)
                    for e in evidence
                ):
                    evidence.append(replace(item, evidence_id=f"E{len(evidence) + 1}"))
            deferred_visuals.clear()
            if self.overview_reader:
                for related in self.overview_reader.related_sources(tuple(evidence), context):
                    key = (related.document_id, related.document_version_id, related.chunk_id)
                    if key not in seen_sources:
                        seen_sources.add(key)
                        evidence.append(replace(related, evidence_id=f"E{len(evidence) + 1}"))
                annotate = getattr(self.overview_reader, "annotate_associations", None)
                if callable(annotate):
                    for item in evidence:
                        figure_reference_sources.setdefault(
                            (item.document_version_id, item.chunk_id), item
                        )
                    evidence = list(
                        annotate(
                            tuple(evidence),
                            reference_sources=tuple(figure_reference_sources.values()),
                        )
                    )
            if visual_session is not None:
                reserve = (
                    1
                    if self.evidence_selector is not None
                    and not final
                    and visual_session.owner.max_images > 1
                    else 0
                )
                evidence = list(visual_session(tuple(evidence), reserve_images=reserve))
                deferred_visuals.extend(visual_session.deferred)
            elif self.visual_enricher is not None:
                evidence = list(self.visual_enricher(question, tuple(evidence)))

        collect(result)
        check_visuals()
        queries = [question]
        coverage, clarification = "unchecked", None
        health = result.retrieval_health
        warnings = list(result.warnings)
        if self.evidence_selector is not None and health is not RetrievalHealth.UNAVAILABLE:
            try:
                choice = (
                    visual_session.preselected(tuple(evidence)) if visual_session else None
                ) or self.evidence_selector.select(question, tuple(evidence))
                # One supplemental round, at most two queries; same tenant, space,
                # release and permission context. No bypass of retrieval fences.
                if choice.coverage in {"partial", "missing"}:
                    for query in choice.queries[:2]:
                        if query.strip().casefold() in {q.casefold() for q in queries}:
                            continue
                        queries.append(query.strip())
                        extra = self.search_service.search(
                            query, context, limit=self.final_evidence_count
                        )
                        collect(extra)
                        warnings.extend(extra.warnings)
                        if extra.retrieval_health is RetrievalHealth.UNAVAILABLE:
                            health = RetrievalHealth.UNAVAILABLE
                        elif (
                            extra.retrieval_health is RetrievalHealth.DEGRADED
                            and health is RetrievalHealth.HEALTHY
                        ):
                            health = RetrievalHealth.DEGRADED
                if (
                    len(queries) > 1 or deferred_visuals
                ) and health is not RetrievalHealth.UNAVAILABLE:
                    check_visuals(final=True)
                    choice = (
                        visual_session.preselected(tuple(evidence)) if visual_session else None
                    ) or self.evidence_selector.select(question, tuple(evidence))
                coverage, clarification = choice.coverage, choice.clarification
                selected: set[str] = set()
                used = 0
                counter = ConservativeTokenCounter()
                by_id = {item.evidence_id: item for item in evidence}
                for identity in choice.source_ids[: self.final_evidence_count]:
                    item = by_id[identity]
                    size = counter.count(item.text)
                    if used + size <= 8000:
                        selected.add(identity)
                        used += size
                evidence = [
                    replace(
                        item,
                        source_role=(
                            ("parent_context" if item.chunk_id in parent_ids else "hit")
                            if item.evidence_id in selected
                            else "conflict_context"
                        ),
                    )
                    for item in evidence
                ]
            except TransientProviderError as error:
                raise QuestionAssessmentFailed(
                    "EVIDENCE_SELECTION_UNAVAILABLE", retryable=True
                ) from error
            except (InvalidProviderResponse, KeyError) as error:
                raise QuestionAssessmentFailed(
                    "EVIDENCE_SELECTION_PROTOCOL_INVALID", retryable=False
                ) from error
        if visual_session is not None:
            warnings.extend(visual_session.warnings)
        missing_crops = [
            e.evidence_id
            for e in evidence
            if e.source_role != "conflict_context"
            and e.locator.get("qa_structure", {}).get("visual_gap")
        ]
        coverage_report = {}
        if missing_crops:
            coverage = "partial"
            warnings.append("VISUAL_SOURCE_CROP_MISSING")
            coverage_report = {
                "complete": False,
                "gaps": ["相关图表没有可用裁图，图中专有内容尚未确认。"],
                "missing_crop_evidence_ids": missing_crops,
            }
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
            retrieval_health=health,
            retrieval_warnings=tuple(dict.fromkeys(warnings)),
            disposition_reason="missing_context"
            if coverage == "ambiguous"
            else assessment.reason_code,
            question_assessor_revision=self.question_assessor.revision,
            coverage=coverage,
            retrieval_queries=tuple(queries),
            clarification_question=clarification,
            disposition=(
                QuestionDisposition.NEEDS_CLARIFICATION
                if coverage == "ambiguous"
                else QuestionDisposition.ANSWERABLE
            ),
            clarification_fields=("subject",) if coverage == "ambiguous" else (),
            coverage_report=coverage_report,
        )
