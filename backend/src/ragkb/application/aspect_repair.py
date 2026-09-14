"""One optional, append-only completion followed by full independent verification."""

from collections.abc import Callable
from typing import Any

from ragkb.application.qa_budget import budget_stage
from ragkb.domain.errors import InvalidProviderResponse, QABudgetExceeded, TransientProviderError
from ragkb.domain.question_coverage import required_aspects
from ragkb.domain.rag import DraftAnswer, DraftAnswerStatus, Evidence, VerificationResult
from ragkb.domain.visual_claims import visual_claim_evidence


def complete_aspects(
    question: str,
    draft: DraftAnswer,
    verification: VerificationResult,
    evidence: tuple[Evidence, ...],
    repair: Callable[..., DraftAnswer],
    verify: Callable[[str, DraftAnswer, tuple[Evidence, ...]], VerificationResult],
) -> tuple[DraftAnswer, VerificationResult, str]:
    if not verification.supported:
        return draft, verification, ""
    aspects = {r["aspect_id"]: r["question"] for r in required_aspects(question)}
    available = {e.evidence_id: e for e in evidence}
    gaps: list[dict[str, Any]] = [
        {**r, "question": aspects[r["aspect_id"]]}
        for r in verification.aspect_checks
        if r.get("status") in {"answer_missing", "partial"}
        and r.get("aspect_id") in aspects
        and r.get("evidence_ids")
        and set(r["evidence_ids"]) <= available.keys()
    ]
    if not gaps:
        return draft, verification, ""
    ids = {identity for row in gaps for identity in row["evidence_ids"]}
    sources = tuple(e for e in evidence if e.evidence_id in ids)
    try:
        with budget_stage("generation"):
            addition = repair(question, draft, sources, gaps)
        if addition.status is not DraftAnswerStatus.ANSWERED or not addition.text.strip():
            return draft, verification, "ASPECT_COMPLETION_NO_SUPPORTED_ADDITION"
        cited = {i for claim in addition.claims for i in claim.evidence_ids}
        if (
            not addition.claims
            or not cited
            or not cited <= ids
            or set(addition.citation_ids) != cited
        ):
            raise InvalidProviderResponse("ASPECT_COMPLETION_CITATIONS_INVALID")
        for claim in addition.claims:
            visual_claim_evidence(claim, sources)
        merged = DraftAnswer(
            text=draft.text.rstrip() + "\n\n" + addition.text.strip(),
            citation_ids=tuple(dict.fromkeys((*draft.citation_ids, *addition.citation_ids))),
            claims=(*draft.claims, *addition.claims),
            synthesized=draft.synthesized or addition.synthesized,
        )
        checked = verify(question, merged, evidence)
        if checked.conflicting_evidence_ids:
            return merged, checked, "ASPECT_COMPLETION_CONFLICT"
        old_answered = {
            r["aspect_id"] for r in verification.aspect_checks if r["status"] == "answered"
        }
        new_answered = {r["aspect_id"] for r in checked.aspect_checks if r["status"] == "answered"}
        if checked.supported and old_answered < new_answered:
            return merged, checked, "ASPECT_COMPLETION_VERIFIED"
        return draft, verification, "ASPECT_COMPLETION_NOT_VERIFIED"
    except QABudgetExceeded:
        return draft, verification, "ASPECT_COMPLETION_BUDGET_EXHAUSTED"
    except (TransientProviderError, InvalidProviderResponse, ValueError):
        return draft, verification, "ASPECT_COMPLETION_UNAVAILABLE"
