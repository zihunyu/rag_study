from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from ragkb.adapters.model_http import OpenAICompatibleClaimVerifier
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.api.app import create_app
from ragkb.application.qa_diagnostics import (
    MAX_CALLS,
    diagnostic_scope,
    failure_diagnostics,
    record_failure,
    record_model_call,
)
from ragkb.domain.answer_conditions import (
    ConditionCheckError,
    condition_requirements,
    validate_condition_checks,
)
from ragkb.domain.errors import InvalidProviderResponse
from ragkb.domain.rag import AtomicClaim, DraftAnswer, Evidence
from ragkb.runtime_components import build_runtime_components
from test_model_http_adapters import _MockTransport as BaseTransport
from test_model_http_adapters import _settings
from test_trusted_qa import _service


def condition_response(response, sent):
    if "conflict_evidence" in sent:
        return response
    response = copy.deepcopy(response)
    loaded = json.loads(response["choices"][0]["message"]["content"])
    ids = {r["id"] for r in sent["condition_requirements"]}
    loaded = {"condition_checks": [c for c in loaded["condition_checks"] if c["id"] in ids]}
    response["choices"][0]["message"]["content"] = json.dumps(loaded)
    return response


class _MockTransport(BaseTransport):
    def post_json(self, url, **kwargs):
        response = super().post_json(url, **kwargs)
        sent = json.loads(kwargs["payload"]["messages"][1]["content"])
        return condition_response(response, sent)


def case():
    value = json.loads(
        (Path(__file__).parent / "fixtures/cross-document-verifier-failure.json").read_text(
            encoding="utf-8"
        )
    )
    saved = value["draft"]
    draft = DraftAnswer(
        saved["text"],
        tuple(saved["citation_ids"]),
        tuple(AtomicClaim(c["text"], tuple(c["evidence_ids"])) for c in saved["claims"]),
        synthesized=True,
    )
    return value, draft, tuple(Evidence(**e) for e in value["evidence"])


def corrected_response(value):
    response = copy.deepcopy(value["verifier_response"])
    loaded = json.loads(response["choices"][0]["message"]["content"])
    loaded["condition_checks"][2].update(
        status="not_applicable",
        answer_quote="",
        reason="Question concerns specific facts, not the document's intended use.",
    )
    response["choices"][0]["message"]["content"] = json.dumps(loaded, ensure_ascii=False)
    return response


def test_captured_failure_identifies_exact_condition_and_reason(tmp_path):
    value, draft, evidence = case()
    settings, _ = _settings(tmp_path)
    model = OpenAICompatibleClaimVerifier(
        settings, transport=_MockTransport(value["verifier_response"])
    )
    with pytest.raises(
        InvalidProviderResponse, match="VERIFIER_CONDITION_WITNESS_INVALID"
    ) as caught:
        model.verify(value["question"], draft, evidence)
    assert caught.value.diagnostic == {
        "code": "VERIFIER_CONDITION_WITNESS_INVALID",
        "reason": "quote_does_not_address_condition",
        "condition_id": "K3",
        "evidence_id": "E7",
        "verification_stage": "claims_and_conflicts",
        "condition_only_repair_attempted": True,
    }
    assert "虚构" not in str(caught.value)


def test_verifier_rate_limit_is_explicit_without_releasing_the_draft(tmp_path):
    from ragkb.api.citation_projection import reader_report
    from ragkb.domain.errors import ProviderRateLimited

    _, _, evidence = case()
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(evidence))

    class Limited:
        revision = "limited-fixture"

        def verify(self, *args):
            raise ProviderRateLimited("MODEL_PROVIDER_RATE_LIMITED")

    service.verifier = Limited()
    result = service.ask("保修期多久？", "tenant", "user")
    assert result.retryable and result.answer is None and not result.citations
    assert "MODEL_PROVIDER_RATE_LIMITED" in result.warnings
    report = reader_report(result.coverage_report)
    assert report["verification_failure"] == {
        "http_status": 429,
        "provider_code": "MODEL_PROVIDER_RATE_LIMITED",
        "stage": "claims_and_conflicts",
    }


def test_correct_applicability_does_not_force_unasked_repair_or_document_notes(tmp_path):
    value, draft, evidence = case()
    settings, _ = _settings(tmp_path)
    model = OpenAICompatibleClaimVerifier(
        settings, transport=_MockTransport(corrected_response(value))
    )
    result = model.verify(value["question"], draft, evidence)
    assert result.supported
    assert [c["status"] for c in result.condition_checks] == [
        "covered",
        "not_applicable",
        "not_applicable",
    ]


