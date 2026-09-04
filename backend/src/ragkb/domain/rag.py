"""Trusted RAG evidence, answer state and citation contracts for G3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


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

    def __post_init__(self) -> None:
        expected = [f"E{index}" for index in range(1, len(self.evidence) + 1)]
        if [item.evidence_id for item in self.evidence] != expected:
            raise ValueError("evidence IDs must be contiguous E1...En")


@dataclass(frozen=True)
class AtomicClaim:
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.evidence_ids:
            raise ValueError("claims require text and evidence IDs")


@dataclass(frozen=True)
class DraftAnswer:
    text: str
    citation_ids: tuple[str, ...]
    claims: tuple[AtomicClaim, ...] = ()


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

    @property
    def supported(self) -> bool:
        return bool(
            self.verdicts
            and self.citation_ids_valid
            and self.answer_claims_covered
            and self.evidence_support_verified
            and self.conflict_checked
            and self.policy_checked
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
