from dataclasses import replace

import pytest
from ragkb.application.qa import CompositeClaimVerifier, DeterministicClaimVerifier
from ragkb.domain.numeric_facts import check_numeric_facts
from test_numeric_claim_verification import RecordingSemanticVerifier, _draft
from test_trusted_qa import _evidence


@pytest.mark.parametrize("supported", [True, False])
def test_reference_identifier_requires_full_semantic_review(supported):
    question = "Atlas M7 可以像 Vega K4 一样远程办理吗？"
    claim = "Atlas M7 不能像 Vega K4 一样远程办理，仅支持现场办理。"
    source = (_evidence(text="Atlas M7 仅支持现场办理，不支持远程办理。"),)
    structural = DeterministicClaimVerifier()
    draft = replace(_draft(claim), text=claim + "[E1]", synthesized=True)
    semantic = RecordingSemanticVerifier(supported=supported)
    initial = structural.verify(question, draft, source)
    assert not initial.supported
    assert any(v.reason_code == "NUMERIC_FACT_REQUIRES_SEMANTIC_REVIEW" for v in initial.verdicts)
    result = CompositeClaimVerifier(structural, semantic).verify(question, draft, source)
    assert semantic.calls == 1
    assert result.supported is supported


def test_context_is_not_source_support_for_an_invented_identifier():
    semantic = RecordingSemanticVerifier()
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic).verify(
        "Atlas M7 支持什么？",
        _draft("Vega K4 支持现场办理。"),
        (_evidence(text="Atlas M7 仅支持现场办理。"),),
    )
    assert not result.supported
    assert semantic.calls == 0


@pytest.mark.parametrize(
    ("claim", "source"),
    [
        ("M7 的退款期限为2天，是否像K4一样？", "M7 的退款期限为12天。"),
        ("M7 的退款期限为15天，是否像K4一样？", "M7 的退款期限不超过15天。"),
        ("M7 费用为20元，是否像K4一样？", "M7 费用为15元。"),
        ("M7 电压为220V，是否像K4一样？", "M7 电压为110V。"),
    ],
)
def test_question_reference_cannot_hide_real_numeric_mismatch(claim, source):
    semantic = RecordingSemanticVerifier()
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic).verify(
        "M7 的2天、15天、20元、220V是否像K4一样？", _draft(claim), (_evidence(text=source),)
    )
    assert not result.supported
    assert semantic.calls == 0
    assert any(v.reason_code == "EXACT_FACT_NOT_IN_EVIDENCE" for v in result.verdicts)


def test_other_callers_keep_strict_identifier_matching():
    assert check_numeric_facts("M7不能像K4一样远程办理。", ("M7仅支持现场办理。",)) == "mismatch"
    assert check_numeric_facts(
        "M7不能像K4一样远程办理。",
        ("M7仅支持现场办理。",),
        question_identifiers=("m7", "k4"),
    ) == "uncertain"


def test_question_context_does_not_hide_cross_source_conflict():
    class ConflictingSemantic(RecordingSemanticVerifier):
        def verify(self, question, draft, evidence):
            return replace(
                super().verify(question, draft, evidence), conflicting_evidence_ids=("E1", "E2")
            )

    semantic = ConflictingSemantic()
    evidence = (
        _evidence(text="M7仅支持现场办理。"),
        replace(_evidence(text="M7支持远程办理。"), evidence_id="E2", document_id="doc-2"),
    )
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic).verify(
        "M7可以像K4一样远程办理吗？", _draft("M7不能像K4一样远程办理。"), evidence
    )
    assert semantic.calls == 1
    assert not result.supported
    assert result.conflicting_evidence_ids == ("E1", "E2")
