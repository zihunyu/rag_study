"""Retrieval and generation metrics for a frozen, business-owned gold dataset."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    hit_rate: float
    mrr: float
    ndcg_at_k: float


@dataclass(frozen=True)
class GenerationMetrics:
    answer_token_f1: float
    citation_precision: float
    citation_recall: float
    no_answer_accuracy: float


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def retrieval_metrics(
    expected: Sequence[set[str]], predicted: Sequence[Sequence[str]], *, k: int
) -> RetrievalMetrics:
    if len(expected) != len(predicted) or k < 1:
        raise ValueError("retrieval metric inputs must align and k must be positive")
    recalls: list[float] = []
    precisions: list[float] = []
    hits: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    for relevant, ranking in zip(expected, predicted, strict=True):
        top = tuple(ranking[:k])
        matched = relevant.intersection(top)
        recalls.append(len(matched) / len(relevant) if relevant else float(not top))
        precisions.append(len(matched) / len(top) if top else float(not relevant))
        hits.append(float(bool(matched) if relevant else not top))
        reciprocal_ranks.append(
            next((1.0 / rank for rank, item in enumerate(top, start=1) if item in relevant), 0.0)
            if relevant
            else float(not top)
        )
        dcg = sum(
            1.0 / math.log2(rank + 1) for rank, item in enumerate(top, start=1) if item in relevant
        )
        ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
        ndcgs.append(dcg / ideal if ideal else float(not top))
    return RetrievalMetrics(
        recall_at_k=_mean(recalls),
        precision_at_k=_mean(precisions),
        hit_rate=_mean(hits),
        mrr=_mean(reciprocal_ranks),
        ndcg_at_k=_mean(ndcgs),
    )


def _answer_tokens(text: str) -> list[str]:
    return re.findall(r"[\u3400-\u9fff]|[a-z0-9]+", text.casefold())


def _token_f1(expected: str, actual: str) -> float:
    expected_tokens = _answer_tokens(expected)
    actual_tokens = _answer_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return float(expected_tokens == actual_tokens)
    expected_counts = {item: expected_tokens.count(item) for item in set(expected_tokens)}
    actual_counts = {item: actual_tokens.count(item) for item in set(actual_tokens)}
    overlap = sum(min(count, actual_counts.get(item, 0)) for item, count in expected_counts.items())
    if not overlap:
        return 0.0
    precision = overlap / len(actual_tokens)
    recall = overlap / len(expected_tokens)
    return 2 * precision * recall / (precision + recall)


def generation_metrics(cases: Sequence[Mapping[str, Any]]) -> GenerationMetrics:
    answer_scores: list[float] = []
    citation_precision: list[float] = []
    citation_recall: list[float] = []
    no_answer: list[float] = []
    for case in cases:
        answerable = bool(case["answerable"])
        expected_answer = str(case.get("expected_answer", ""))
        actual_answer = str(case.get("actual_answer", ""))
        expected_citations = set(map(str, case.get("relevant_chunk_ids", ())))
        actual_citations = set(map(str, case.get("actual_citation_chunk_ids", ())))
        if answerable:
            answer_scores.append(_token_f1(expected_answer, actual_answer))
            citation_precision.append(
                len(expected_citations & actual_citations) / len(actual_citations)
                if actual_citations
                else 0.0
            )
            citation_recall.append(
                len(expected_citations & actual_citations) / len(expected_citations)
                if expected_citations
                else float(not actual_citations)
            )
        else:
            no_answer.append(float(not actual_answer.strip()))
    return GenerationMetrics(
        answer_token_f1=_mean(answer_scores),
        citation_precision=_mean(citation_precision),
        citation_recall=_mean(citation_recall),
        no_answer_accuracy=_mean(no_answer),
    )


def evaluate_quality(
    cases: Sequence[Mapping[str, Any]], *, k: int, thresholds: Mapping[str, float]
) -> dict[str, object]:
    retrieval_cases = [case for case in cases if case.get("relevant_chunk_ids")]
    expected = [set(map(str, case.get("relevant_chunk_ids", ()))) for case in retrieval_cases]
    predicted = [tuple(map(str, case.get("retrieved_chunk_ids", ()))) for case in retrieval_cases]
    retrieval = retrieval_metrics(expected, predicted, k=k)
    generation = generation_metrics(cases)
    metrics = {**asdict(retrieval), **asdict(generation)}
    failures = tuple(
        sorted(
            name
            for name, minimum in thresholds.items()
            if name not in metrics or float(metrics[name]) < minimum
        )
    )
    buckets: dict[str, dict[str, object]] = {}
    for query_type in sorted({str(case.get("query_type", "unknown")) for case in retrieval_cases}):
        bucket_cases = [
            case for case in retrieval_cases if str(case.get("query_type", "unknown")) == query_type
        ]
        bucket_expected = [
            set(map(str, case.get("relevant_chunk_ids", ()))) for case in bucket_cases
        ]
        bucket_predicted = [
            tuple(map(str, case.get("retrieved_chunk_ids", ()))) for case in bucket_cases
        ]
        buckets[query_type] = asdict(retrieval_metrics(bucket_expected, bucket_predicted, k=k))
    return {
        "case_count": len(cases),
        "k": k,
        "metrics": metrics,
        "query_type_buckets": buckets,
        "thresholds": dict(thresholds),
        "failed_metrics": failures,
        "passed": not failures,
    }
