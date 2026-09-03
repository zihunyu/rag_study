"""Trusted QA orchestration with buffered generation and final fail-closed verification."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict

from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.rag import (
    BufferedGenerationPort,
    CitationReferencePort,
    EvidenceProviderPort,
    FinalPermissionPort,
    RAGRunRepositoryPort,
    VerifiedAnswerCachePort,
)
from ragkb.domain.errors import InvalidProviderResponse, RetrievalFailClosed, TransientProviderError
from ragkb.domain.ids import new_uuid7
from ragkb.domain.rag import (
    AnswerStatus,
    AskResult,
    Citation,
    DraftAnswer,
    EvidencePackage,
    Feedback,
    QuestionDisposition,
)


class TrustedQAService:
    revision = "trusted-qa:g3-v1"

    def __init__(
        self,
        evidence_provider: EvidenceProviderPort,
        generator: BufferedGenerationPort,
        permission: FinalPermissionPort,
        references: CitationReferencePort,
        repository: RAGRunRepositoryPort,
        cache: VerifiedAnswerCachePort | None = None,
        tracer: TracerPort | None = None,
    ) -> None:
        self.evidence_provider = evidence_provider
        self.generator = generator
        self.permission = permission
        self.references = references
        self.repository = repository
        self.cache = cache
        self.tracer = tracer or InMemoryTracer()

    def _save(
        self,
        package: EvidencePackage,
        status: AnswerStatus,
        *,
        answer: str | None = None,
        citations: tuple[Citation, ...] = (),
        warnings: tuple[str, ...] = (),
        verified: bool = False,
    ) -> AskResult:
        result = AskResult(
            rag_run_id=package.rag_run_id,
            status=status,
            answer=answer,
            citations=citations,
            evidence=package.evidence,
            warnings=warnings,
            verified=verified,
            real_acceptance=package.real_acceptance and verified,
        )
        self.repository.save_run(package, result)
        return result

    def _permission_recheck(
        self, package: EvidencePackage, subject_scope_tokens: tuple[str, ...]
    ) -> bool:
        return self.permission.recheck(
            package.evidence,
            tenant_id=package.tenant_id,
            user_id=package.user_id,
            subject_scope_tokens=subject_scope_tokens,
            permission_revision=package.permission_revision,
            at_epoch=int(time.time()),
        )

    def _ask(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
    ) -> AskResult:
        try:
            with self.tracer.span("rag.ask.evidence.build"):
                package = self.evidence_provider.build_package(
                    question,
                    tenant_id,
                    user_id,
                    subject_scope_tokens=subject_scope_tokens,
                )
        except (RetrievalFailClosed, TransientProviderError):
            package = EvidencePackage(
                rag_run_id=new_uuid7(),
                tenant_id=tenant_id,
                user_id=user_id,
                query=question,
                query_time_epoch=int(time.time()),
                index_generation_id="unavailable",
                retrieval_revision=self.evidence_provider.revision,
                prompt_revision=self.generator.revision,
                model_revision=self.generator.revision,
                permission_revision=0,
                evidence=(),
                real_acceptance=False,
            )
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("RETRIEVAL_OR_PERMISSION_FAIL_CLOSED",),
            )
        if package.disposition is QuestionDisposition.OUT_OF_SCOPE:
            return self._save(package, AnswerStatus.OUT_OF_SCOPE, verified=True)
        if package.disposition is QuestionDisposition.NEEDS_CLARIFICATION:
            return self._save(package, AnswerStatus.NEEDS_CLARIFICATION, verified=True)
        if not package.evidence:
            return self._save(package, AnswerStatus.INSUFFICIENT_EVIDENCE, verified=True)
        if package.conflict_detected:
            return self._save(package, AnswerStatus.CONFLICTING_EVIDENCE, verified=True)
        if not all(
            evidence.authorized
            and evidence.current_version
            and evidence.valid_at(package.query_time_epoch)
            and evidence.locator
            for evidence in package.evidence
        ):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("EVIDENCE_VALIDATION_FAILED",),
            )
        if not self._permission_recheck(package, subject_scope_tokens):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("PRE_GENERATION_PERMISSION_RECHECK_FAILED",),
            )
        draft = self.cache.get(package) if self.cache is not None else None
        try:
            if draft is None:
                with self.tracer.span("rag.ask.llm.generate"):
                    draft = self.generator.generate(question, package.evidence)
        except (TransientProviderError, InvalidProviderResponse):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("GENERATION_UNAVAILABLE_AUTHORIZED_EVIDENCE_ONLY",),
            )
        available = {item.evidence_id: item for item in package.evidence}
        if (
            not draft.text.strip()
            or not draft.citation_ids
            or len(set(draft.citation_ids)) != len(draft.citation_ids)
            or any(evidence_id not in available for evidence_id in draft.citation_ids)
        ):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("CITATION_VALIDATION_FAILED",),
            )
        cited = tuple(available[evidence_id] for evidence_id in draft.citation_ids)
        if not self._permission_recheck(package, subject_scope_tokens):
            return self._save(
                package,
                AnswerStatus.SYSTEM_ERROR,
                warnings=("FINAL_PERMISSION_RECHECK_FAILED",),
            )
        with self.tracer.span("rag.ask.citation.verify"):
            citations = tuple(
                Citation(
                    evidence_id=evidence.evidence_id,
                    source_url=self.references.source_url(
                        package.rag_run_id,
                        evidence.evidence_id,
                        package.tenant_id,
                        user_id,
                        evidence.document_id,
                    ),
                    locator=evidence.locator,
                )
                for evidence in cited
            )
        if self.cache is not None:
            self.cache.put(package, draft)
        return self._save(
            package,
            AnswerStatus.ANSWERED,
            answer=draft.text,
            citations=citations,
            verified=True,
        )

    def ask(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
    ) -> AskResult:
        with self.tracer.span("rag.ask", {"tenant_id": tenant_id}):
            return self._ask(
                question,
                tenant_id,
                user_id,
                subject_scope_tokens=subject_scope_tokens,
            )

    def feedback(
        self,
        rag_run_id: str,
        user_id: str,
        rating: int,
        reason_code: str,
        comment: str,
    ) -> Feedback:
        result = self.repository.get_result(rag_run_id)
        if result is None:
            raise KeyError(rag_run_id)
        package = self.repository.get_package(rag_run_id)
        if package is None:
            raise KeyError(rag_run_id)
        if package.user_id != user_id:
            raise KeyError(rag_run_id)
        feedback = Feedback(
            rag_run_id=rag_run_id,
            user_id=user_id,
            rating=rating,
            reason_code=reason_code,
            comment=comment,
            index_generation_id=package.index_generation_id,
            retrieval_revision=package.retrieval_revision,
            prompt_revision=package.prompt_revision,
            model_revision=package.model_revision,
        )
        self.repository.save_feedback(feedback)
        return feedback


class InMemoryVerifiedAnswerCache:
    """Caches only verified drafts and naturally invalidates on evidence or revision changes."""

    def __init__(self, *, max_entries: int = 1024) -> None:
        self.max_entries = max_entries
        self._values: dict[str, DraftAnswer] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(package: EvidencePackage) -> str:
        payload = {
            "tenant_id": package.tenant_id,
            "user_id": package.user_id,
            "permission_revision": package.permission_revision,
            "query": " ".join(package.query.casefold().split()),
            "index_generation_id": package.index_generation_id,
            "retrieval_revision": package.retrieval_revision,
            "prompt_revision": package.prompt_revision,
            "model_revision": package.model_revision,
            "evidence": [asdict(item) for item in package.evidence],
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, package: EvidencePackage) -> DraftAnswer | None:
        with self._lock:
            return self._values.get(self._key(package))

    def put(self, package: EvidencePackage, draft: DraftAnswer) -> None:
        key = self._key(package)
        with self._lock:
            if len(self._values) >= self.max_entries:
                self._values.pop(next(iter(self._values)))
            self._values[key] = draft
