"""Offline admission, retry accounting, adaptive retrieval and fail-closed answers."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace

import httpx
import pytest
from ragkb.adapters.model_http import HttpxJsonTransport
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.api.citation_projection import reader_report
from ragkb.application.qa_budget import (
    Limits,
    QuestionBudget,
    budget_stage,
    choose_profile,
    current,
    question_budget,
)
from ragkb.application.reading_scope import ReadingOptions, reading_scope
from ragkb.config import EnvSettings
from ragkb.contracts.rag import EvidenceSelection
from ragkb.domain.errors import QABudgetExceeded
from ragkb.domain.rag import AnswerStatus
from test_hybrid_search import _candidate, _chunk
from test_retrieval_query_budget import QUESTION, RecordingIndex, ask, services
from test_trusted_qa import _evidence, _service


@pytest.mark.parametrize(
    "question,expected",
    [
        ("设备保修多久？", "simple"),
        (QUESTION, "standard"),
        ("2025 版与 2026 版的售后政策有什么区别？", "standard"),
        ("如何计算总价？", "standard"),
        ("设备是什么？", "simple"),
        ("总结这份文档", "standard"),
        ("它能否使用？", "standard"),
    ],
)
def test_auto_profile_is_conservative(question, expected):
    assert choose_profile(question) == expected


def test_deep_requires_explicit_option_and_nested_question_keeps_one_budget():
    with reading_scope(ReadingOptions(budget_profile="deep")):
        with question_budget(EnvSettings(), QUESTION) as outer:
            with question_budget(EnvSettings(), "保修多久？") as inner:
                assert inner is outer
                assert inner.profile == "deep" and inner.limits.calls == 48
    assert current.get() is None


def test_retrieval_cannot_spend_generation_and_verification_reserves():
    budget = QuestionBudget("standard", Limits(10, 100000, 10000, 300))
    for _ in range(5):
        r = budget.reserve({"input": ["a"]})
        budget.settle(r, {"prompt_tokens": 1}, sent=True)
    with pytest.raises(QABudgetExceeded, match="QA_MODEL_CALL_BUDGET_EXHAUSTED"):
        budget.reserve({"input": ["a"]})
    with budget_stage("generation"):
        budget.reserve({"messages": [], "max_tokens": 100})
        with pytest.raises(QABudgetExceeded):
            budget.reserve({"messages": [], "max_tokens": 100})
    with budget_stage("verification"):
        for _ in range(4):
            budget.reserve({"messages": [], "max_tokens": 100})
        with pytest.raises(QABudgetExceeded):
            budget.reserve({"input": []})
    assert budget.calls == 10


@pytest.mark.parametrize(
    "limit,reason",
    [
        (Limits(20, 100, 10000, 300), "QA_INPUT_BUDGET_EXHAUSTED"),
        (Limits(20, 100000, 100, 300), "QA_OUTPUT_BUDGET_EXHAUSTED"),
    ],
)
def test_token_admission_blocks_before_spending(limit, reason):
    budget = QuestionBudget("standard", limit)
    with pytest.raises(QABudgetExceeded, match=reason):
        budget.reserve({"messages": [], "max_tokens": 500})
    assert budget.calls == 0


def test_usage_above_reservation_is_not_clipped_and_unknown_usage_is_not_free():
    budget = QuestionBudget("standard", Limits(20, 100000, 10000, 300))
    r = budget.reserve({"messages": [], "max_tokens": 100})
    budget.settle(r, {"prompt_tokens": 20000, "completion_tokens": 200}, sent=True)
    assert (budget.input_tokens, budget.output_tokens) == (20000, 200)
    r = budget.reserve({"messages": [], "max_tokens": 100})
    budget.settle(r, {}, sent=True)
    assert budget.input_tokens == 20000 + r.input_tokens
    assert budget.output_tokens == 300 and budget.unknown_usage_calls == 1


def test_unsent_call_refunds_reservation():
    budget = QuestionBudget("standard", Limits(10, 100000, 10000, 300))
    r = budget.reserve({"messages": [], "max_tokens": 100})
    budget.settle(r, {}, sent=False)
    assert (budget.calls, budget.input_tokens, budget.output_tokens) == (0, 0, 0)


def test_stage_time_reserves_are_absolute_and_unused_time_flows_forward(monkeypatch):
    budget = QuestionBudget("standard", Limits(10, 100000, 10000, 300), started=0)
    monkeypatch.setattr("ragkb.application.qa_budget.time.monotonic", lambda: 151)
    with pytest.raises(QABudgetExceeded, match="QA_TIME_BUDGET_EXHAUSTED"):
        budget.timeout(300)
    with budget_stage("generation"):
        assert budget.timeout(300) == 44
    with budget_stage("verification"):
        assert budget.timeout(300) == 149


def test_shared_parallel_contexts_cannot_overspend():
    budget = QuestionBudget("standard", Limits(4, 100000, 10000, 300))
    token = current.set(budget)

    def attempt():
        try:
            current.get().reserve({"input": ["query"]})
            return True
        except QABudgetExceeded:
            return False

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(copy_context().run, attempt) for _ in range(20)]
            assert sum(f.result() for f in futures) == 2
    finally:
        current.reset(token)


def test_real_http_retry_boundary_counts_each_sent_attempt(monkeypatch):
    transport = HttpxJsonTransport(
        EnvSettings(
            model_account_limit_enabled=False,
            model_usage_enabled=False,
            model_http_max_retries=3,
        )
    )
    attempts = []

    def send(url, **kwargs):
        attempts.append(kwargs)
        return httpx.Response(429, json={"error": "busy"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(transport._client, "post", send)
    monkeypatch.setattr(transport, "_delay", lambda *args, **kwargs: None)
    budget = QuestionBudget("standard", Limits(2, 100000, 10000, 300))
    token = current.set(budget)
    try:
        with budget_stage("verification"), pytest.raises(QABudgetExceeded):
            transport.post_json(
                "https://example.test/chat/completions",
                headers={},
                payload={"messages": [], "max_tokens": 100},
                timeout=10,
            )
        assert len(attempts) == budget.calls == budget.unknown_usage_calls == 2
    finally:
        current.reset(token)
        transport.close()


@pytest.mark.parametrize("profile,maximum", [("standard", 4), ("deep", 8)])
def test_first_pass_reserves_quota_and_each_new_facet_is_reassessed(profile, maximum):
    class GrowingIndex(RecordingIndex):
        def search_bm25(self, query, context, limit):
            super().search_bm25(query, context, limit)
            return (_candidate(str(len(self.queries)), "bm25", 1),)

    index, search, provider, _ = services(index=GrowingIndex())
    search.control_plane._chunks.update(
        {str(i): _chunk(str(i), f"独立资料 {i}", str(i)) for i in range(1, 10)}
    )

    class AdaptiveSelector:
        revision = "test"

        def select(self, question, evidence):
            return EvidenceSelection(
                tuple(e.evidence_id for e in evidence),
                "partial",
                (f"缺失方面 {len(index.queries)}",),
                missing_aspects=("其他条件",),
            )

    provider.evidence_selector = AdaptiveSelector()
    with reading_scope(ReadingOptions(budget_profile=profile)):
        package = ask(provider)
    receipt = package.coverage_report["retrieval_budget"]
    assert len(index.queries) == maximum
    assert receipt == {
        "profile": profile,
        "maximum": maximum,
        "used": maximum,
        "remaining": 0,
        "initial_limit": 2,
        "stop_reason": "retrieval_budget_exhausted",
    }
    assert package.retrieval_queries == tuple(index.queries)
    assert all(context is index.contexts[0] for context in index.contexts)


def test_sufficient_first_pass_skips_supplements():
    index, _, provider, selector = services()
    selector.select = lambda question, evidence: EvidenceSelection(
        tuple(e.evidence_id for e in evidence)
    )
    package = ask(provider)
    assert len(index.queries) == 2
    assert package.coverage_report["retrieval_budget"]["stop_reason"] == "sufficient"


def test_no_gain_stops_before_spending_remaining_quota():
    index, _, provider, _ = services()
    package = ask(provider)
    assert len(index.queries) == 3
    assert package.coverage_report["retrieval_budget"]["remaining"] == 1
    assert package.coverage_report["retrieval_budget"]["stop_reason"] == "no_new_evidence"


@pytest.mark.parametrize("phase", ["retrieval", "generation", "verification"])
def test_budget_exhaustion_never_releases_a_draft_or_claims_the_corpus_is_empty(tmp_path, phase):
    service, repository, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))

    def fail(*args, **kwargs):
        raise QABudgetExceeded("QA_MODEL_CALL_BUDGET_EXHAUSTED")

    if phase == "retrieval":
        service.evidence_provider.build_package = fail
    elif phase == "generation":
        service.generator.generate = fail
    else:
        service.verifier.verify = fail
    result = service.ask("保修多久？", "tenant", "user")
    assert result.status is AnswerStatus.BUDGET_EXHAUSTED
    assert not result.verified and result.answer is None and not result.citations
    assert result.retryable is False
    assert not result.degraded
    assert repository.get_result(result.rag_run_id).status is AnswerStatus.BUDGET_EXHAUSTED


@pytest.mark.parametrize("blocking", [True, False])
def test_independent_partial_answer_is_verified_but_missing_prerequisite_blocks_conclusion(
    tmp_path, blocking
):
    provider = SyntheticEvidenceProvider((_evidence(),))
    original = provider.build_package
    provider.build_package = lambda *args, **kwargs: replace(
        original(*args, **kwargs),
        coverage="partial",
        coverage_report={
            "blocking_missing": blocking,
            "retrieval_budget": {"stop_reason": "retrieval_budget_exhausted"},
        },
    )
    service, _, _ = _service(tmp_path, provider)
    result = service.ask(QUESTION, "tenant", "user")
    assert result.status is (AnswerStatus.BUDGET_EXHAUSTED if blocking else AnswerStatus.ANSWERED)
    assert bool(result.answer) is (not blocking)
    assert result.coverage == "partial"


def test_reader_can_see_operational_budget_without_private_source_metadata():
    report = reader_report(
        {
            "budget": {"model_calls": 3, "prompt": "private"},
            "retrieval_budget": {"used": 2, "queries": ["private"]},
            "missing_aspects": ["private"],
            "stop_reason": "budget_exhausted",
        }
    )
    assert report["budget"] == {"model_calls": 3}
    assert report["retrieval_budget"] == {"used": 2}
    assert "missing_aspects" not in report


def test_http_query_vector_cache_saves_calls_but_not_retrieval_quota(tmp_path, monkeypatch):
    from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache
    from ragkb.adapters.model_http import OpenAICompatibleEmbeddingAdapter
    from test_hybrid_search import _context

    settings = EnvSettings(
        embedding_base_url="https://example.test/v1",
        embedding_model="test",
        embedding_dimension=2,
        model_account_limit_enabled=False,
        model_usage_enabled=False,
    )
    transport = HttpxJsonTransport(settings)
    sent = []

    def send(url, **kwargs):
        sent.append(url)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"prompt_tokens": 6}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(transport._client, "post", send)
    index, search, _, _ = services()
    search.embedding = OpenAICompatibleEmbeddingAdapter(
        settings,
        transport=transport,
        external_call_approved=True,
        cache=SQLiteEmbeddingCache(tmp_path / "vectors.sqlite3"),
    )
    try:
        with question_budget(settings, "保修多久？") as budget:
            for _ in range(2):
                search.search("保修多久？", _context(), query_limit=1)
            assert len(sent) == budget.calls == 1
            assert len(index.queries) == len(budget.queries) == 2
            with pytest.raises(QABudgetExceeded, match="QA_RETRIEVAL_BUDGET_EXHAUSTED"):
                search.search("保修多久？", _context(), query_limit=1)
            assert len(sent) == 1 and len(index.queries) == 2
    finally:
        transport.close()


@pytest.mark.parametrize(
    "patch",
    [
        {"blocking_missing": "yes"},
        {"missing_aspects": ["x" * 301]},
        {"coverage": "sufficient", "blocking_missing": True},
    ],
)
def test_model_missing_aspect_contract_rejects_invalid_flags(tmp_path, patch):
    import json

    from ragkb.adapters.evidence_selection import ModelEvidenceSelector
    from ragkb.domain.errors import InvalidProviderResponse
    from test_model_http_adapters import _MockTransport, _settings

    settings, _ = _settings(tmp_path)
    value = {
        "source_ids": ["E1"],
        "coverage": "partial",
        "queries": [],
        "clarification": None,
        "missing_aspects": ["费用"],
        "blocking_missing": False,
        **patch,
    }
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(value)}}]})
    with pytest.raises(InvalidProviderResponse, match="EVIDENCE_SELECTION_INVALID"):
        ModelEvidenceSelector(settings, transport).select(QUESTION, (_evidence(),))


def test_supplement_denied_by_model_budget_keeps_last_assessed_evidence(tmp_path):
    index, search, provider, _ = services()
    original = search.search

    def limited(*args, **kwargs):
        if index.queries:
            raise QABudgetExceeded("QA_INPUT_BUDGET_EXHAUSTED")
        return original(*args, **kwargs)

    search.search = limited
    service, _, _ = _service(tmp_path, provider)
    service.generator.answer = "保修三年"
    result = service.ask(QUESTION, "tenant-1", "user", clearance_level=2)
    assert result.status is AnswerStatus.ANSWERED and result.verified
    assert result.coverage == "partial" and result.citations
    assert result.coverage_report["retrieval_budget"]["stop_reason"] == "model_budget_exhausted"
    assert result.coverage_report["retrieval_budget"]["used"] == 2


@pytest.mark.parametrize("endpoint", ["/api/ask", "/api/ask:stream"])
def test_deep_profile_reaches_both_http_question_entrypoints(tmp_path, endpoint):
    import json

    from fastapi.testclient import TestClient
    from ragkb.api.app import create_app
    from test_upload_api import _components

    with TestClient(create_app(_components(tmp_path))) as client:
        response = client.post(
            endpoint, json={"question": QUESTION, "reading": {"budget_profile": "deep"}}
        )
        assert response.status_code == 200
        result = (
            response.json()
            if endpoint == "/api/ask"
            else json.loads(
                next(
                    frame
                    for frame in response.text.split("\n\n")
                    if frame.startswith("event: result")
                ).split("data: ", 1)[1]
            )
        )
        assert result["coverage_report"]["budget"]["profile"] == "deep"
        assert result["coverage_report"]["budget"]["max_retrieval_queries"] == 8
        assert (
            client.post(
                endpoint, json={"question": QUESTION, "reading": {"budget_profile": "unlimited"}}
            ).status_code
            == 422
        )
