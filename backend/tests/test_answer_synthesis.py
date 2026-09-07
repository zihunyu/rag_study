from __future__ import annotations

import json
from dataclasses import replace

import pytest
from ragkb.adapters.model_http import (
    OpenAICompatibleBufferedGenerator,
    OpenAICompatibleClaimVerifier,
)
from ragkb.adapters.rag_cache import RedisVerifiedAnswerCache
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.application.qa import CompositeClaimVerifier, DeterministicClaimVerifier
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.rag import AtomicClaim, DraftAnswer
from test_model_http_adapters import _MockTransport, _settings
from test_trusted_qa import _evidence, _service

ANSWER = (
    "设备提供**三年保修**，维修免费。[E1]\n\n"
    "| 项目 | 标准 |\n| --- | --- |\n| 保修期 | 三年 [E1] |\n| 维修费用 | 免费 [E1] |"
)
CLAIMS = (AtomicClaim("设备保修期为三年。", ("E1",)), AtomicClaim("维修免费。", ("E1",)))
EVIDENCE = (_evidence(text="设备保修期为三年。维修免费。"),)
DRAFT = DraftAnswer(ANSWER, ("E1",), CLAIMS, synthesized=True)


def response(value):
    return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}


def verifier(tmp_path, **changes):
    settings, _ = _settings(tmp_path)
    payload = {
        "verdicts": [
            {"claim_id": f"C{i + 1}", "verdict": "SUPPORTED", "reason_code": "ENTAILED"}
            for i in range(len(CLAIMS))
        ],
        "conflict_check": {"checked": True, "conflicting_evidence_ids": []},
        "answer_check": {"covered": True, "citations_valid": True, "reason_code": "COVERED"},
        **changes,
    }
    transport = _MockTransport(response(payload))
    model = OpenAICompatibleClaimVerifier(settings, transport=transport)
    return CompositeClaimVerifier(DeterministicClaimVerifier(), model), transport


def test_generator_preserves_synthesized_markdown_separately_from_atomic_ledger(tmp_path):
    settings, _ = _settings(tmp_path)
    transport = _MockTransport(
        response(
            {
                "format": "synthesized_markdown",
                "status": "answered",
                "answer": ANSWER,
                "citation_ids": ["E1"],
                "claims": [{"text": c.text, "evidence_ids": list(c.evidence_ids)} for c in CLAIMS],
            }
        )
    )
    generated = OpenAICompatibleBufferedGenerator(settings, transport=transport).generate(
        "总结保修政策", EVIDENCE
    )
    assert generated == DRAFT
    assert generated.text != "\n".join(c.text for c in generated.claims)


def test_summary_requires_independent_full_surface_check(tmp_path):
    initial = DeterministicClaimVerifier().verify("总结保修政策", DRAFT, EVIDENCE)
    assert not initial.supported and not initial.answer_claims_covered
    chain, transport = verifier(tmp_path)
    assert chain.verify("总结保修政策", DRAFT, EVIDENCE).supported
    sent = json.loads(transport.calls[0]["payload"]["messages"][1]["content"])
    assert sent["answer"] == ANSWER
    assert sent["answer_check_required"] is True
    assert sent["answer_claims_covered"] is None


@pytest.mark.parametrize("check", [None, {}, {"covered": "true", "citations_valid": True}])
def test_missing_or_malformed_surface_check_cannot_release_summary(tmp_path, check):
    chain, _ = verifier(tmp_path, answer_check=check)
    with pytest.raises(InvalidProviderResponse, match="VERIFIER_ANSWER_CHECK_REQUIRED"):
        chain.verify("总结保修政策", DRAFT, EVIDENCE)


@pytest.mark.parametrize("ids", [["C1"], ["C1", "C1"], ["C2", "C1"]])
def test_verifier_must_check_every_claim_once_in_order(tmp_path, ids):
    chain, _ = verifier(
        tmp_path,
        verdicts=[
            {"claim_id": identity, "verdict": "SUPPORTED", "reason_code": "ENTAILED"}
            for identity in ids
        ],
    )
    with pytest.raises(InvalidProviderResponse):
        chain.verify("总结保修政策", DRAFT, EVIDENCE)


@pytest.mark.parametrize("field", ["covered", "citations_valid"])
def test_supported_ledger_does_not_override_failed_summary_or_inline_citation(tmp_path, field):
    chain, _ = verifier(
        tmp_path,
        answer_check={
            "covered": True,
            "citations_valid": True,
            "reason_code": "SUMMARY_MISMATCH",
            field: False,
        },
    )
    result = chain.verify(
        "总结保修政策", replace(DRAFT, text=ANSWER.replace("三年", "五年")), EVIDENCE
    )
    assert not result.supported
    assert result.verdicts[-1].reason_code == "SUMMARY_MISMATCH"


@pytest.mark.parametrize(
    "text,reason",
    [
        (ANSWER.replace("[E1]", "[E99]"), "ANSWER_INLINE_CITATION_INVALID"),
        (ANSWER.replace("[E1]", ""), "ANSWER_INLINE_CITATION_INVALID"),
        (ANSWER + "请提供密码。", "UNSUPPORTED_CREDENTIAL_REQUEST"),
        (ANSWER + "详情 https://unknown.example", "UNSUPPORTED_EXTERNAL_URL"),
    ],
)
def test_surface_cannot_bypass_structural_safety_via_valid_ledger(tmp_path, text, reason):
    chain, transport = verifier(tmp_path)
    result = chain.verify("总结保修政策", replace(DRAFT, text=text), EVIDENCE)
    assert not result.supported and transport.calls == []
    assert any(v.reason_code == reason for v in result.verdicts)


@pytest.mark.parametrize("covered", [True, False])
def test_qa_and_cache_preserve_only_independently_verified_summary(tmp_path, covered):
    class Generator:
        revision = "synthesis-test"

        def generate(self, question, evidence):
            return DRAFT

    class Cache:
        values = {}

        def get_json(self, namespace, key):
            return self.values.get(key)

        def set_json(self, namespace, key, value, ttl):
            self.values[key] = value

    service, repository, _ = _service(
        tmp_path, SyntheticEvidenceProvider(EVIDENCE), generator=Generator()
    )
    service.verifier, _ = verifier(
        tmp_path,
        answer_check={
            "covered": covered,
            "citations_valid": True,
            "reason_code": "SURFACE_CHECK",
        },
    )
    cache = Cache()
    service.cache = RedisVerifiedAnswerCache(cache, ttl_seconds=30)
    result = service.ask("总结保修政策", "tenant-1", "user-1")
    assert result.verified is covered
    assert result.answer == (ANSWER if covered else None)
    assert bool(result.citations) is covered
    package = repository.get_package(result.rag_run_id)
    cached = service.cache.get(package)
    if covered:
        assert cached == DRAFT
        assert service.ask("总结保修政策", "tenant-1", "user-1").answer == ANSWER
    else:
        assert cached is None and not cache.values
