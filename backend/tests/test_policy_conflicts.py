from __future__ import annotations

import json

import pytest
from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.adapters.rag_stubs import DeterministicBufferedGenerator, SyntheticEvidenceProvider
from ragkb.application.qa import CompositeClaimVerifier, DeterministicClaimVerifier
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.policy_conflicts import conflicting_sources
from ragkb.domain.rag import AnswerStatus, AtomicClaim, DraftAnswer, VerificationResult
from test_model_http_adapters import _MockTransport, _settings
from test_trusted_qa import _evidence, _service


@pytest.mark.parametrize(
    ("first", "second", "conflict"),
    [
        ("退款期限为15天。", "退款期限为30天。", True),
        ("退款期限为十五天。", "退款期限为15天。", False),
        ("退款期限最多15天。", "退款期限超过15天。", True),
        ("退款期限为15天。", "退款期限不超过15天。", False),
        ("服务时间为09:00至12:00。", "服务时间为13:00至17:00。", True),
        ("员工可以申请退款。", "员工不可以申请退款。", True),
        ("员工需要提交凭证。", "员工无需提交凭证。", True),
        ("适用于正式员工。退款期限为15天。", "适用于临时员工。退款期限为30天。", False),
        ("A退款期限为15天。", "B退款期限为30天。", False),
    ],
)
def test_conflicts_respect_values_relations_scope_and_objects(first, second, conflict):
    evidence = (
        _evidence(text=first),
        _evidence(evidence_id="E2", document_id="other", text=second),
    )
    assert bool(conflicting_sources(evidence, at_epoch=100)) is conflict


@pytest.mark.parametrize(
    "overrides",
    [
        {"valid_to_epoch": 100},
        {"valid_from_epoch": 101},
        {"current_version": False},
        {"authorized": False},
    ],
)
def test_non_current_or_unauthorized_sources_do_not_participate(overrides):
    evidence = (
        _evidence(text="退款期限为15天。"),
        _evidence(evidence_id="E2", text="退款期限为30天。", **overrides),
    )
    assert conflicting_sources(evidence, at_epoch=100) == ()


def test_retrieval_order_and_authority_numbers_cannot_elect_a_winner(tmp_path):
    provider = SyntheticEvidenceProvider(
        (
            _evidence(text="退款期限为15天。", authority_rank=999),
            _evidence(evidence_id="E2", text="退款期限为30天。", authority_rank=1),
        )
    )
    service, _, _ = _service(
        tmp_path, provider, generator=DeterministicBufferedGenerator(answer="退款期限为15天。")
    )
    result = service.ask("退款期限？", "tenant-1", "user")
    assert result.status is AnswerStatus.CONFLICTING_EVIDENCE
    assert result.answer is None and not result.citations


def test_unrelated_policy_difference_does_not_block_the_claim(tmp_path):
    provider = SyntheticEvidenceProvider(
        (
            _evidence(text="退款期限为15天。保修期为3年。"),
            _evidence(evidence_id="E2", text="退款期限为15天。保修期为5年。"),
        )
    )
    service, _, _ = _service(
        tmp_path, provider, generator=DeterministicBufferedGenerator(answer="退款期限为15天。")
    )
    result = service.ask("退款期限？", "tenant-1", "user")
    assert result.status is AnswerStatus.ANSWERED


def _verifier(tmp_path, check):
    settings, _ = _settings(tmp_path)
    response = {"verdicts": [{"verdict": "SUPPORTED", "reason_code": "CITED_SOURCE_SUPPORTS"}]}
    if check is not None:
        response["conflict_check"] = check
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(response)}}]})
    return OpenAICompatibleClaimVerifier(settings, transport=transport), transport


def test_model_receives_all_conflict_sources_but_only_cited_support(tmp_path):
    verifier, transport = _verifier(
        tmp_path, {"checked": True, "conflicting_evidence_ids": ["E1", "E2"]}
    )
    evidence = (
        _evidence(text="退款期限为15天。"),
        _evidence(
            evidence_id="E2", text="退费申请应在三十天内提出。", source_role="conflict_context"
        ),
    )
    draft = DraftAnswer("退款期限为15天。", ("E1",), (AtomicClaim("退款期限为15天。", ("E1",)),))
    result = verifier.verify("退款期限？", draft, evidence)
    assert result.conflicting_evidence_ids == ("E1", "E2")
    assert not result.supported
    payload = json.loads(transport.calls[0]["payload"]["messages"][1]["content"])
    assert [e["evidence_id"] for e in payload["claims"][0]["evidence"]] == ["E1"]
    assert [e["evidence_id"] for e in payload["conflict_evidence"]] == ["E1", "E2"]
    assert payload["conflict_evidence"][1]["source_role"] == "conflict_context"


@pytest.mark.parametrize(
    "check",
    [
        None,
        {},
        {"checked": False, "conflicting_evidence_ids": []},
        {"checked": "true", "conflicting_evidence_ids": []},
        {"checked": True, "conflicting_evidence_ids": "E1,E2"},
        {"checked": True, "conflicting_evidence_ids": ["E1"]},
        {"checked": True, "conflicting_evidence_ids": ["E1", "E999"]},
        {"checked": True, "conflicting_evidence_ids": ["E1", "E1"]},
        {"checked": True, "conflicting_evidence_ids": [{"id": "E1"}]},
    ],
)
def test_missing_or_invalid_conflict_review_is_a_protocol_error(tmp_path, check):
    verifier, _ = _verifier(tmp_path, check)
    draft = DraftAnswer("退款期限15天。", ("E1",), (AtomicClaim("退款期限15天。", ("E1",)),))
    with pytest.raises(InvalidProviderResponse):
        verifier.verify("退款期限？", draft, (_evidence(text="退款期限15天。"),))


def test_semantic_conflict_blocks_answer_cache_and_obeys_final_permission(tmp_path, monkeypatch):
    evidence = (
        _evidence(text="退款期限为15天。"),
        _evidence(evidence_id="E2", text="退费申请应在三十天内提出。"),
    )
    service, _, _ = _service(
        tmp_path,
        SyntheticEvidenceProvider(evidence),
        generator=DeterministicBufferedGenerator(answer="退款期限为15天。"),
    )
    semantic, _ = _verifier(tmp_path, {"checked": True, "conflicting_evidence_ids": ["E1", "E2"]})
    service.verifier = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic)
    result = service.ask("退款期限？", "tenant-1", "user")
    assert result.status is AnswerStatus.CONFLICTING_EVIDENCE
    assert result.answer is None and result.citations == ()

    original = semantic.verify

    def revoked(*args):
        result = original(*args)
        service.permission.allowed = False
        return result

    monkeypatch.setattr(semantic, "verify", revoked)
    result = service.ask("退款期限？", "tenant-1", "user")
    assert result.status is AnswerStatus.SYSTEM_ERROR
    assert result.warnings == ("FINAL_PERMISSION_RECHECK_FAILED",)


def test_structural_conflict_survives_composite_short_circuit():
    class NeverVerify:
        revision = "never"

        def verify(self, *args) -> VerificationResult:
            pytest.fail("a known structural conflict cannot be overruled")

    draft = DraftAnswer("退款期限15天。", ("E1",), (AtomicClaim("退款期限15天。", ("E1",)),))
    evidence = (
        _evidence(text="退款期限15天。"),
        _evidence(evidence_id="E2", text="退款期限30天。"),
    )
    result = CompositeClaimVerifier(DeterministicClaimVerifier(), NeverVerify()).verify(
        "退款期限？", draft, evidence
    )
    assert result.conflicting_evidence_ids == ("E1", "E2")
