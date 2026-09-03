from __future__ import annotations

from ragkb.application.model_probe import run_bounded_model_probes, run_single_reranker_probe


class _Embedding:
    revision = "test-embedding"
    dimension = 3

    def embed(self, texts):
        return [[1.0, 0.0, 0.0]]


class _Reranker:
    revision = "test-reranker"

    def rerank(self, query, documents):
        return [0, 1]


def test_bounded_model_probe_records_counts_metrics_and_no_payloads() -> None:
    result = run_bounded_model_probes(_Embedding(), _Reranker())

    assert result["status"] == "MODEL_PROBES_PASSED"
    assert result["request_counts_before"] == {"embedding": 0, "reranker": 0}
    assert result["request_counts_after"] == {"embedding": 1, "reranker": 1}
    assert result["within_limits"] is True
    assert result["embedding"]["vector_dimension"] == 3
    assert result["reranker"]["known_relevant_ranked_first"] is True
    assert result["raw_response_in_output"] is False
    assert result["automatic_retry_count"] == 0


def test_probe_failure_is_secret_safe_and_never_retried() -> None:
    class _FailingEmbedding(_Embedding):
        def embed(self, texts):
            raise RuntimeError("credential and request body must stay hidden")

    result = run_bounded_model_probes(_FailingEmbedding(), _Reranker())

    assert result["status"] == "MODEL_PROBES_FAILED"
    assert result["embedding"]["error_type"] == "RuntimeError"
    assert result["embedding"]["error_code"] == "MODEL_PROBE_FAILED"
    assert result["request_counts_after"]["embedding"] == 1
    assert result["automatic_retry_count"] == 0
    assert "credential and request" not in str(result)


def test_reranker_only_probe_continues_cumulative_count_without_embedding() -> None:
    result = run_single_reranker_probe(_Reranker(), requests_already_used=1, request_limit=5)

    assert result["passed"] is True
    assert result["request_count_before"] == 1
    assert result["request_count_after"] == 2
    assert result["request_limit"] == 5
    assert result["automatic_retry_count"] == 0
