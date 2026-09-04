from __future__ import annotations

from ragkb.domain.claim_coverage import (
    extract_answer_clauses,
    render_verified_claims,
    verify_answer_claim_coverage,
)
from ragkb.domain.rag import AtomicClaim


def test_answer_clauses_require_complete_number_negation_and_exception_coverage() -> None:
    claims = (
        AtomicClaim("保修期为三年。", ("E1",)),
        AtomicClaim("无需登记。", ("E1",)),
        AtomicClaim("海外地区除外。", ("E2",)),
    )

    coverage = verify_answer_claim_coverage(
        "根据已验证证据，保修期为三年；同时无需登记；海外地区除外。[E1, E2]",
        claims,
    )

    assert coverage.complete is True
    assert coverage.uncovered_clauses == ()
    assert extract_answer_clauses("根据已验证证据，保修期为三年。") == ("保修期为三年",)


def test_extra_hallucinated_clause_is_not_hidden_by_a_valid_claim() -> None:
    claims = (AtomicClaim("产品提供标准保修服务。", ("E1",)),)

    coverage = verify_answer_claim_coverage(
        "产品提供标准保修服务，但保修期为五年。",
        claims,
    )

    assert coverage.complete is False
    assert coverage.uncovered_clauses == ("但保修期为五年",)


def test_verified_answer_renderer_deduplicates_and_uses_claim_text_only() -> None:
    claims = (
        AtomicClaim("保修期为三年。", ("E1",)),
        AtomicClaim("保修期为三年。", ("E1",)),
        AtomicClaim("维修免费。", ("E2",)),
    )

    assert render_verified_claims(claims) == "保修期为三年。\n维修免费。"
