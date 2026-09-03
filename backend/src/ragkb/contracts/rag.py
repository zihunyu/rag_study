"""G3 trusted QA ports."""

from __future__ import annotations

from typing import Protocol

from ragkb.domain.rag import AskResult, DraftAnswer, Evidence, EvidencePackage, Feedback


class EvidenceProviderPort(Protocol):
    revision: str

    def build_package(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        *,
        subject_scope_tokens: tuple[str, ...] = (),
    ) -> EvidencePackage: ...


class BufferedGenerationPort(Protocol):
    revision: str

    def generate(self, question: str, evidence: tuple[Evidence, ...]) -> DraftAnswer: ...


class VerifiedAnswerCachePort(Protocol):
    def get(self, package: EvidencePackage) -> DraftAnswer | None: ...

    def put(self, package: EvidencePackage, draft: DraftAnswer) -> None: ...


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
