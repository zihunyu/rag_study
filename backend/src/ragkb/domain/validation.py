"""G4 local quality and human-review contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ragkb.domain.documents import CanonicalDocument


class QualityDisposition(StrEnum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    DEGRADED = "DEGRADED"
    BLOCKED_REAL_VALIDATION = "BLOCKED_REAL_VALIDATION"


class ReviewDecision(StrEnum):
    APPROVED = "APPROVED"
    NEEDS_REWORK = "NEEDS_REWORK"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class DocumentQualityReport:
    document_version_id: str
    source_format: str
    parser_revision: str
    node_count: int
    locator_coverage: float
    issue_codes: tuple[str, ...]
    disposition: QualityDisposition
    real_acceptance: bool = False

    @classmethod
    def from_document(cls, document: CanonicalDocument) -> DocumentQualityReport:
        node_count = len(document.nodes)
        located = sum(bool(node.locator.to_dict()) for node in document.nodes)
        issues = tuple(document.quality_issues) + (("EMPTY_DOCUMENT",) if node_count == 0 else ())
        uses_stub = any("stub" in issue.casefold() for issue in issues)
        disposition = (
            QualityDisposition.BLOCKED_REAL_VALIDATION
            if uses_stub
            else QualityDisposition.DEGRADED
            if issues
            else QualityDisposition.READY_FOR_REVIEW
        )
        return cls(
            document_version_id=document.document_version_id,
            source_format=document.source_format,
            parser_revision=document.parser_revision,
            node_count=node_count,
            locator_coverage=located / node_count if node_count else 0.0,
            issue_codes=issues,
            disposition=disposition,
            real_acceptance=False,
        )


@dataclass(frozen=True)
class DocumentReview:
    document_version_id: str
    reviewer_id: str
    decision: ReviewDecision
    comment: str
    quality_revision: str
    real_acceptance: bool = False
