import json

import pytest
from ragkb.adapters.conversation_context import ModelContextResolver
from ragkb.application.qa_performance import record_event
from ragkb.domain.errors import InvalidProviderResponse, ProviderRateLimited, ProviderTimeout
from test_model_http_adapters import _MockTransport, _settings
from test_workspace_redesign import stream_result
from test_workspace_redesign import workspace as workspace_fixture

workspace = workspace_fixture


@pytest.mark.parametrize(
    "error,code,status",
    [
        (ProviderRateLimited("MODEL_PROVIDER_RATE_LIMITED"), "MODEL_PROVIDER_RATE_LIMITED", 429),
        (
            ProviderRateLimited("MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET"),
            "MODEL_ACCOUNT_REQUEST_EXCEEDS_TOKEN_BUDGET",
            None,
        ),
        (ProviderTimeout("MODEL_PROVIDER_TIMEOUT"), "MODEL_PROVIDER_TIMEOUT", None),
        (
            InvalidProviderResponse("CONVERSATION_CONTEXT_INVALID"),
            "CONVERSATION_CONTEXT_INVALID",
            None,
        ),
        (ValueError("PRIVATE upstream prompt"), "CONVERSATION_EXECUTION_FAILED", None),
    ],
)
def test_context_failure_is_durable_and_specific_before_retrieval(
    workspace, monkeypatch, error, code, status
):
    client, runtime, service, space = workspace
    conversation = client.post(
        "/api/conversations", headers={"Idempotency-Key": "failure-case"}, json={"space_id": space}
    ).json()["id"]

    def fail(question, history):
        record_event(
            "model_http",
            model="fixture",
            outcome="429" if status == 429 else "failed",
            sent=status == 429,
            queue_seconds=0,
            network_seconds=0.1,
        )
        raise error

    monkeypatch.setattr(service.resolver, "resolve", fail)
    monkeypatch.setattr(
        runtime.qa_service, "ask", lambda *a, **kw: pytest.fail("retrieval must not run")
    )
    turn = stream_result(client, conversation, "明确的问题", "failure-turn")
    assert turn["state"] == "failed" and turn["rag_run_id"] is None
    assert turn["error_code"] == code
    failure = turn["result"]["coverage_report"]["execution_failure"]
    assert failure["stage"] == "conversation_context"
    assert failure["request_id"] == turn["id"] and failure.get("http_status") == status
    assert turn["result"]["answer"] is None and not turn["result"]["verified"]
    assert "PRIVATE" not in json.dumps(turn)
    assert any(
        e.get("name") == "conversation.resolve"
        for e in turn["result"]["coverage_report"]["performance"]["events"]
    )
    saved = client.get(f"/api/conversations/{conversation}/turns/{turn['id']}").json()
    assert saved["result"] == turn["result"]


@pytest.mark.parametrize(
    "question,resolved,answer,should_call",
    [
        ("合同期限是多少？", "合同期限是多少？", "两年。", False),
        ("它的期限是多少？", "它的期限是多少？", "两年。", True),
        ("其他条款有哪些？", "其他条款有哪些？", "条款。", True),
        ("合同期限是多少？", "合同期限是多少？", "", True),
        ("合同期限是多少？", "甲合同期限是多少？", "两年。", True),
    ],
)
def test_only_confirmed_identical_standalone_resolution_is_reused(
    tmp_path, question, resolved, answer, should_call
):
    settings, _ = _settings(tmp_path)
    transport = _MockTransport(
        {
            "choices": [
                {"message": {"content": json.dumps({"question": question, "clarification": None})}}
            ]
        }
    )
    resolver = ModelContextResolver(settings, transport)
    result = resolver.resolve(
        question, [{"question": question, "resolved_question": resolved, "answer": answer}]
    )
    assert result.question == question
    assert bool(transport.calls) == should_call