@pytest.mark.parametrize(
    "question", ["上门维修需要提前预约吗，服务时间是什么？", "总结上门维修政策"]
)
def test_relevant_appointment_condition_still_cannot_be_excluded(question):
    _, draft, evidence = case()
    required = condition_requirements(evidence)
    checks = [
        {"id": r["id"], "status": "not_applicable", "answer_quote": "", "reason": "未在答案中提及"}
        for r in required
    ]
    result = validate_condition_checks(checks, required, draft, question)
    assert result[1]["status"] == "missing"


def test_retrieval_titles_are_not_source_conditions():
    _, _, evidence = case()
    body = "进水损坏不在免费保修范围内。"
    title = "保修条款 / 免费保修除外情形"
    source = replace(
        evidence[0],
        text=f"SECTION_PATH: {title}\nDOCUMENT_TITLE: 保修条款\n{title}\n{body}",
        display_text=body,
        locator={"section_path": title},
    )
    required = condition_requirements((source,))
    assert [r["source_quote"] for r in required] == [body]
    assert required[0]["rule_context"] == body


@pytest.mark.parametrize(
    "question",
    [
        "澄星 R9 保修多久，从什么时候开始？",
        "澄星 R9 的保修期、起算时间、常温功率和电池容量分别是多少？",
        "请汇总澄星 R9 的保修期及起算时间、常温与低温功率，以及服务费退款期限。",
    ],
)
def test_semantic_rule_scope_is_not_overridden_by_shared_warranty_words(question):
    _, _, evidence = case()
    source = replace(
        evidence[0],
        text="申请澄星 R9 保修，需要提供购买凭证和设备序列号。进水损坏不在免费保修范围内。",
        display_text="",
        locator={"section_path": "澄星 R9 保修条款"},
    )
    required = condition_requirements((source,))
    draft = DraftAnswer("澄星 R9 保修三年，自购买凭证记载的购买日期起算。[E1]", ("E1",))
    checks = [
        {
            "id": r["id"],
            "status": "not_applicable",
            "answer_quote": "",
            "reason": "问题询问期限及起算日期；此规则约束维修申请材料或免费维修资格。",
        }
        for r in required
    ]
    assert all(
        c["status"] == "not_applicable"
        for c in validate_condition_checks(checks, required, draft, question)
    )
    # A semantic verdict of missing must still block, even in a short fact lookup.
    checks[0].update(status="missing", reason="The answer makes a repair eligibility claim.")
    assert validate_condition_checks(checks, required, draft, question)[0]["status"] == "missing"


def test_cross_document_rule_references_do_not_require_literal_reference_wording():
    _, _, evidence = case()
    source = replace(
        evidence[0],
        text=(
            "保修期和免费保修除外情形按保修条款执行。"
            "预约需要提交客户编号；申请保修所需的其他材料按保修条款执行。"
        ),
        display_text="",
    )
    required = condition_requirements((source,))
    answer = "进水损坏不免费维修。申请时准备客户编号、购买凭证和设备序列号。[E1]"
    draft = DraftAnswer(answer, ("E1",))
    checks = [
        {
            "id": r["id"],
            "status": "covered",
            "answer_quote": answer,
            "reason": "已核对被引用条款中的实际限制和材料，答案完整保留。",
        }
        for r in required
    ]
    assert all(c["status"] == "covered" for c in validate_condition_checks(checks, required, draft))
    # A concrete exclusion beside a reference remains a constraint.
    source = replace(source, text="进水损坏不在免费维修范围内；其他情形按保修条款执行。")
    required = condition_requirements((source,))
    wrong = "进水损坏可以免费维修。[E1]"
    checks = [
        {
            "id": required[0]["id"],
            "status": "covered",
            "answer_quote": wrong,
            "reason": "claimed covered",
        }
    ]
    assert (
        validate_condition_checks(checks, required, DraftAnswer(wrong, ("E1",)))[0]["status"]
        == "missing"
    )


def test_condition_witness_can_select_only_a_real_answer_paragraph():
    _, _, evidence = case()
    source = replace(evidence[0], text="出库后不能直接取消，需要按退货流程处理。", display_text="")
    required = condition_requirements((source,))
    paragraph = "但设备已出库后，订单不能直接取消，需要按退货流程处理。[E1]"
    draft = DraftAnswer("取消合同不等于取消订单。\n\n" + paragraph, ("E1",))
    checks = [
        {
            "id": "K1",
            "status": "covered",
            "answer_span_id": "A2",
            "answer_quote": "",
            "reason": "所选段落说明出库后的取消限制和退货流程。",
        }
    ]
    result = validate_condition_checks(checks, required, draft)
    assert result[0]["answer_quote"] == paragraph and result[0]["status"] == "covered"
    checks[0]["answer_span_id"] = "A99"
    with pytest.raises(ConditionCheckError) as caught:
        validate_condition_checks(checks, required, draft)
    assert caught.value.diagnostic["reason"] == "answer_span_id_invalid"


