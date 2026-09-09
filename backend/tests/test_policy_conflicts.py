from __future__ import annotations

import json

import pytest
from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.adapters.rag_stubs import DeterministicBufferedGenerator, SyntheticEvidenceProvider
from ragkb.application.qa import CompositeClaimVerifier, DeterministicClaimVerifier
from ragkb.domain.errors import InvalidProviderResponse, ProviderTimeout
from ragkb.domain.policy_conflicts import conflict_witness_error, conflicting_sources
from ragkb.domain.rag import (
    AnswerStatus,
    AtomicClaim,
    ClaimVerdict,
    DraftAnswer,
    VerificationResult,
)
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
        if check.get("conflicting_evidence_ids") == ["E1", "E2"]:
            check["pairs"] = [_pair("退款期限为15天。", "退费申请应在三十天内提出。")]
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(response)}}]})
    return OpenAICompatibleClaimVerifier(settings, transport=transport), transport


def _pair(left, right):
    return {
        "left_id": "E1",
        "left_quote": left,
        "right_id": "E2",
        "right_quote": right,
        "reason": "Incompatible deadlines for the same refund scope.",
    }


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        (None, "conflict_pairs_required"),
        ([], "conflict_pairs_required"),
        (
            [_pair("退款期限为15天。", "退款期限为15天。")],
            "identical_assertions_are_not_a_conflict_witness",
        ),
        ([_pair("退款期限为15天。", "退款期限为999天。")], "conflict_quote_not_in_source"),
        ([_pair("退款期限为15天。", "退款期限为30天。")], ""),
    ],
)
def test_positive_conflicts_require_different_source_bound_assertions(pairs, expected):
    # A parent can repeat a child or contain an actual internal inconsistency.
    # Preserve both possibilities; do not simply discard all same-document pairs.
    text = "退款期限为15天。退款期限为30天。"
    evidence = (_evidence(text=text), _evidence(evidence_id="E2", text=text))
    assert conflict_witness_error(pairs, ["E1", "E2"], evidence) == expected


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


@pytest.mark.parametrize("repair_valid", [True, False])
def test_missing_conflict_review_rechecks_all_claims_once(tmp_path, repair_valid):
    settings, _ = _settings(tmp_path)
    calls = []

    class Transport:
        real_network = False

        def post_json(self, url, *, headers, payload, timeout):
            sent = json.loads(payload["messages"][1]["content"])
            calls.append(sent)
            response = {
                "verdicts": [{"verdict": "SUPPORTED", "reason_code": "ATTRIBUTED_POLICY_SUPPORTED"}]
            }
            if len(calls) == 2:
                assert sent["protocol_repair"]["field"] == "conflict_check"
                assert sent["claims"] == calls[0]["claims"]
                assert sent["conflict_evidence"] == calls[0]["conflict_evidence"]
                if repair_valid:
                    response["conflict_check"] = {
                        "checked": True,
                        "conflicting_evidence_ids": ["E1", "E2"],
                        "pairs": [_pair("退款期限为15天。", "退款期限为30天。")],
                    }
            return {"choices": [{"message": {"content": json.dumps(response)}}]}

    verifier = OpenAICompatibleClaimVerifier(settings, transport=Transport())
    evidence = (
        _evidence(text="退款期限为15天。"),
        _evidence(evidence_id="E2", text="退款期限为30天。"),
    )
    draft = DraftAnswer("退款期限为15天。", ("E1",), (AtomicClaim("退款期限为15天。", ("E1",)),))
    if repair_valid:
        result = verifier.verify("退款期限？", draft, evidence)
        assert result.conflicting_evidence_ids == ("E1", "E2")
        assert not result.supported
    else:
        with pytest.raises(InvalidProviderResponse, match="VERIFIER_CONFLICT_CHECK_REQUIRED"):
            verifier.verify("退款期限？", draft, evidence)
    assert len(calls) == 2


def test_semantic_conflict_blocks_answer_cache_and_obeys_final_permission(tmp_path, monkeypatch):
    evidence = (
        _evidence(text="退款期限为15天。"),
        _evidence(evidence_id="E2", text="退费申请应在三十天内提出。"),
    )
    service, repository, _ = _service(
        tmp_path,
        SyntheticEvidenceProvider(evidence),
        generator=DeterministicBufferedGenerator(answer="退款期限为15天。"),
    )
    semantic, _ = _verifier(tmp_path, {"checked": True, "conflicting_evidence_ids": ["E1", "E2"]})
    service.verifier = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic)
    result = service.ask("退款期限？", "tenant-1", "user")
    assert result.status is AnswerStatus.CONFLICTING_EVIDENCE
    assert result.answer is None and result.citations == ()
    saved = repository.get_package(result.rag_run_id)
    failure = saved.diagnostics["failure"]
    assert failure["stage"] == "verification"
    assert failure["verification"]["conflicting_evidence_ids"] == ["E1", "E2"]
    assert saved.diagnostics["calls"][-1]["response"]

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


@pytest.mark.parametrize("outcome", ["conflict", "no_conflict", "unchecked", "timeout", "unsafe"])
def test_numeric_rejection_preserves_full_pool_conflict_check_without_approving_facts(outcome):
    evidence = (
        _evidence(document_id="one", text="退款期限为15天。", locator={"section_path": "政策甲"}),
        _evidence(evidence_id="E2", document_id="two", text="退款期限为30天。",
                  locator={"section_path": "政策乙"}, source_role="conflict_context"),
    )
    text = "退款期限为2天。" if outcome != "unsafe" else "退款期限为2天，请提供密码。"
    draft = DraftAnswer(text, ("E1",), (AtomicClaim(text, ("E1",)),))
    original = DeterministicClaimVerifier().verify("退款期限？", draft, evidence)
    assert not original.supported and not original.conflicting_evidence_ids

    class Semantic:
        revision = "fixture"
        calls = 0

        def verify(self, question, current, sources):
            self.calls += 1
            assert sources == evidence  # Includes the uncited, separate document.
            if outcome == "timeout":
                raise ProviderTimeout("MODEL_PROVIDER_TIMEOUT")
            return VerificationResult(
                (ClaimVerdict(text, ("E1",), "SUPPORTED", "semantic_pass"),), "fixture",
                conflict_checked=outcome != "unchecked",
                conflicting_evidence_ids=() if outcome == "no_conflict" else ("E1", "E2"),
            )

    semantic = Semantic()
    verifier = CompositeClaimVerifier(DeterministicClaimVerifier(), semantic)
    if outcome == "timeout":
        with pytest.raises(ProviderTimeout):
            verifier.verify("退款期限？", draft, evidence)
        return
    result = verifier.verify("退款期限？", draft, evidence)
    assert semantic.calls == (0 if outcome == "unsafe" else 1)
    assert result.verdicts == original.verdicts and not result.supported
    assert result.conflicting_evidence_ids == (("E1", "E2") if outcome == "conflict" else ())
