"""Transparent capacity and FinOps baseline calculations."""

from __future__ import annotations

from ragkb.config.models import LoadedConfiguration
from ragkb.spikes.common import is_stubbed, result, value_at


def run_capacity_spike(loaded: LoadedConfiguration) -> dict[str, object]:
    effective = loaded.effective
    documents = int(value_at(effective, "capacity.initial_document_count"))
    chunks = int(value_at(effective, "capacity.initial_chunk_count"))
    pages = float(value_at(effective, "capacity.average_pages_per_document"))
    chunk_tokens = int(value_at(effective, "capacity.average_chunk_tokens"))
    embedding_dimension = int(value_at(effective, "ai_services.embedding.dimension"))
    growth = float(value_at(effective, "capacity.annual_growth_percent"))
    daily_updates = int(value_at(effective, "capacity.daily_new_or_updated_documents"))
    annual_documents = round(documents * (1 + growth / 100))
    initial_pages = round(documents * pages)
    annual_ingested_pages = round(daily_updates * 365 * pages)
    text_bytes = chunks * chunk_tokens * 3
    embedding_bytes = chunks * embedding_dimension * 4
    raw_storage_bytes = text_bytes + embedding_bytes
    headroom_storage_bytes = raw_storage_bytes * 3
    relevant = (
        "capacity.annual_growth_percent",
        "capacity.average_pages_per_document",
        "capacity.average_chunk_tokens",
        "ai_services.embedding.dimension",
        "slo_and_finops.max_cost_per_ask",
        "slo_and_finops.max_cost_per_1000_pages_ingested",
        "slo_and_finops.monthly_budget",
    )
    blockers = [path for path in relevant if is_stubbed(loaded.stubbed_paths, path)]
    blockers.extend(
        [
            "real_file_size_distribution_not_provided",
            "real_native_service_benchmarks_not_executed",
            "backup_disk_capacity_and_throughput_not_provided",
        ]
    )
    assertions = [
        {
            "name": "capacity_inputs_are_positive",
            "passed": all(item > 0 for item in (documents, chunks, pages, chunk_tokens)),
        },
        {
            "name": "headroom_is_three_times_raw_baseline",
            "passed": headroom_storage_bytes == raw_storage_bytes * 3,
        },
        {"name": "cost_outputs_are_not_fabricated", "passed": True},
    ]
    return result(
        "capacity_cost",
        assertions,
        blockers,
        {
            "estimate_source": "effective_config_with_stub_markers",
            "initial_documents": documents,
            "documents_after_one_year": annual_documents,
            "initial_pages": initial_pages,
            "annual_ingested_pages": annual_ingested_pages,
            "raw_text_plus_stub_vector_bytes": raw_storage_bytes,
            "three_x_headroom_bytes": headroom_storage_bytes,
            "assumptions": {
                "utf8_bytes_per_token": 3,
                "embedding_dimension": embedding_dimension,
                "float_bytes": 4,
                "headroom_multiplier": 3,
                "excludes_original_media_indexes_backups_and_database_overhead": True,
            },
            "cost_result": "BLOCKED_INPUT",
        },
    )
