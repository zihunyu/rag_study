"""Capacity arithmetic using typed env inputs and explicit assumptions."""

from __future__ import annotations

from ragkb.config import EnvLoadResult
from ragkb.spikes.common import result


def run_capacity_spike(loaded: EnvLoadResult) -> dict[str, object]:
    settings = loaded.settings
    if settings is None:
        return result(
            "capacity_cost",
            [{"name": "typed_env_available", "passed": False}],
            ["config/.env:typed_validation_failed"],
        )
    planning_chunk_count = 150_000
    text_bytes = planning_chunk_count * settings.chunk_target_tokens * 3
    vector_bytes = planning_chunk_count * settings.embedding_dimension * 4
    raw_bytes = text_bytes + vector_bytes
    assertions = [
        {"name": "typed_env_available", "passed": True},
        {
            "name": "embedding_and_zilliz_dimensions_match",
            "passed": settings.embedding_dimension == settings.zilliz_cloud_dimension,
        },
        {
            "name": "local_capacity_positive",
            "passed": settings.local_storage_max_gb > 0,
        },
    ]
    return result(
        "capacity_cost",
        assertions,
        [
            "real_file_size_distribution_not_measured",
            "zilliz_cloud_capacity_and_cost_not_measured",
            "backup_throughput_not_measured",
        ],
        {
            "planning_chunk_count": planning_chunk_count,
            "raw_text_plus_vector_bytes": raw_bytes,
            "local_storage_limit_gb": settings.local_storage_max_gb,
            "assumptions": {
                "utf8_bytes_per_token": 3,
                "float_bytes": 4,
                "real_acceptance": False,
            },
        },
    )