def test_irrelevant_condition_cannot_be_labeled_missing():
    _, draft, evidence = case()
    required = condition_requirements(evidence)
    assert required
    checks = [
        {
            "id": r["id"],
            "applicable": False,
            "status": "missing",
            "answer_quote": "",
            "reason": "The question concerns a different rule.",
        }
        for r in required
    ]
    with pytest.raises(ConditionCheckError) as caught:
        validate_condition_checks(checks, required, draft)
    assert caught.value.diagnostic["reason"] == "applicability_status_mismatch"


def test_uncited_title_is_not_a_reading_gap_and_real_gaps_remain_partial(tmp_path):
    from ragkb.domain.rag import AnswerStatus, Citation

    _, _, evidence = case()
    source = replace(evidence[0], locator={"section_path": "退款条款"})
    provider = SyntheticEvidenceProvider((source,))
    service, _, _ = _service(tmp_path, provider)
    package = provider.build_package("退款期限？", "tenant", "user")
    sections = [
        {"version_id": source.document_version_id, "section": "root"},
        {"version_id": source.document_version_id, "section": "退款条款"},
    ]
    package = replace(
        package,
        coverage="complete",
        coverage_report={
            "mode": "overview",
            "scope_complete": True,
            "complete": True,
            "sections": sections,
            "gaps": [],
        },
    )
    result = service._save(
        package,
        AnswerStatus.ANSWERED,
        answer="退款十五天。",
        citations=(Citation("E1", "/source", {}),),
        verified=True,
    )
    assert result.coverage_report["complete"] and result.coverage == "complete"
    assert result.coverage_report["uncited_sections"] == [sections[0]]
    partial = replace(
        package,
        rag_run_id="partial-test",
        coverage_report={
            **package.coverage_report,
            "complete": False,
            "gaps": ["达到读取片段上限"],
        },
    )
    result = service._save(partial, AnswerStatus.ANSWERED, answer="退款十五天。", verified=True)
    assert result.coverage == "partial" and not result.coverage_report["complete"]


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"answer_quote": None}, "quote_not_string"),
        ({"answer_quote": ""}, "covered_quote_empty"),
        ({"answer_quote": "编造的退款承诺"}, "quote_not_in_answer"),
        ({"status": "not_applicable"}, "noncovered_quote_not_empty"),
    ],
)
def test_witness_errors_keep_distinct_causes(mutation, reason):
    value, draft, evidence = case()
    required = condition_requirements(evidence)
    checks = json.loads(value["verifier_response"]["choices"][0]["message"]["content"])[
        "condition_checks"
    ]
    checks[0].update(mutation)
    with pytest.raises(ConditionCheckError) as caught:
        validate_condition_checks(checks, required, draft, value["question"])
    assert caught.value.diagnostic["condition_id"] == "K1"
    assert caught.value.diagnostic["reason"] == reason


def test_failed_model_output_is_persisted_privately_and_can_be_replayed(tmp_path):
    value, draft, evidence = case()
    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(update={"verifier_api_key": SecretStr("private-verifier-key")})
    transport = _MockTransport(value["verifier_response"])
    # Unexpected HTTP-envelope fields must not enter diagnostics.
    transport.response["headers"] = {"Authorization": "private-auth-header"}

    class Generator:
        revision = "captured-draft"

        def generate(self, question, evidence):
            return draft

    service, repository, _ = _service(
        tmp_path, SyntheticEvidenceProvider(evidence), generator=Generator()
    )
    service.verifier = OpenAICompatibleClaimVerifier(settings, transport=transport)
    runtime = build_runtime_components(
        storage_root=tmp_path / "api", database_path=tmp_path / "api.sqlite"
    )
    client = TestClient(create_app(replace(runtime, qa_service=service)))
    response = client.post("/api/ask", json={"question": value["question"]})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "system_error" and not result["verified"]
    assert result["answer"] is None and result["citations"] == []
    assert "diagnostics" not in result and "仅用于" not in response.text
    saved = repository.get_package(result["rag_run_id"])
    assert saved.diagnostics["failure"]["detail"]["condition_id"] == "K3"
    assert saved.diagnostics["failure"]["draft"]["text"] == draft.text
    assert len(saved.diagnostics["calls"]) == 2
    assert "private-auth-header" not in json.dumps(saved.diagnostics)
    assert settings.verifier_api_key.get_secret_value() not in json.dumps(saved.diagnostics)
    # Operator replay uses only the saved input/output and does not call a provider.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "inspect_rag_run", Path(__file__).parents[2] / "scripts/inspect_rag_run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    summary = module.summarize_run(saved, repository.get_result(result["rag_run_id"]))
    assert summary["condition_id"] == "K3" and "calls" not in summary
    assert "仅用于" not in json.dumps(summary, ensure_ascii=False)
    assert module.replay_conditions(saved)["reason"] == "quote_does_not_address_condition"
    assert len(transport.calls) == 2
    # A later successful run does not inherit the failed request's private data.
    service.verifier = OpenAICompatibleClaimVerifier(
        settings, transport=_MockTransport(corrected_response(value))
    )
    result = service.ask(value["question"], "tenant", "user")
    assert result.verified
    assert set(repository.get_package(result.rag_run_id).diagnostics) == {"performance"}
    assert "diagnostics" not in asdict(result)


