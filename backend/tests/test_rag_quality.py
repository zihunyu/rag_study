from __future__ import annotations

from ragkb.evaluation.rag_quality import evaluate_quality, retrieval_metrics


def test_retrieval_metrics_include_ranking_quality() -> None:
    metrics = retrieval_metrics([{"a", "b"}], [("a", "noise", "b")], k=3)

    assert metrics.recall_at_k == 1.0
    assert metrics.precision_at_k == 2 / 3
    assert metrics.mrr == 1.0
    assert 0 < metrics.ndcg_at_k < 1


def test_quality_gate_fails_when_a_metric_regresses() -> None:
    report = evaluate_quality(
        [
            {
                "query_type": "semantic",
                "answerable": True,
                "expected_answer": "住宿标准六百元",
                "actual_answer": "住宿标准六百元",
                "relevant_chunk_ids": ["right"],
                "retrieved_chunk_ids": ["wrong"],
                "actual_citation_chunk_ids": ["wrong"],
            }
        ],
        k=1,
        thresholds={"recall_at_k": 0.8, "answer_token_f1": 0.8},
    )

    assert report["passed"] is False
    assert "recall_at_k" in report["failed_metrics"]
