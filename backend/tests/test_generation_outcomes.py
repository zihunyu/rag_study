from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.model_http import OpenAICompatibleBufferedGenerator
from ragkb.adapters.rag_cache import RedisVerifiedAnswerCache
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.api.app import create_app
from ragkb.application.qa import InMemoryVerifiedAnswerCache
from ragkb.domain.errors import InvalidProviderResponse, ProviderTimeout
from ragkb.domain.rag import DraftAnswer, DraftAnswerStatus
from test_model_http_adapters import _MockTransport, _settings
from test_search_backed_qa import _components_with_search_qa
from test_trusted_qa import _evidence, _service

REFUSAL = {"status": "insufficient_evidence", "answer": "", "citation_ids": [], "claims": []}
ANSWER = {
    "status": "answered",
    "answer": "保修期三年",
    "citation_ids": ["E1"],
    "claims": [{"text": "保修期三年", "evidence_ids": ["E1"]}],
}


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask:stream"])
@pytest.mark.parametrize(
    "failure,warning,retryable",
    [
        (ProviderTimeout, "CLAIM_VERIFIER_UNAVAILABLE", True),
        (InvalidProviderResponse, "CLAIM_VERIFIER_PROTOCOL_INVALID", False),
        (ValueError, "CLAIM_VERIFIER_PROTOCOL_INVALID", False),
    ],
)
def test_verifier_failures_preserve_retry_policy(
    runtime, monkeypatch, path, failure, warning, retryable
):
    calls = []

    def fail(*args):
        calls.append(args)
        raise failure("private-upstream-details")

    monkeypatch.setattr(runtime.qa_service.verifier, "verify", fail)
    client = TestClient(create_app(runtime))
    for _ in range(2):
        response = client.post(path, json={"question": "保修期多久？"})
        assert response.status_code == 200
        result = (
            json.loads(response.text.split("event: result\ndata: ")[1])
            if path.endswith(":stream")
            else response.json()
        )
        assert result["status"] == "system_error"
        assert result["warnings"] == [warning] + (
            ["CLAIM_VERIFIER_TIMEOUT"] if failure is ProviderTimeout else []
        )
        assert result["retryable"] is retryable
        assert result["verified"] is False
        assert result["answer"] is None and result["citations"] == []
        saved = runtime.rag_repository.get_result(result["rag_run_id"])
        assert saved.retryable is retryable and saved.warnings == tuple(result["warnings"])
        assert "private-upstream-details" not in response.text
    assert len(calls) == 2  # Neither failure is cached as an answer.


def generator_for(tmp_path, output):
    settings, _ = _settings(tmp_path)
    transport = _MockTransport(
        {
            "choices": [
                {"message": {"content": output if isinstance(output, str) else json.dumps(output)}}
            ]
        }
    )
    return OpenAICompatibleBufferedGenerator(settings, transport=transport), transport


@pytest.mark.parametrize(
    "output",
    [
        "",
        "not json",
        "[]",
        "{}",
        '{"status":',
        {key: value for key, value in REFUSAL.items() if key != "status"},
        {key: value for key, value in ANSWER.items() if key != "status"},
        {**REFUSAL, "status": "unknown"},
        {**REFUSAL, "status": None},
        {**REFUSAL, "status": []},
        {**REFUSAL, "status": "answered"},
        {**REFUSAL, "answer": "refunds are available"},
        {**REFUSAL, "answer": " "},
        {**REFUSAL, "citation_ids": ["E1"]},
        {**REFUSAL, "claims": ANSWER["claims"]},
        {**REFUSAL, "claims": "[]"},
        {**ANSWER, "claims": []},
        {**ANSWER, "citation_ids": [1]},
        {**ANSWER, "claims": [{"text": "", "evidence_ids": ["E1"]}]},
        {**ANSWER, "claims": [{"text": "保修期三年", "evidence_ids": [1]}]},
        {**ANSWER, "claims": [{"text": "保修期三年", "evidence_ids": []}]},
    ],
)
def test_empty_or_inconsistent_model_outputs_are_protocol_errors(tmp_path, output):
    generator, _ = generator_for(tmp_path, output)
    with pytest.raises(InvalidProviderResponse):
        generator.generate("退款政策是什么？", (_evidence(),))


