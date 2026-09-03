"""Explicitly approved Zilliz/Milvus provisioning orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pymilvus import MilvusClient

from ragkb.adapters.zilliz import ZillizCloudAdapter, required_zilliz_fields
from ragkb.adapters.zilliz_lifecycle_probe import (
    ZILLIZ_SAFE_WRITE_BATCH_SIZE,
    ZillizSyntheticCleanupFailed,
    ZillizSyntheticEntityNotVisible,
    ZillizSyntheticLifecycleError,
    run_synthetic_lifecycle,
    synthetic_records,
)
from ragkb.adapters.zilliz_readiness import (
    ZillizCollectionNotReady,
    request_collection_load_if_needed,
    wait_for_collection_ready,
)
from ragkb.adapters.zilliz_schema import (
    ZillizCollectionCapacityError,
    ZillizSchemaConflict,
    build_sdk_schema,
    database_creation_required,
    database_switch_required,
)
from ragkb.application.search import rrf_fuse
from ragkb.config import EnvSettings
from ragkb.domain.retrieval import SearchContext
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan

CREATE_APPROVAL = "ZILLIZ_COLLECTION_CREATE_APPROVED"

__all__ = [
    "CREATE_APPROVAL",
    "ZILLIZ_SAFE_WRITE_BATCH_SIZE",
    "ZillizCollectionCapacityError",
    "ZillizCollectionNotReady",
    "ZillizSchemaConflict",
    "ZillizSyntheticCleanupFailed",
    "ZillizSyntheticEntityNotVisible",
    "ZillizSyntheticLifecycleError",
    "build_sdk_schema",
    "database_creation_required",
    "database_switch_required",
    "provision_and_validate",
    "request_collection_load_if_needed",
    "run_synthetic_lifecycle",
    "wait_for_collection_ready",
]


def provision_and_validate(
    settings: EnvSettings,
    *,
    approval: str,
    client_factory: Callable[..., Any] = MilvusClient,
) -> dict[str, Any]:
    if approval != CREATE_APPROVAL:
        raise PermissionError("ZILLIZ_COLLECTION_CREATE_APPROVAL_REQUIRED")
    adapter = ZillizCloudAdapter(settings, client_factory=client_factory)
    client = adapter.connect()
    operations: list[str] = ["connect"]
    databases = set(map(str, client.list_databases(timeout=settings.zilliz_cloud_timeout_seconds)))
    operations.append("list_databases_diagnostic")
    database_created = database_creation_required(settings, databases)
    if database_created:
        client.create_database(
            db_name=settings.zilliz_cloud_database,
            timeout=settings.zilliz_cloud_timeout_seconds,
        )
        operations.append("create_database")
    if database_switch_required(settings):
        client.use_database(db_name=settings.zilliz_cloud_database)
        operations.append("use_custom_database")
    else:
        operations.append("use_existing_default_session")
    collections = client.list_collections(timeout=settings.zilliz_cloud_timeout_seconds)
    operations.append("verify_database_session")
    collection_exists = bool(
        client.has_collection(
            collection_name=settings.zilliz_cloud_collection,
            timeout=settings.zilliz_cloud_timeout_seconds,
        )
    )
    collection_created = False
    if not collection_exists and len(collections) >= 5:
        raise ZillizCollectionCapacityError("ZILLIZ_COLLECTION_CAPACITY_REQUIRED")
    if collection_exists:
        inspection = adapter.read_only_inspect()
        if not inspection["schema_compatible"]:
            raise ZillizSchemaConflict("EXISTING_ZILLIZ_COLLECTION_SCHEMA_INCOMPATIBLE")
        operations.append("verify_existing_collection")
    else:
        schema, index_params = build_sdk_schema(client, settings)
        client.create_collection(
            collection_name=settings.zilliz_cloud_collection,
            schema=schema,
            index_params=index_params,
            consistency_level=settings.zilliz_cloud_consistency_level,
            timeout=settings.zilliz_cloud_timeout_seconds,
        )
        operations.append("create_collection_with_indexes")
        collection_created = True
    load_action = request_collection_load_if_needed(client, settings)
    operations.append(load_action)

    records, ids = synthetic_records(settings)
    inserted_ids = [str(record["zilliz_pk"]) for record in records]

    def validate_synthetic(confirmed_ids: tuple[str, ...]) -> dict[str, object]:
        context = SearchContext(
            tenant_id=ids["tenant"],
            space_ids=(ids["space"],),
            subject_scope_tokens=(ids["reader"],),
            clearance_level=2,
            as_of_epoch=int(time.time()),
            active_generation_id=ids["generation"],
            active_permission_revision=12,
            required_security_watermark=12,
        )
        search_adapter = ZillizCloudAdapter(
            settings,
            client_factory=lambda **kwargs: client,
            watermark_provider=lambda _: 12,
        )
        bm25 = search_adapter.search_bm25("设备 保修期", context, 10)
        dense_vector = [0.0] * settings.zilliz_cloud_dimension
        dense_vector[0] = 1.0
        dense = search_adapter.search_dense(dense_vector, context, 10)
        fused = rrf_fuse((bm25, dense), rrf_k=settings.retrieval_rrf_k)
        returned = {candidate.chunk_id for candidate in (*bm25, *dense)}
        validation: dict[str, object] = {
            "confirmed_id_count": len(confirmed_ids),
            "bm25_hit_count": len(bm25),
            "dense_hit_count": len(dense),
            "hybrid_fused_count": len(fused),
            "authorized_id_returned": ids["authorized"] in returned,
            "denied_id_filtered": ids["denied"] not in returned,
            "expired_id_filtered": ids["expired"] not in returned,
            "wrong_generation_filtered": ids["wrong_generation"] not in returned,
            "security_consistency": settings.zilliz_cloud_security_consistency_level,
            "watermark_ready": search_adapter.observed_security_watermark(context) >= 12,
        }
        required = (
            "authorized_id_returned",
            "denied_id_filtered",
            "expired_id_filtered",
            "wrong_generation_filtered",
            "watermark_ready",
        )
        if not all(validation[key] for key in required):
            raise RuntimeError("ZILLIZ_SYNTHETIC_VALIDATION_FAILED")
        return validation

    lifecycle = run_synthetic_lifecycle(
        client,
        settings,
        records,
        validate_synthetic,
        total_timeout_seconds=min(30.0, settings.zilliz_cloud_timeout_seconds),
        max_polls=20,
        poll_interval_seconds=1,
    )
    operations.extend(
        (
            "wait_collection_ready",
            "upsert_synthetic_batches",
            "strong_confirm_batch",
            "search_bm25",
            "search_dense",
            "application_rrf",
            "delete_confirmed_synthetic_only",
            "strong_confirm_cleanup",
        )
    )
    validation_result = lifecycle["validation"]
    if not isinstance(validation_result, dict):
        raise ValueError("synthetic validation result must be a mapping")
    plan = build_zilliz_collection_plan(settings)
    return {
        "status": "ZILLIZ_G2_SYNTHETIC_VALIDATION_PASSED",
        "database_name": settings.zilliz_cloud_database,
        "collection_name": settings.zilliz_cloud_collection,
        "database_created": database_created,
        "collection_created": collection_created,
        "schema_fingerprint": plan["schema_fingerprint"],
        "required_field_count": len(required_zilliz_fields(settings)),
        "synthetic_test_ids": inserted_ids,
        "synthetic_inserted_count": lifecycle["inserted_count"],
        "synthetic_cleanup_count": lifecycle["inserted_count"],
        "safe_batch_size": lifecycle["safe_batch_size"],
        "validation": {
            "readiness": lifecycle["readiness"],
            **validation_result,
            "cleanup_remaining_count": 0,
        },
        "operations": operations,
        "possible_costs": [
            "database/collection metadata",
            "index build and storage",
            "four synthetic upserts",
            "two searches",
            "four synthetic deletes",
        ],
        "endpoint_in_output": False,
        "token_in_output": False,
        "non_project_resource_modified": False,
        "drop_operation_performed": False,
    }