@pytest.mark.parametrize("valid_repair", [True, False])
def test_protocol_repair_is_bounded_and_only_rechecks_invalid_conditions(tmp_path, valid_repair):
    value, draft, evidence = case()
    settings, _ = _settings(tmp_path)

    class Transport(_MockTransport):
        def post_json(self, url, **kwargs):
            result = super().post_json(url, **kwargs)
            if len(self.calls) == 2:
                sent = json.loads(kwargs["payload"]["messages"][1]["content"])
                assert sent["protocol_repair"]["condition_id"] == "K3"
                assert sent["answer"] == draft.text
                assert "conflict_evidence" not in sent
                assert [r["id"] for r in sent["condition_requirements"]] == ["K3"]
                fixed = corrected_response(value)
                if not valid_repair:
                    loaded = json.loads(fixed["choices"][0]["message"]["content"])
                    loaded["condition_checks"][2].update(
                        status="missing", reason="Required scope absent."
                    )
                    fixed["choices"][0]["message"]["content"] = json.dumps(loaded)
                return condition_response(fixed, sent)
            return result

    transport = Transport(value["verifier_response"])
    result = OpenAICompatibleClaimVerifier(settings, transport=transport).verify(
        value["question"], draft, evidence
    )
    assert result.supported is valid_repair
    assert len(transport.calls) == 2


def test_diagnostics_are_request_isolated_and_bounded():
    def run(identity):
        with diagnostic_scope():
            for _ in range(MAX_CALLS + 2):
                record_model_call(
                    "fixture",
                    {"model": "fixture", "messages": [{"content": identity}]},
                    {"choices": []},
                    0.1,
                )
            record_failure("verification", "fixture")
            return failure_diagnostics()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["request-a", "request-b"]))
    for identity, result in zip(["request-a", "request-b"], results, strict=True):
        assert result["truncated"] and len(result["calls"]) == MAX_CALLS
        assert all(c["messages"][0]["content"] == identity for c in result["calls"])
    assert failure_diagnostics() == {}


def test_single_attempt_acceptance_can_disable_protocol_repair(tmp_path):
    value, draft, evidence = case()
    settings, _ = _settings(tmp_path)
    transport = _MockTransport(value["verifier_response"])
    verifier = OpenAICompatibleClaimVerifier(
        settings, transport=transport, condition_protocol_repair=False
    )
    with pytest.raises(InvalidProviderResponse):
        verifier.verify(value["question"], draft, evidence)
    assert len(transport.calls) == 1


def test_protocol_repair_shares_the_original_timeout(tmp_path, monkeypatch):
    import ragkb.application.deadlines as deadlines

    value, draft, evidence = case()
    settings, _ = _settings(tmp_path)
    settings = settings.model_copy(update={"verifier_timeout_seconds": 30})
    clock = [100.0]
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: clock[0])

    class Transport(_MockTransport):
        def post_json(self, url, **kwargs):
            result = super().post_json(url, **kwargs)
            if len(self.calls) == 1:
                clock[0] += 20
                return result
            assert kwargs["timeout"] <= 10
            sent = json.loads(kwargs["payload"]["messages"][1]["content"])
            return condition_response(corrected_response(value), sent)

    transport = Transport(value["verifier_response"])
    assert (
        OpenAICompatibleClaimVerifier(settings, transport=transport)
        .verify(value["question"], draft, evidence)
        .supported
    )
    assert len(transport.calls) == 2
