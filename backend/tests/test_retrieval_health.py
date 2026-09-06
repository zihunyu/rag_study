from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.qa import InMemoryVerifiedAnswerCache
from ragkb.domain.errors import ProviderTimeout
from ragkb.domain.retrieval import IndexCandidate, RetrievalHealth
from ragkb.infrastructure.rag_repository import _package, _result
from test_search_backed_qa import _components_with_search_qa


class FaultIndex:
    revision = "retrieval-health-test"

    def __init__(self, failed=(), *, hits=True):
        self.failed = failed
        self.hits = hits

    def observed_security_watermark(self, context):
        return 0

    def candidates(self, channel):
        if channel in self.failed:
            raise ProviderTimeout("private-provider-error")
        return (
            (IndexCandidate("qa-chunk", "qa-version", None, channel, 1, 1.0),) if self.hits else ()
        )

    def search_bm25(self, query, context, limit):
        return self.candidates("bm25")

    def search_dense(self, vector, context, limit):
        return self.candidates("dense")


class NativeFaultIndex(FaultIndex):
    def search_hybrid(self, query, vector, context, *, bm25_limit, dense_limit):
        return self.candidates("bm25"), self.candidates("dense")


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
    "failed,hits,health,status,retryable",
    [
        ((), False, "healthy", "insufficient_evidence", False),
        (("bm25", "dense"), False, "unavailable", "system_error", True),
        (("bm25",), True, "degraded", "answered", False),
        (("dense",), True, "degraded", "answered", False),
        (("bm25",), False, "degraded", "system_error", True),
        (("dense",), False, "degraded", "system_error", True),
    ],
)
def test_search_health_reaches_qa_api_and_persistence(
    runtime, monkeypatch, path, failed, hits, health, status, retryable
):
    runtime.search_service.index = FaultIndex(failed, hits=hits)
    calls = []
    original = runtime.qa_service.generator.generate

    def generate(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(runtime.qa_service.generator, "generate", generate)
    client = TestClient(create_app(runtime))
    search = client.post("/api/search", json={"query": "保修期多久？"}).json()
    assert search["retrieval_health"] == health
    response = client.post(path, json={"question": "保修期多久？"})
    assert response.status_code == 200
    payload = (
        json.loads(response.text.split("event: result\ndata: ")[1])
        if path.endswith(":stream")
        else response.json()
    )
    assert payload["status"] == status
    assert payload["verified"] is (status != "system_error")
    assert payload["retryable"] is retryable
    assert payload["degraded"] is (health != "healthy")
    assert payload["retrieval_health"] == health
    assert set(search["warnings"]).issubset(payload["warnings"])
    assert "private-provider-error" not in response.text
    if status == "answered":
        assert payload["answer"] and payload["citations"] and calls
    else:
        assert payload["answer"] is None and payload["citations"] == []
        assert not calls
    package = runtime.rag_repository.get_package(payload["rag_run_id"])
    result = runtime.rag_repository.get_result(payload["rag_run_id"])
    assert package.retrieval_health is RetrievalHealth(health)
    assert package.retrieval_warnings == tuple(search["warnings"])
    assert result.retrieval_health is RetrievalHealth(health)
    assert result.retryable is retryable and result.warnings == tuple(payload["warnings"])


@pytest.mark.parametrize(
    "failed,hits,health",
    [
        ((), False, "healthy"),
        (("dense",), True, "degraded"),
        (("dense",), False, "degraded"),
        (("bm25", "dense"), False, "unavailable"),
    ],
)
def test_native_hybrid_fallback_tracks_successful_empty_channel(runtime, failed, hits, health):
    runtime.search_service.index = NativeFaultIndex(failed, hits=hits)
    response = (
        TestClient(create_app(runtime)).post("/api/ask", json={"question": "保修期多久？"}).json()
    )
    assert response["retrieval_health"] == health
    assert response["retryable"] is (health != "healthy" and not hits)


def test_degraded_cache_hit_keeps_current_health_and_warnings(runtime, monkeypatch):
    runtime.search_service.index = FaultIndex()
    runtime.qa_service.cache = InMemoryVerifiedAnswerCache()
    client = TestClient(create_app(runtime))
    assert client.post("/api/ask", json={"question": "保修期多久？"}).json()["verified"]
    runtime.search_service.index.failed = ("dense",)

    def unexpected_generate(*args):
        pytest.fail("same authorized evidence should reuse the verified draft")

    monkeypatch.setattr(runtime.qa_service.generator, "generate", unexpected_generate)
    degraded = client.post("/api/ask", json={"question": "保修期多久？"}).json()
    assert degraded["verified"] and degraded["degraded"]
    assert degraded["warnings"] == ["DENSE_RETRIEVAL_UNAVAILABLE"]
    runtime.search_service.index.failed = ()
    healthy = client.post("/api/ask", json={"question": "保修期多久？"}).json()
    assert healthy["verified"] and not healthy["degraded"] and healthy["warnings"] == []


def test_reranker_failure_preserves_degradation_in_answer(runtime, monkeypatch):
    runtime.search_service.index = FaultIndex()

    def fail(*args):
        raise ProviderTimeout("private-reranker-error")

    monkeypatch.setattr(runtime.search_service.reranker, "rerank", fail)
    result = (
        TestClient(create_app(runtime)).post("/api/ask", json={"question": "保修期多久？"}).json()
    )
    assert result["status"] == "answered" and result["verified"]
    assert result["retrieval_health"] == "degraded"
    assert result["warnings"] == ["RERANKER_UNAVAILABLE"]


@pytest.mark.parametrize("index_type", [FaultIndex, NativeFaultIndex])
@pytest.mark.parametrize("bm25_fails", [False, True])
def test_embedding_outage_keeps_bm25_health(runtime, monkeypatch, index_type, bm25_fails):
    runtime.search_service.index = index_type(("bm25",) if bm25_fails else ())

    def fail(*args):
        raise ProviderTimeout("private-embedding-error")

    monkeypatch.setattr(runtime.search_service.embedding, "embed", fail)
    result = (
        TestClient(create_app(runtime)).post("/api/ask", json={"question": "保修期多久？"}).json()
    )
    assert result["status"] == ("system_error" if bm25_fails else "answered")
    assert result["retryable"] is bm25_fails
    assert result["retrieval_health"] == ("unavailable" if bm25_fails else "degraded")
    assert "DENSE_RETRIEVAL_UNAVAILABLE" in result["warnings"]


def test_pre_retrieval_outage_is_retryable_without_claiming_zero_hits(runtime, monkeypatch):
    def fail(*args):
        raise ProviderTimeout("private-watermark-error")

    monkeypatch.setattr(runtime.search_service.index, "observed_security_watermark", fail)
    result = (
        TestClient(create_app(runtime)).post("/api/ask", json={"question": "保修期多久？"}).json()
    )
    assert result["status"] == "system_error" and result["retryable"]
    assert not result["verified"] and result["retrieval_health"] == "unavailable"


def test_old_persisted_packages_and_results_remain_readable(runtime):
    result = (
        TestClient(create_app(runtime)).post("/api/ask", json={"question": "保修期多久？"}).json()
    )
    package_data = asdict(runtime.rag_repository.get_package(result["rag_run_id"]))
    result_data = asdict(runtime.rag_repository.get_result(result["rag_run_id"]))
    for key in ("retrieval_health", "retrieval_warnings"):
        package_data.pop(key)
    for key in ("retrieval_health", "degraded", "retryable"):
        result_data.pop(key)
    assert _package(package_data).retrieval_health is RetrievalHealth.HEALTHY
    assert _package(package_data).retrieval_warnings == ()
    assert _result(result_data).retrieval_health is RetrievalHealth.HEALTHY
    assert not _result(result_data).retryable
