"""Model adapter and outbound-policy harness using typed env settings."""

from __future__ import annotations

from ragkb.adapters.egress_policy import decide_external_ai_egress
from ragkb.adapters.stubs import (
    DeterministicEmbedding,
    DeterministicGeneration,
    DeterministicReranker,
)
from ragkb.config import EnvLoadResult
from ragkb.spikes.common import result


def run_model_spike(loaded: EnvLoadResult) -> dict[str, object]:
    settings = loaded.settings
    if settings is None:
        return result(
            "model_adapter_contracts",
            [{"name": "typed_env_available", "passed": False}],
            ["config/.env:typed_validation_failed"],
        )
    embedding = DeterministicEmbedding()
    reranker = DeterministicReranker()
    generation = DeterministicGeneration()
    vectors = embedding.embed(["企业知识库", "权限过滤"])
    restricted = decide_external_ai_egress(
        classification="restricted",
        outbound_ai_allowed=settings.ai_outbound_allowed,
        allowed_classifications=settings.ai_outbound_allowed_classifications,
        provider_region_approved=True,
        cross_border_transfer_allowed=settings.ai_cross_border_transfer_allowed,
        provider_is_cross_border=False,
    )
    assertions = [
        {"name": "typed_env_available", "passed": True},
        {"name": "embedding_contract", "passed": len(vectors[0]) == embedding.dimension},
        {
            "name": "generation_refuses_without_evidence",
            "passed": generation.generate("q", []) == "insufficient_evidence",
        },
        {"name": "restricted_outbound_denied", "passed": not restricted.allowed},
        {
            "name": "reranker_contract",
            "passed": list(reranker.rerank("exact", ["noise", "exact"])) == [1, 0],
        },
    ]
    required = {
        "LLM_API_KEY",
        "LLM_MODEL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
        "RERANKER_API_KEY",
        "RERANKER_MODEL",
    }
    blockers = [f"{key}:not_configured" for key in sorted(required) if not loaded.configured[key]]
    blockers.extend(
        [
            "real_model_quality_not_measured",
            "real_model_latency_and_rate_limits_not_measured",
        ]
    )
    return result("model_adapter_contracts", assertions, blockers)