def test_generator_returns_explicit_refusal_status_and_prompts_for_it(tmp_path):
    generator, transport = generator_for(tmp_path, REFUSAL)
    draft = generator.generate("退款政策是什么？", (_evidence(),))
    assert draft == DraftAnswer("", (), (), status=DraftAnswerStatus.INSUFFICIENT_EVIDENCE)
    prompt = transport.calls[0]["payload"]["messages"][0]["content"]
    assert '"status":"insufficient_evidence"' in prompt
    assert "status (exactly answered or insufficient_evidence)" in prompt
    assert generator.revision.endswith(
        ":synthesized-markdown-v21-shared-condition-scope"
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for key, value in {
        "APP_ENV": "testing",
        "RAG_RUNTIME_PROFILE": "local",
        "VECTOR_BACKEND": "local",
        "AUTH_MODE": "local_single_user",
        "REAL_PROVIDER_CALLS_ENABLED": "false",
        "EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED": "false",
        "OTEL_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    return _components_with_search_qa(tmp_path)


@pytest.mark.parametrize("path", ["/api/ask", "/api/ask:stream"])
@pytest.mark.parametrize(
    "output,expected_status,warning",
    [
        (REFUSAL, "insufficient_evidence", "MODEL_INSUFFICIENT_EVIDENCE"),
        ("", "system_error", "GENERATION_PROTOCOL_INVALID"),
        (
            {"answer": "", "claims": [], "citation_ids": []},
            "system_error",
            "GENERATION_PROTOCOL_INVALID",
        ),
        ({**REFUSAL, "answer": "invented policy"}, "system_error", "GENERATION_PROTOCOL_INVALID"),
        (
            {
                **ANSWER,
                "citation_ids": ["E999"],
                "claims": [{"text": "保修期三年", "evidence_ids": ["E999"]}],
            },
            "system_error",
            "CITATION_VALIDATION_FAILED",
        ),
    ],
)
def test_refusal_and_protocol_failure_have_distinct_api_results(
    runtime, tmp_path, monkeypatch, path, output, expected_status, warning
):
    generator, transport = generator_for(tmp_path, output)
    service = runtime.qa_service
    service.generator = generator
    service.cache = InMemoryVerifiedAnswerCache()

    def unexpected(*args):
        pytest.fail(
            "a refusal or protocol failure must not verify claims, cache, or issue citations"
        )

    monkeypatch.setattr(service.verifier, "verify", unexpected)
    monkeypatch.setattr(service.cache, "put", unexpected)
    monkeypatch.setattr(service.references, "source_url", unexpected)
    client = TestClient(create_app(runtime))
    # Repeating the request must call the model again; refusals are not answer-cache entries.
    for _ in range(2):
        response = client.post(path, json={"question": "退款政策是什么？"})
        assert response.status_code == 200
        result = (
            json.loads(response.text.split("event: result\ndata: ")[1])
            if path.endswith(":stream")
            else response.json()
        )
        assert result["status"] == expected_status
        assert result["verified"] is (expected_status == "insufficient_evidence")
        assert result["answer"] is None and result["citations"] == []
        assert result["warnings"] == [warning]
        assert not result["retryable"]
        assert runtime.rag_repository.get_result(result["rag_run_id"]).status == expected_status
    assert len(transport.calls) == 2


def test_model_service_failure_is_retryable_system_error(runtime, monkeypatch):
    def unavailable(*args):
        raise ProviderTimeout("private-upstream-error")

    monkeypatch.setattr(runtime.qa_service.generator, "generate", unavailable)
    result = (
        TestClient(create_app(runtime))
        .post("/api/ask", json={"question": "退款政策是什么？"})
        .json()
    )
    assert result["status"] == "system_error" and not result["verified"]
    assert result["retryable"] and result["answer"] is None
    assert result["warnings"] == ["GENERATION_UNAVAILABLE_AUTHORIZED_EVIDENCE_ONLY"]


@pytest.mark.parametrize(
    "draft",
    [
        DraftAnswer("", (), ()),  # No explicit refusal status: still an invalid answer.
        DraftAnswer("hidden answer", (), (), DraftAnswerStatus.INSUFFICIENT_EVIDENCE),
    ],
)
def test_application_rejects_inconsistent_drafts_from_other_generators(tmp_path, draft):
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))

    class Generator:
        revision = "invalid-draft"

        def generate(self, *args):
            return draft

    service.generator = Generator()
    result = service.ask("退款政策是什么？", "tenant-1", "user-1")
    assert result.status == "system_error" and not result.verified


def test_refusal_still_obeys_final_permission_gate(tmp_path, monkeypatch):
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))

    def revoked(*args):
        service.permission.allowed = False
        return DraftAnswer("", (), (), DraftAnswerStatus.INSUFFICIENT_EVIDENCE)

    monkeypatch.setattr(service.generator, "generate", revoked)
    result = service.ask("退款政策是什么？", "tenant-1", "user-1")
    assert result.status == "system_error" and not result.verified
    assert result.warnings == ("FINAL_PERMISSION_RECHECK_FAILED",)


def test_answer_caches_do_not_store_or_reinterpret_refusals(tmp_path):
    provider = SyntheticEvidenceProvider((_evidence(),))
    package = provider.build_package("question", "tenant", "user")
    draft = DraftAnswer("", (), (), DraftAnswerStatus.INSUFFICIENT_EVIDENCE)
    memory = InMemoryVerifiedAnswerCache()
    memory.put(package, draft)
    assert memory.get(package) is None

    class Redis:
        stored = None

        def get_json(self, *args):
            return self.stored

        def set_json(self, namespace, key, value, ttl):
            self.stored = value

    redis = Redis()
    cache = RedisVerifiedAnswerCache(redis, ttl_seconds=10)
    cache.put(package, draft)
    assert redis.stored is None
    redis.stored = REFUSAL
    assert cache.get(package) is None
    generator, _ = generator_for(tmp_path, ANSWER)
    answered = generator.generate("保修期？", (_evidence(),))
    cache.put(package, answered)
    assert cache.get(package) == answered
    redis.stored.pop("status")  # Existing non-empty answered entries remain readable.
    assert cache.get(package) == replace(answered, status=DraftAnswerStatus.ANSWERED)
