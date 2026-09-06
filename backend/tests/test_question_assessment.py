from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.model_http import OpenAICompatibleQuestionAssessor
from ragkb.api.app import create_app
from ragkb.application.question_assessment import ConservativeQuestionAssessor
from ragkb.domain.errors import InvalidProviderResponse, ProviderTimeout
from ragkb.domain.rag import QuestionAssessment, QuestionDisposition
from ragkb.runtime_components import build_runtime_components
from test_model_http_adapters import _MockTransport, _settings
from test_search_backed_qa import _components_with_search_qa


def _assessor(tmp_path, output):
    settings, _ = _settings(tmp_path)
    content = output if isinstance(output, str) else json.dumps(output)
    transport = _MockTransport({"choices": [{"message": {"content": content}}]})
    return OpenAICompatibleQuestionAssessor(settings, transport=transport), transport


@pytest.mark.parametrize("path", ["/api/v1/ask", "/api/v1/ask:stream"])
@pytest.mark.parametrize("production_assessor", [False, True])
@pytest.mark.parametrize(
    "question,status,reason,fields",
    [
        ("它的保修期是多久？", "needs_clarification", "missing_context", ["subject"]),
        ("请帮我预订明天的机票", "out_of_scope", "unsupported_operation", []),
    ],
)
def test_default_api_routes_question_without_retrieval_or_generation(
    tmp_path, monkeypatch, path, production_assessor, question, status, reason, fields
):
    runtime = build_runtime_components(
        storage_root=tmp_path / "storage", database_path=tmp_path / "control.sqlite3"
    )
    provider = runtime.qa_service.evidence_provider
    transport = None
    if production_assessor:
        assessor, transport = _assessor(
            tmp_path,
            {
                "disposition": status,
                "reason_code": reason,
                "clarification_fields": fields,
            },
        )
        provider.question_assessor = assessor

    def unexpected(*args, **kwargs):
        pytest.fail("early question routing must not retrieve, generate, verify, cache or cite")

    service = runtime.qa_service
    monkeypatch.setattr(provider.search_service, "search", unexpected)
    monkeypatch.setattr(service.generator, "generate", unexpected)
    monkeypatch.setattr(service.verifier, "verify", unexpected)
    monkeypatch.setattr(service.cache, "put", unexpected)
    monkeypatch.setattr(service.references, "source_url", unexpected)
    client = TestClient(create_app(runtime))
    for _ in range(2):
        response = client.post(path, json={"question": question})
        assert response.status_code == 200
        result = (
            json.loads(response.text.split("event: result\ndata: ")[1])
            if path.endswith(":stream")
            else response.json()
        )
        assert result["status"] == status
        assert result["answer"] is None and result["citations"] == []
        assert result["clarification_fields"] == fields
        assert result["warnings"] == [reason]
        assert result["verified"] and not result["retryable"] and not result["degraded"]
        saved = runtime.rag_repository.get_result(result["rag_run_id"])
        package = runtime.rag_repository.get_package(result["rag_run_id"])
        assert saved.clarification_fields == tuple(fields)
        assert package.disposition == status and package.disposition_reason == reason
        assert package.evidence == () and package.index_generation_id == "not_retrieved"
        assert package.question_assessor_revision == provider.question_assessor.revision
    if transport is not None:
        assert len(transport.calls) == 2
        payload = transport.calls[0]["payload"]
        assert json.loads(payload["messages"][1]["content"]) == {"question": question}
        assert "Do not infer out_of_scope" in payload["messages"][0]["content"]


@pytest.mark.parametrize(
    "question",
    [
        "保修期多久？",
        "产品 A 的保修期是多久？",
        "退款流程是什么？",
        "如何预订机票？",
        "帮我解释转账规则",
        "请说明删除账户的流程",
        "某个陌生星球的半径是多少？",
        "What is the refund policy?",
        "How do I book a flight?",
    ],
)
def test_local_assessor_does_not_turn_knowledge_questions_into_refusals(question):
    assert ConservativeQuestionAssessor().assess(question) == QuestionAssessment()


def test_clarified_question_continues_to_evidence_answer(tmp_path):
    runtime = _components_with_search_qa(tmp_path)
    client = TestClient(create_app(runtime))
    assert (
        client.post("/api/v1/ask", json={"question": "它的保修期多久？"}).json()["status"]
        == "needs_clarification"
    )
    result = client.post("/api/v1/ask", json={"question": "产品的保修期多久？"}).json()
    assert result["status"] == "answered" and result["citations"]
    assert result["clarification_fields"] == []


@pytest.mark.parametrize(
    "failure,code,retryable",
    [
        (ProviderTimeout, "QUESTION_ASSESSOR_UNAVAILABLE", True),
        (InvalidProviderResponse, "QUESTION_ASSESSOR_PROTOCOL_INVALID", False),
    ],
)
def test_assessor_failure_remains_system_error(tmp_path, monkeypatch, failure, code, retryable):
    runtime = _components_with_search_qa(tmp_path)
    assessor, _ = _assessor(tmp_path, {})

    def fail(*args, **kwargs):
        raise failure("private provider details")

    monkeypatch.setattr(assessor, "_post_json", fail)
    runtime.qa_service.evidence_provider.question_assessor = assessor
    response = TestClient(create_app(runtime)).post(
        "/api/v1/ask", json={"question": "保修期多久？"}
    )
    result = response.json()
    assert result["status"] == "system_error" and not result["verified"]
    assert result["retryable"] is retryable and result["warnings"] == [code]
    assert result["answer"] is None and not result["citations"]
    assert "private provider details" not in response.text


@pytest.mark.parametrize(
    "output",
    [
        "",
        "not json",
        "[]",
        {},
        {
            "disposition": "needs_clarification",
            "reason_code": "missing_context",
            "clarification_fields": [],
        },
        {
            "disposition": "answerable",
            "reason_code": "standalone_question",
            "clarification_fields": ["subject"],
        },
        {
            "disposition": "out_of_scope",
            "reason_code": "absent_evidence",
            "clarification_fields": [],
        },
        {
            "disposition": "needs_clarification",
            "reason_code": "missing_context",
            "clarification_fields": ["invented fact"],
        },
        {
            "disposition": "needs_clarification",
            "reason_code": "missing_context",
            "clarification_fields": ["subject", "subject"],
        },
        {"disposition": [], "reason_code": "missing_context", "clarification_fields": ["subject"]},
        {
            "disposition": "out_of_scope",
            "reason_code": "unsupported_operation",
            "clarification_fields": [],
            "answer": "unsafe model text",
        },
    ],
)
def test_invalid_structured_assessment_is_protocol_error(tmp_path, output):
    assessor, _ = _assessor(tmp_path, output)
    with pytest.raises(InvalidProviderResponse):
        assessor.assess("question")


def test_model_answerable_status_preserves_normal_question(tmp_path):
    assessor, _ = _assessor(
        tmp_path,
        {
            "disposition": "answerable",
            "reason_code": "standalone_question",
            "clarification_fields": [],
        },
    )
    assert assessor.assess("退款规则？").disposition is QuestionDisposition.ANSWERABLE
