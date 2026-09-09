from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from ragkb.adapters.rag_stubs import DeterministicBufferedGenerator, SyntheticEvidenceProvider
from ragkb.application.qa import (
    CompositeClaimVerifier,
    DeterministicClaimVerifier,
    InMemoryVerifiedAnswerCache,
)
from ragkb.domain.rag import (
    AnswerStatus,
    AtomicClaim,
    ClaimVerdict,
    DraftAnswer,
    Evidence,
    VerificationResult,
)
from test_trusted_qa import _evidence, _service


class RecordingSemanticVerifier:
    revision = "recording-semantic:test"

    def __init__(self, *, supported: bool = True) -> None:
        self.calls = 0
        self.supported = supported

    def verify(
        self, question: str, draft: DraftAnswer, evidence: tuple[Evidence, ...]
    ) -> VerificationResult:
        self.calls += 1
        return VerificationResult(
            tuple(
                ClaimVerdict(
                    claim.text,
                    claim.evidence_ids,
                    "SUPPORTED" if self.supported else "INSUFFICIENT",
                    "SEMANTIC_SUPPORTED" if self.supported else "SEMANTIC_NOT_SUPPORTED",
                )
                for claim in draft.claims
            ),
            self.revision,
            evidence_support_verified=self.supported,
        )


def _draft(text: str) -> DraftAnswer:
    return DraftAnswer(text, ("E1",), (AtomicClaim(text, ("E1",)),))


def test_equivalent_chinese_number_reaches_independent_verifier() -> None:
    semantic = RecordingSemanticVerifier()
    verifier = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic)
    result = verifier.verify(
        "退款期限？", _draft("退款期限为十五天。"), (_evidence(text="退款期限为15天。"),)
    )
    assert result.supported
    assert semantic.calls == 1


@pytest.mark.parametrize("supported", [True, False])
def test_semantic_verifier_resolves_only_explicit_numeric_uncertainty(supported: bool) -> None:
    structural = DeterministicClaimVerifier()
    semantic = RecordingSemanticVerifier(supported=supported)
    verifier = CompositeClaimVerifier(structural, semantic)
    draft = _draft("退款处理需要十五天。")
    evidence = (_evidence(text="退款期限为15天。"),)
    initial = structural.verify("退款期限？", draft, evidence)
    assert not initial.supported
    assert initial.verdicts[0].reason_code == "NUMERIC_FACT_REQUIRES_SEMANTIC_REVIEW"

    result = verifier.verify("退款期限？", draft, evidence)
    assert semantic.calls == 1
    assert result.supported is supported
    assert result.evidence_support_verified is supported


@pytest.mark.parametrize(
    ("claim", "source", "reason"),
    [
        ("退款期限为2天。", "退款期限为12天。", "EXACT_FACT_NOT_IN_EVIDENCE"),
        ("退款期限为15天。", "退款期限不超过15天。", "EXACT_FACT_NOT_IN_EVIDENCE"),
        ("A退款期限为20天。", "A退款期限为15天，B退款期限为20天。", "EXACT_FACT_NOT_IN_EVIDENCE"),
        (
            "退款期限为2天，费用约为15元。",
            "退款期限为12天，费用为15元。",
            "EXACT_FACT_NOT_IN_EVIDENCE",
        ),
        ("退款约15天，请提供密码。", "退款期限为15天。", "UNSUPPORTED_CREDENTIAL_REQUEST"),
        (
            "退款约15天，详情见https://evil.example。",
            "退款期限为15天。",
            "UNSUPPORTED_EXTERNAL_URL",
        ),
    ],
)
def test_semantic_success_cannot_override_known_failure(
    claim: str, source: str, reason: str
) -> None:
    semantic = RecordingSemanticVerifier()
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic).verify(
        "退款期限？", _draft(claim), (_evidence(text=source),)
    )
    assert not result.supported
    assert result.verdicts[0].reason_code == reason
    assert semantic.calls == 0


@pytest.mark.parametrize("supported", [True, False])
@pytest.mark.parametrize(
    "claim",
    [
        "R9 功率减少 70 W，计算为 480 - 410 = 70 W。",
        "计算：R9 从常温变为低温时，额定功率减少 480 W - 410 W = 70 W。",
    ],
)
def test_grounded_explicit_calculation_requires_independent_semantic_check(supported, claim):
    semantic = RecordingSemanticVerifier(supported=supported)
    evidence = (_evidence(text="型号 | 常温功率 | 低温功率\nR9 | 480 | 410"),)
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic).verify(
        "R9 功率减少多少？", _draft(claim), evidence
    )
    assert semantic.calls == 1
    assert result.supported is supported


