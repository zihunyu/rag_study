"""Bounded, secret-safe G2 real model probe orchestration."""

from __future__ import annotations

import math
import time

from ragkb.contracts.ports import EmbeddingPort, RerankerPort


def _safe_failure(provider: str, error: Exception) -> dict[str, object]:
    error_type = type(error).__name__
    lowered = error_type.casefold()
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    retryable = (
        "timeout" in lowered
        or "connect" in lowered
        or status_code in {408, 425, 429, 500, 502, 503, 504}
    )
    if "timeout" in lowered:
        code = "MODEL_PROBE_TIMEOUT"
    elif "http" in lowered or "status" in lowered:
        code = "MODEL_PROBE_HTTP_ERROR"
    else:
        code = "MODEL_PROBE_FAILED"
    result: dict[str, object] = {
        "provider": provider,
        "passed": False,
        "error_type": error_type,
        "error_code": code,
        "retryable": retryable,
        "request_body_in_output": False,
        "credential_in_output": False,
    }
    if isinstance(status_code, int):
        result["http_status"] = status_code
    return result


def run_single_reranker_probe(
    reranker: RerankerPort,
    *,
    requests_already_used: int,
    request_limit: int,
) -> dict[str, object]:
    if requests_already_used >= request_limit:
        raise RuntimeError("RERANKER_PROBE_REQUEST_LIMIT_REACHED")
    started = time.perf_counter()
    try:
        order = list(
            reranker.rerank(
                "Which synthetic document states a three-year warranty?",
                [
                    "Synthetic public document: the equipment warranty is three years.",
                    "Synthetic public document: the cafeteria opens at noon.",
                ],
            )
        )
        result: dict[str, object] = {
            "provider": "configured_reranker_provider",
            "passed": bool(order) and order[0] == 0,
            "result_count": len(order),
            "indexes_unique": len(order) == len(set(order)),
            "indexes_in_range": all(0 <= index < 2 for index in order),
            "known_relevant_ranked_first": bool(order) and order[0] == 0,
            "estimated_cost_available": False,
            "request_body_in_output": False,
            "credential_in_output": False,
        }
    except Exception as error:
        result = _safe_failure("configured_reranker_provider", error)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    result["request_count_before"] = requests_already_used
    result["request_count_after"] = requests_already_used + 1
    result["request_limit"] = request_limit
    result["within_limit"] = requests_already_used + 1 <= request_limit
    result["automatic_retry_count"] = 0
    result["synthetic_public_input_only"] = True
    result["raw_response_in_output"] = False
    result["base_url_in_output"] = False
    return result


def run_bounded_model_probes(
    embedding: EmbeddingPort,
    reranker: RerankerPort,
    *,
    embedding_limit: int = 5,
    reranker_limit: int = 5,
) -> dict[str, object]:
    if embedding_limit < 1 or reranker_limit < 1:
        raise ValueError("probe request limits must be positive")
    counts_before = {"embedding": 0, "reranker": 0}
    counts_after = dict(counts_before)
    started = time.perf_counter()
    embedding_started = time.perf_counter()
    counts_after["embedding"] += 1
    try:
        vectors = embedding.embed(["synthetic public test: equipment warranty is three years"])
        vector = list(vectors[0])
        embedding_result: dict[str, object] = {
            "provider": "configured_embedding_provider",
            "passed": len(vectors) == 1 and len(vector) == embedding.dimension,
            "latency_ms": round((time.perf_counter() - embedding_started) * 1000, 3),
            "vector_dimension": len(vector),
            "expected_dimension": embedding.dimension,
            "vector_norm": round(math.sqrt(sum(value * value for value in vector)), 8),
            "finite_values": all(math.isfinite(value) for value in vector),
            "estimated_cost_available": False,
            "request_body_in_output": False,
            "credential_in_output": False,
        }
    except Exception as error:
        embedding_result = _safe_failure("configured_embedding_provider", error)
        embedding_result["latency_ms"] = round((time.perf_counter() - embedding_started) * 1000, 3)

    reranker_started = time.perf_counter()
    counts_after["reranker"] += 1
    try:
        order = list(
            reranker.rerank(
                "Which synthetic document states a three-year warranty?",
                [
                    "Synthetic public document: the equipment warranty is three years.",
                    "Synthetic public document: the cafeteria opens at noon.",
                ],
            )
        )
        reranker_result: dict[str, object] = {
            "provider": "configured_reranker_provider",
            "passed": bool(order) and order[0] == 0,
            "latency_ms": round((time.perf_counter() - reranker_started) * 1000, 3),
            "result_count": len(order),
            "indexes_unique": len(order) == len(set(order)),
            "indexes_in_range": all(0 <= index < 2 for index in order),
            "known_relevant_ranked_first": bool(order) and order[0] == 0,
            "estimated_cost_available": False,
            "request_body_in_output": False,
            "credential_in_output": False,
        }
    except Exception as error:
        reranker_result = _safe_failure("configured_reranker_provider", error)
        reranker_result["latency_ms"] = round((time.perf_counter() - reranker_started) * 1000, 3)

    within_limits = (
        counts_after["embedding"] <= embedding_limit and counts_after["reranker"] <= reranker_limit
    )
    return {
        "status": (
            "MODEL_PROBES_PASSED"
            if embedding_result["passed"] and reranker_result["passed"] and within_limits
            else "MODEL_PROBES_FAILED"
        ),
        "request_counts_before": counts_before,
        "request_counts_after": counts_after,
        "request_limits": {"embedding": embedding_limit, "reranker": reranker_limit},
        "within_limits": within_limits,
        "embedding": embedding_result,
        "reranker": reranker_result,
        "total_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "synthetic_public_input_only": True,
        "raw_response_in_output": False,
        "base_url_in_output": False,
        "credential_in_output": False,
        "automatic_retry_count": 0,
    }
