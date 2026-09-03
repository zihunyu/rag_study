from __future__ import annotations

from pathlib import Path

from ragkb.adapters.zilliz import required_zilliz_fields
from ragkb.adapters.zilliz_provision import database_creation_required, database_switch_required
from ragkb.config import load_env
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan


def test_zilliz_plan_is_exact_non_executing_and_matches_search_contract() -> None:
    loaded = load_env(Path(__file__).resolve().parents[2])
    assert loaded.settings is not None

    plan = build_zilliz_collection_plan(loaded.settings)
    schema = plan["schema"]
    fields = {field["name"]: field for field in schema["fields"]}
    indexes = {index["field"]: index for index in schema["indexes"]}

    assert set(fields) == required_zilliz_fields(loaded.settings)
    assert fields["dense_vector"]["dimension"] == loaded.settings.zilliz_cloud_dimension
    assert fields["retrieval_text"]["enable_analyzer"] is True
    assert schema["functions"] == [
        {
            "name": "retrieval_text_bm25",
            "type": "BM25",
            "input_fields": ["retrieval_text"],
            "output_fields": ["sparse_vector"],
        }
    ]
    assert indexes["dense_vector"]["index_type"] == "HNSW"
    assert indexes["sparse_vector"]["metric_type"] == "BM25"
    assert plan["execution"]["mutating_call_performed"] is False
    assert plan["execution"]["approval_required"] == ("ZILLIZ_COLLECTION_CREATE_APPROVAL_REQUIRED")
    assert plan["execution"]["database_create_policy"] == "forbidden_for_default"
    assert not any(
        "create_database" in operation for operation in plan["execution"]["allowed_after_approval"]
    )
    assert database_creation_required(loaded.settings, {"db_internal_opaque_id"}) is False
    assert database_switch_required(loaded.settings) is False
    assert len(plan["schema_fingerprint"]) == 64
