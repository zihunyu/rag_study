"""Trusted RAG evidence, answer state and citation contracts for G3."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from ragkb.domain.retrieval import RetrievalHealth


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_CLARIFICATION = "needs_clarification"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    OUT_OF_SCOPE = "out_of_scope"
    SYSTEM_ERROR = "system_error"


class QuestionDisposition(StrEnum):
    ANSWERABLE = "answerable"
    NEEDS_CLARIFICATION = "needs_clarification"
    OUT_OF_SCOPE = "out_of_scope"


CLARIFICATION_FIELDS = frozenset({"subject", "product", "version", "region", "time_period"})


@dataclass(frozen=True)
class QuestionAssessment:
    disposition: QuestionDisposition = QuestionDisposition.ANSWERABLE
    reason_code: str = "standalone_question"
    clarification_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected = {
            QuestionDisposition.ANSWERABLE: {"standalone_question"},
            QuestionDisposition.NEEDS_CLARIFICATION: {"missing_context"},
            QuestionDisposition.OUT_OF_SCOPE: {"unsupported_operation", "outside_knowledge_qa"},
        }
        if (
            not isinstance(self.disposition, QuestionDisposition)
            or not isinstance(self.reason_code, str)
            or self.reason_code not in expected[self.disposition]
            or not isinstance(self.clarification_fields, tuple)
            or any(
                not isinstance(field, str) or field not in CLARIFICATION_FIELDS
                for field in self.clarification_fields
            )
            or len(set(self.clarification_fields)) != len(self.clarification_fields)
            or bool(self.clarification_fields)
            != (self.disposition is QuestionDisposition.NEEDS_CLARIFICATION)
        ):
            raise ValueError("QUESTION_ASSESSMENT_INVALID")


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    chunk_id: str
    document_id: str
    document_version_id: str
    text: str
    locator: dict[str, Any]
    valid_from_epoch: int
    valid_to_epoch: int
    authority_rank: int
    permission_revision: int
    authorized: bool
    current_version: bool
    source_role: Literal["hit", "parent_context", "conflict_context"] = "hit"
    parent_chunk_id: str | None = None
    display_text: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id.startswith("E") or not self.evidence_id[1:].isdigit():
            raise ValueError("evidence IDs must use E1...En")
        if not self.text.strip() or not self.locator:
            raise ValueError("evidence text and locator are required")
        if self.valid_from_epoch < 0 or self.valid_to_epoch < 0:
            raise ValueError("evidence validity must be non-negative")

    def valid_at(self, timestamp: int) -> bool:
        return self.valid_from_epoch <= timestamp and (
            self.valid_to_epoch == 0 or self.valid_to_epoch > timestamp
        )


@dataclass(frozen=True)
class EvidencePackage:
    rag_run_id: str
    tenant_id: str
    user_id: str
    query: str
    query_time_epoch: int
    index_generation_id: str
    retrieval_revision: str
    prompt_revision: str
    model_revision: str
    permission_revision: int
    evidence: tuple[Evidence, ...]
    verifier_revision: str = ""
    disposition: QuestionDisposition = QuestionDisposition.ANSWERABLE
    conflict_detected: bool = False
    real_acceptance: bool = False
    retrieval_health: RetrievalHealth = RetrievalHealth.HEALTHY
    retrieval_warnings: tuple[str, ...] = ()
    disposition_reason: str = ""
    clarification_fields: tuple[str, ...] = ()
    question_assessor_revision: str = ""
    coverage: str = "unchecked"
    retrieval_queries: tuple[str, ...] = ()
    clarification_question: str | None = None
    coverage_report: dict[str, Any] = field(default_factory=dict)
    subject_authorization_revision: str = ""
    # Private run-store data; never projected into AskResult or conversation APIs.
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = [f"E{index}" for index in range(1, len(self.evidence) + 1)]
        if [item.evidence_id for item in self.evidence] != expected:
            raise ValueError("evidence IDs must be contiguous E1...En")

    @property
    def generation_evidence(self) -> tuple[Evidence, ...]:
        return tuple(item for item in self.evidence if item.source_role != "conflict_context")


@dataclass(frozen=True)
class AtomicClaim:
    text: str
    evidence_ids: tuple[str, ...]
    visual_fact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.evidence_ids:
            raise ValueError("claims require text and evidence IDs")


class DraftAnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class DraftAnswer:
    text: str
    citation_ids: tuple[str, ...]
    claims: tuple[AtomicClaim, ...] = ()
    status: DraftAnswerStatus = DraftAnswerStatus.ANSWERED
    synthesized: bool = False


@dataclass(frozen=True)
class ClaimVerdict:
    claim_text: str
    evidence_ids: tuple[str, ...]
    verdict: Literal["SUPPORTED", "CONTRADICTED", "INSUFFICIENT"]
    reason_code: str


@dataclass(frozen=True)
class VerificationResult:
    verdicts: tuple[ClaimVerdict, ...]
    revision: str
    citation_ids_valid: bool = True
    answer_claims_covered: bool = True
    evidence_support_verified: bool = True
    conflict_checked: bool = True
    policy_checked: bool = True
    conflicting_evidence_ids: tuple[str, ...] = ()
    condition_checks: tuple[dict[str, str], ...] = ()

    @property
    def supported(self) -> bool:
        return bool(
            self.verdicts
            and self.citation_ids_valid
            and self.answer_claims_covered
            and self.evidence_support_verified
            and self.conflict_checked
            and self.policy_checked
            and not self.conflicting_evidence_ids
            and all(c["status"] != "missing" for c in self.condition_checks)
            and all(item.verdict == "SUPPORTED" for item in self.verdicts)
        )


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    source_url: str
    locator: dict[str, Any]


@dataclass(frozen=True)
class AskResult:
    rag_run_id: str
    status: AnswerStatus
    answer: str | None
    citations: tuple[Citation, ...]
    evidence: tuple[Evidence, ...]
    warnings: tuple[str, ...]
    verified: bool
    real_acceptance: bool = False
    retrieval_health: RetrievalHealth = RetrievalHealth.HEALTHY
    degraded: bool = False
    retryable: bool = False
    clarification_fields: tuple[str, ...] = ()
    clarification_question: str | None = None
    coverage: str = "unchecked"
    coverage_report: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Feedback:
    rag_run_id: str
    user_id: str
    rating: int
    reason_code: str
    comment: str
    index_generation_id: str
    retrieval_revision: str
    prompt_revision: str
    model_revision: str

    def __post_init__(self) -> None:
        if self.rating < 1 or self.rating > 5:
            raise ValueError("feedback rating must be 1..5")
