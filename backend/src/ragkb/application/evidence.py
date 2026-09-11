"""Search-backed local evidence assembly for trusted QA."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ragkb.application.evidence_packing import pack_sources, source_limit
from ragkb.application.provider_budget import ConservativeTokenCounter
from ragkb.application.qa_budget import choose_profile, current
from ragkb.application.qa_performance import record_event
from ragkb.application.question_assessment import ConservativeQuestionAssessor
from ragkb.application.reading_scope import is_overview, options
from ragkb.application.search import HybridSearchService
from ragkb.contracts.ports import RetrievalReleasePort
from ragkb.contracts.rag import EvidenceSelectorPort, OverviewReadingPort, QuestionAssessmentPort
from ragkb.domain.errors import (
    InvalidProviderResponse,
    QABudgetExceeded,
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
    revision = "search-backed-evidence:packed-coverage-v8"

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
        readable_scope: Callable[[SearchContext], bool | None] | None = None,
        deep_max_subqueries: int = 8,
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
        self.readable_scope = readable_scope
        self.deep_max_subqueries = deep_max_subqueries

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
            document_ids=options.get().document_ids,
        )
        if (
            self.readable_scope is not None
            and ConservativeQuestionAssessor().assess(question).disposition
            is QuestionDisposition.ANSWERABLE
        ):
            if self.search_service.index.observed_security_watermark(context) < required_watermark:
                raise SecurityWatermarkNotReady("SECURITY_WATERMARK_NOT_READY")
            readable = self.readable_scope(context)
            record_event(
                "reading_scope",
                outcome="empty" if readable is False else "readable" if readable else "unknown",
            )
            if readable is False:
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
                    evidence=(),
                    verifier_revision=self.verifier_revision,
                    coverage="missing",
                    disposition_reason="NO_READABLE_CURRENT_SOURCES",
                    question_assessor_revision=self.question_assessor.revision,
                )
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
        budget = current.get()
        profile = budget.profile if budget else choose_profile(question)
        query_maximum = (
            self.deep_max_subqueries
            if profile == "deep"
            else getattr(self.search_service, "max_subqueries", 4)
        )
        if profile == "simple":
            query_maximum = min(2, query_maximum)
        if not getattr(self.search_service, "query_planning_enabled", True):
            query_maximum = 1
        initial_limit = min(query_maximum, 1 if profile == "simple" else 2)
        result = self.search_service.search(
            question,
            context,
            limit=self.final_evidence_count,
            query_limit=initial_limit,
        )
        queries = list(result.retrieval_queries or (question.strip(),))
        evidence: list[Evidence] = []
        seen_sources: set[tuple[str, str, str]] = set()
        parent_ids: set[str] = set()

        def append_source(source: SearchHit | SearchSource, *, review_only: bool = False) -> None:
            if options.get().document_ids and source.document_id not in options.get().document_ids:
                return
            key = (source.document_id, source.document_version_id, source.chunk_id)
            if key in seen_sources:
                for index, existing in enumerate(evidence):
                    if (
                        existing.document_id,
                        existing.document_version_id,
                        existing.chunk_id,
                    ) == key:
                        evidence[index] = replace(
                            existing,
                            retrieval_queries=tuple(
                                dict.fromkeys(
                                    (*existing.retrieval_queries, *source.retrieval_queries)
                                )
                            ),
                        )
                        break
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
                    retrieval_queries=source.retrieval_queries,
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
        record_event(
            "retrieval_round",
            number=1,
            new_sources=len(evidence),
            reason="initial",
            health=str(result.retrieval_health),
        )
        check_visuals()
        retrieval_round = 1
        coverage, clarification = "unchecked", None
        health = result.retrieval_health
        warnings = list(result.warnings)
        stop_reason = (
            "provider_unavailable" if health is RetrievalHealth.UNAVAILABLE else "not_assessed"
        )
        missing_aspects: tuple[str, ...] = ()
        blocking_missing = False
        aspect_sources: tuple[dict[str, Any], ...] = ()
        packing_omitted: list[str] = []
        if self.evidence_selector is not None and health is not RetrievalHealth.UNAVAILABLE:
            try:
                choice = (
                    visual_session.preselected(tuple(evidence)) if visual_session else None
                ) or self.evidence_selector.select(question, tuple(evidence))
                # One targeted query at a time, reassessed only when evidence grows.
                # A supplemental query must never spawn another compound plan.
                while choice.coverage in {"partial", "missing"}:
                    if len(queries) >= query_maximum:
                        stop_reason = "retrieval_budget_exhausted"
                        break
                    query = next(
                        (
                            q.strip()
                            for q in choice.queries
                            if q.strip() and q.strip() not in queries
                        ),
                        None,
                    )
                    if query is None:
                        stop_reason = "no_followup_query"
                        break
                    try:
                        before_input = deepcopy(
                            tuple(replace(e, retrieval_queries=()) for e in evidence)
                        )
                        before_count = len(evidence)
                        queries.append(query)
                        extra = self.search_service.search(
                            query,
                            context,
                            limit=self.final_evidence_count,
                            query_limit=1,
                            exclude_queries=tuple(queries[:-1]),
                        )
                        retrieval_round += 1
                        collect(extra)
                        record_event(
                            "retrieval_round",
                            number=retrieval_round,
                            new_sources=len(evidence) - before_count,
                            reason="supplement_" + choice.coverage,
                            health=str(extra.retrieval_health),
                        )
                        warnings.extend(extra.warnings)
                        if extra.retrieval_health is RetrievalHealth.UNAVAILABLE:
                            health = RetrievalHealth.UNAVAILABLE
                        elif (
                            extra.retrieval_health is RetrievalHealth.DEGRADED
                            and health is RetrievalHealth.HEALTHY
                        ):
                            health = RetrievalHealth.DEGRADED
                        if health is RetrievalHealth.UNAVAILABLE:
                            stop_reason = "provider_unavailable"
                            break
                        check_visuals(final=True)
                        if (
                            tuple(replace(e, retrieval_queries=()) for e in evidence)
                            == before_input
                        ):
                            stop_reason = "no_new_evidence"
                            break
                        previous_ids = set(choice.source_ids)
                        previous_missing = choice.missing_aspects
                        choice = (
                            visual_session.preselected(tuple(evidence)) if visual_session else None
                        ) or self.evidence_selector.select(question, tuple(evidence))
                        if (
                            choice.coverage in {"partial", "missing"}
                            and set(choice.source_ids) <= previous_ids
                            and choice.missing_aspects == previous_missing
                        ):
                            stop_reason = "no_useful_new_evidence"
                            break
                    except QABudgetExceeded:
                        stop_reason = "model_budget_exhausted"
                        break  # Keep the last assessed evidence for the reserved answer stages.
                if choice.coverage in {"sufficient", "ambiguous"}:
                    stop_reason = choice.coverage
                if deferred_visuals and health is not RetrievalHealth.UNAVAILABLE:
                    before_visuals = tuple(evidence)
                    try:
                        check_visuals(final=True)
                        if tuple(evidence) != before_visuals:
                            choice = (
                                visual_session.preselected(tuple(evidence))
                                if visual_session
                                else None
                            ) or self.evidence_selector.select(question, tuple(evidence))
                    except QABudgetExceeded:
                        stop_reason = "model_budget_exhausted"
                if choice.coverage in {"sufficient", "ambiguous"}:
                    stop_reason = choice.coverage
                elif stop_reason == "sufficient":
                    stop_reason = "visual_review_incomplete"
                coverage, clarification = choice.coverage, choice.clarification
                missing_aspects, blocking_missing = choice.missing_aspects, choice.blocking_missing
                aspect_sources = choice.aspect_sources
                counter = ConservativeTokenCounter()
                by_id = {item.evidence_id: item for item in evidence}
                packed = pack_sources(
                    question,
                    [by_id[key] for key in choice.source_ids],
                    token_limit=8000,
                    cost=lambda e: counter.count(e.text),
                    maximum=source_limit(question, self.final_evidence_count),
                    aspect_sources=aspect_sources,
                )
                selected = {item.evidence_id for item in packed}
                packing_omitted = [key for key in choice.source_ids if key not in selected]
                if packing_omitted:
                    coverage = "partial" if selected else "missing"
                    stop_reason = "evidence_packing_limit"
                    warnings.append("EVIDENCE_PACKING_INCOMPLETE")
                    aspect_sources = tuple(
                        {
                            **row,
                            "status": (
                                "partial"
                                if set(row.get("evidence_ids", ())) & selected
                                else "missing"
                            )
                            if set(row.get("evidence_ids", ())) - selected
                            else row["status"],
                            "evidence_ids": [
                                key for key in row.get("evidence_ids", ()) if key in selected
                            ],
                        }
                        for row in aspect_sources
                    )
                    from ragkb.domain.question_coverage import required_aspects

                    gaps = {
                        row["aspect_id"] for row in aspect_sources if row["status"] != "supported"
                    }
                    missing_aspects = tuple(
                        dict.fromkeys(
                            (
                                *missing_aspects,
                                *(
                                    row["question"]
                                    for row in required_aspects(question)
                                    if row["aspect_id"] in gaps
                                ),
                            )
                        )
                    )
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
        if budget:
            queries = list(budget.queries)
        record_event(
            "retrieval_budget",
            maximum=query_maximum,
            used=len(queries),
            remaining=max(0, query_maximum - len(queries)),
            profile=profile,
            stop_reason=stop_reason,
        )
        missing_crops = [
            e.evidence_id
            for e in evidence
            if e.source_role != "conflict_context"
            and e.locator.get("qa_structure", {}).get("visual_gap")
        ]
        coverage_report = {
            "complete": coverage == "sufficient",
            "missing_aspects": list(missing_aspects),
            "aspect_sources": list(aspect_sources),
            "blocking_missing": blocking_missing,
            "packing_omitted_evidence_ids": packing_omitted,
            "retrieval_budget": {
                "profile": profile,
                "maximum": query_maximum,
                "used": len(queries),
                "remaining": max(0, query_maximum - len(queries)),
                "initial_limit": initial_limit,
                "stop_reason": stop_reason,
            },
        }
        if stop_reason in {"retrieval_budget_exhausted", "model_budget_exhausted"}:
            warnings.append("RETRIEVAL_BUDGET_EXHAUSTED")
            coverage_report["complete"] = False
        if missing_crops:
            coverage = "partial"
            warnings.append("VISUAL_SOURCE_CROP_MISSING")
            coverage_report = {
                **coverage_report,
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
