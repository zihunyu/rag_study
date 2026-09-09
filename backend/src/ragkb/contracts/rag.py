"""G3 trusted QA ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ragkb.domain.rag import (
    AskResult,
    DraftAnswer,
    Evidence,
    EvidencePackage,
    Feedback,
    QuestionAssessment,
    VerificationResult,
)
from ragkb.domain.retrieval import SearchContext


@dataclass(frozen=True)
class EvidenceSelection:
    source_ids: tuple[str, ...]
    coverage: str = "sufficient"
    queries: tuple[str, ...] = ()
    clarification: str | None = None


class EvidenceSelectorPort(Protocol):
    revision: str

    def select(self, question: str, evidence: tuple[Evidence, ...]) -> EvidenceSelection: ...


class QuestionAssessmentPort(Protocol):
    revision: str

    def assess(self, question: str) -> QuestionAssessment: ...


class EvidenceProviderPort(Protocol):
    revision: str

    def build_package(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
        clearance_level: int = 0,
        space_id: str | None = None,
    ) -> EvidencePackage: ...


class BufferedGenerationPort(Protocol):
    revision: str

    def generate(self, question: str, evidence: tuple[Evidence, ...]) -> DraftAnswer: ...


class ClaimVerifierPort(Protocol):
    """Verify claims using their cited IDs, and conflicts using all supplied evidence."""

    revision: str

    def verify(
        self, question: str, draft: DraftAnswer, evidence: tuple[Evidence, ...]
    ) -> VerificationResult: ...


@runtime_checkable
class CitationRepairPort(Protocol):
    def repair_citations(
        self, question: str, draft: DraftAnswer, evidence: tuple[Evidence, ...]
    ) -> DraftAnswer: ...


class VerifiedAnswerCachePort(Protocol):
    def get(self, package: EvidencePackage) -> DraftAnswer | None: ...

    def put(self, package: EvidencePackage, draft: DraftAnswer) -> None: ...


class ExactAnswerReusePort(Protocol):
    def execute(
        self, service: Any, question: str, tenant: str, user: str, **scope: Any
    ) -> AskResult: ...


class FinalPermissionPort(Protocol):
    def recheck(
        self,
        evidence: tuple[Evidence, ...],
        *,
        tenant_id: str,
        user_id: str,
        subject_scope_tokens: tuple[str, ...],
        permission_revision: int,
        at_epoch: int,
        clearance_level: int = 0,
        generation_id: str = "",
    ) -> bool: ...


class CitationReferencePort(Protocol):
    def source_url(
        self,
        run_id: str,
        evidence_id: str,
        tenant_id: str,
        user_id: str,
        document_id: str,
    ) -> str: ...

    def resolve(
        self, run_token: str, evidence_token: str, tenant_id: str, user_id: str
    ) -> tuple[str, str]: ...


class RAGRunRepositoryPort(Protocol):
    def save_run(self, package: EvidencePackage, result: AskResult) -> None: ...

    def get_result(self, run_id: str) -> AskResult | None: ...

    def get_package(self, run_id: str) -> EvidencePackage | None: ...

    def save_feedback(self, feedback: Feedback) -> None: ...

    def get_evidence(self, run_id: str, evidence_id: str) -> Evidence | None: ...


class OverviewReadingPort(Protocol):
    def read(
        self, question: str, context: SearchContext
    ) -> tuple[tuple[Evidence, ...], dict[str, Any]]: ...

    def related_sources(
        self, evidence: tuple[Evidence, ...], context: SearchContext
    ) -> tuple[Evidence, ...]: ...