@pytest.mark.parametrize(
    "claim",
    [
        "R9 功率减少 70 W。",  # No inspectable derivation.
        "R9 功率减少 80 W，计算为 480 - 410 = 80 W。",  # Wrong arithmetic.
        "R9 功率减少 70 W，计算为 490 - 420 = 70 W。",  # Missing operands.
        "R9 功率减少 70 W，计算为 480 - 410 = 70 W；电压 220 V。",  # Extra invention.
        "R9 功率减少 80 W，计算为 480 W - 410 W = 80 W。",
        "R9 功率减少 70 W，计算为 480 W - 410 V = 70 W。",  # Mixed dimensions.
        "R9 功率减少 70 W，计算为 480 kW - 410 W = 70 W。",  # Unconverted scales.
    ],
)
def test_invalid_or_unbound_calculations_still_fail_before_semantic_review(claim):
    semantic = RecordingSemanticVerifier()
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic).verify(
        "R9 功率减少多少？",
        _draft(claim),
        (_evidence(text="型号 | 常温功率 | 低温功率\nR9 | 480 | 410"),),
    )
    assert not result.supported
    assert semantic.calls == 0


@pytest.mark.parametrize(
    "flag", ["citation_ids_valid", "answer_claims_covered", "conflict_checked", "policy_checked"]
)
def test_numeric_deferral_preserves_all_other_structural_gates(flag: str) -> None:
    class FailedGateVerifier(DeterministicClaimVerifier):
        def verify(
            self, question: str, draft: DraftAnswer, evidence: tuple[Evidence, ...]
        ) -> VerificationResult:
            result = super().verify(question, draft, evidence)
            return replace(result, **{flag: False})

    semantic = RecordingSemanticVerifier()
    result = CompositeClaimVerifier(FailedGateVerifier(), semantic).verify(
        "退款期限？", _draft("退款约15天。"), (_evidence(text="退款期限为15天。"),)
    )
    assert not result.supported
    assert semantic.calls == 0


def test_mixed_claims_cannot_hide_a_conflict_behind_numeric_uncertainty() -> None:
    claims = (AtomicClaim("退款期限为2天。", ("E1",)), AtomicClaim("费用约15元。", ("E1",)))
    draft = DraftAnswer("退款期限为2天。费用约15元。", ("E1",), claims)
    semantic = RecordingSemanticVerifier()
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic).verify(
        "退款政策？", draft, (_evidence(text="退款期限为12天。费用为15元。"),)
    )
    assert not result.supported
    assert semantic.calls == 0


@pytest.mark.parametrize(
    ("answer", "source", "answered"),
    [
        ("退款期限为十五天。", "退款期限为15天。", True),
        ("退款期限为二十天。", "退款期限为20天。", True),
        ("退款期限为2天。", "退款期限为12天。", False),
    ],
)
def test_qa_numeric_validation_controls_answer_and_citations(
    tmp_path: Path, answer: str, source: str, answered: bool
) -> None:
    service, _, _ = _service(
        tmp_path,
        SyntheticEvidenceProvider((_evidence(text=source),)),
        generator=DeterministicBufferedGenerator(answer=answer),
    )
    result = service.ask("退款期限？", "tenant-1", "user-1")
    assert result.status is (
        AnswerStatus.ANSWERED if answered else AnswerStatus.INSUFFICIENT_EVIDENCE
    )
    assert result.verified is answered
    assert bool(result.citations) is answered
    if answered:
        assert result.answer == answer
    else:
        assert result.answer is None
        assert "EXACT_FACT_NOT_IN_EVIDENCE" in result.warnings


def test_numeric_rejection_never_writes_verified_cache(tmp_path: Path, monkeypatch) -> None:
    service, _, _ = _service(
        tmp_path,
        SyntheticEvidenceProvider((_evidence(text="退款期限为12天。"),)),
        generator=DeterministicBufferedGenerator(answer="退款期限为2天。"),
    )
    service.cache = InMemoryVerifiedAnswerCache()

    def unexpected_write(*args) -> None:
        pytest.fail("a numerically unsupported draft reached the verified cache")

    monkeypatch.setattr(service.cache, "put", unexpected_write)
    for _ in range(2):
        result = service.ask("退款期限？", "tenant-1", "user-1")
        assert not result.verified and result.answer is None and not result.citations
