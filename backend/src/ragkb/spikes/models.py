"""Model adapter contract harness."""

from __future__ import annotations

from ragkb.adapters.egress_policy import decide_external_ai_egress
from ragkb.adapters.stubs import (
    DeterministicEmbedding,
    DeterministicGeneration,
    DeterministicReranker,
)
from ragkb.config.models import LoadedConfiguration
from ragkb.spikes.common import is_stubbed, result


def run_model_spike(loaded: LoadedConfiguration) -> dict[str, object]:
    embedding = DeterministicEmbedding()
    reranker = DeterministicReranker()
    generation = DeterministicGeneration()
    vectors_once = embedding.embed(["企业知识库", "权限过滤"])
    vectors_twice = embedding.embed(["企业知识库", "权限过滤"])
    ranking = reranker.rerank("permission filter", ["unrelated", "permission filter contract"])
    answer = generation.generate("question", ["E1 evidence"])
    allowed = loaded.user["ai_services"]["allowed_data_classifications"]
    confidential_pending_region = decide_external_ai_egress(
        classification="confidential",
        outbound_ai_allowed=True,
        allowed_classifications=allowed,
        provider_region_approved=False,
        cross_border_transfer_allowed=False,
        provider_is_cross_border=False,
    )
    confidential_approved_region = decide_external_ai_egress(
        classification="confidential",
        outbound_ai_allowed=True,
        allowed_classifications=allowed,
        provider_region_approved=True,
        cross_border_transfer_allowed=False,
        provider_is_cross_border=False,
    )
    restricted = decide_external_ai_egress(
        classification="restricted",
        outbound_ai_allowed=True,
        allowed_classifications=allowed,
        provider_region_approved=True,
        cross_border_transfer_allowed=True,
        provider_is_cross_border=False,
    )
    assertions = [
        {"name": "embedding_is_deterministic", "passed": vectors_once == vectors_twice},
        {
            "name": "embedding_dimension_contract",
            "passed": all(len(vector) == embedding.dimension for vector in vectors_once),
        },
        {"name": "reranker_contract", "passed": list(ranking) == [1, 0]},
        {"name": "generation_is_evidence_bound_stub", "passed": answer.startswith("stub_answer:")},
        {
            "name": "generation_refuses_without_evidence",
            "passed": generation.generate("question", []) == "insufficient_evidence",
        },
        {
            "name": "confidential_waits_for_provider_region_approval",
            "passed": not confidential_pending_region.allowed,
        },
        {
            "name": "confidential_allowed_after_region_approval",
            "passed": confidential_approved_region.allowed,
        },
        {"name": "restricted_outbound_always_denied", "passed": not restricted.allowed},
    ]
    blockers: list[str] = []
    for service in ("llm", "embedding", "reranker", "asr"):
        for suffix in ("provider", "endpoint", "model_id"):
            path = f"ai_services.{service}.{suffix}"
            if is_stubbed(loaded.stubbed_paths, path):
                blockers.append(path)
    if is_stubbed(loaded.stubbed_paths, "ai_services.outbound_ai_allowed"):
        blockers.append("ai_services.outbound_ai_allowed")
    for secret in loaded.secret_statuses:
        if (
            secret.name
            in {
                "LLM_API_KEY",
                "EMBEDDING_API_KEY",
                "RERANKER_API_KEY",
                "ASR_API_KEY",
            }
            and not secret.configured
        ):
            blockers.append(f"env:{secret.name}")
    blockers.extend(
        [
            "real_model_quality_not_measured",
            "real_model_latency_cost_and_rate_limits_not_measured",
            "provider_processing_region_not_approved",
        ]
    )
    return result(
        "model_adapter_contracts",
        assertions,
        blockers,
        {
            "embedding_revision": embedding.revision,
            "embedding_dimension": embedding.dimension,
            "reranker_revision": reranker.revision,
            "generation_revision": generation.revision,
        },
    )
