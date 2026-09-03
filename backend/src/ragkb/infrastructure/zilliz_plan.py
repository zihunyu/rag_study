"""Exact, non-executing Zilliz collection plan for G2 approval."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ragkb.config import EnvSettings

ZILLIZ_PLAN_REVISION = "zilliz-collection-plan:g2-v1"


def _varchar(name: str, max_length: int = 128) -> dict[str, object]:
    return {"name": name, "type": "VARCHAR", "max_length": max_length}


def _string_array(name: str, *, max_capacity: int = 4096) -> dict[str, object]:
    return {
        "name": name,
        "type": "ARRAY",
        "element_type": "VARCHAR",
        "max_capacity": max_capacity,
        "max_length": 256,
    }


def build_zilliz_collection_plan(settings: EnvSettings) -> dict[str, Any]:
    fields: list[dict[str, object]] = [
        {**_varchar("zilliz_pk", 255), "primary": True, "auto_id": False},
        _varchar("tenant_id"),
        _varchar("space_id"),
        _varchar("corpus_id"),
        _varchar("document_id"),
        _varchar("document_version_id"),
        _varchar("chunk_id"),
        _varchar("parent_chunk_id"),
        _varchar("chunk_type", 32),
        _varchar("language", 32),
        {"name": "valid_from_epoch", "type": "INT64"},
        {"name": "valid_to_epoch", "type": "INT64"},
        _varchar("lifecycle_projection", 32),
        _varchar("visibility", 32),
        _string_array("acl_scope_tokens"),
        {"name": "permission_revision", "type": "INT64"},
        {"name": "classification_level", "type": "INT8"},
        {"name": "authority_rank", "type": "INT32"},
        _string_array("category_ids", max_capacity=256),
        _string_array("tag_ids", max_capacity=1024),
        _string_array("product_ids", max_capacity=1024),
        _string_array("applicable_versions", max_capacity=1024),
        _string_array("region_codes", max_capacity=256),
        {
            "name": "retrieval_text",
            "type": "VARCHAR",
            "max_length": 65535,
            "enable_analyzer": True,
            "analyzer_params": {"type": settings.zilliz_cloud_bm25_analyzer},
        },
        {
            "name": settings.zilliz_cloud_dense_field,
            "type": "FLOAT_VECTOR",
            "dimension": settings.zilliz_cloud_dimension,
        },
        {"name": settings.zilliz_cloud_sparse_field, "type": "SPARSE_FLOAT_VECTOR"},
        _varchar("index_generation_id"),
        _varchar("analyzer_revision"),
        _varchar("content_checksum", 64),
    ]
    indexes = [
        {
            "field": settings.zilliz_cloud_dense_field,
            "index_name": "idx_dense_hnsw",
            "index_type": "HNSW",
            "metric_type": settings.zilliz_cloud_metric_type,
            "params": {"M": 16, "efConstruction": 200},
        },
        {
            "field": settings.zilliz_cloud_sparse_field,
            "index_name": "idx_sparse_bm25",
            "index_type": "SPARSE_INVERTED_INDEX",
            "metric_type": "BM25",
            "params": {"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
        },
    ]
    for field in (
        "tenant_id",
        "space_id",
        "index_generation_id",
        "lifecycle_projection",
        "visibility",
        "classification_level",
        "permission_revision",
        "valid_from_epoch",
        "valid_to_epoch",
    ):
        indexes.append(
            {"field": field, "index_name": f"idx_scalar_{field}", "index_type": "AUTOINDEX"}
        )
    schema = {
        "dynamic_fields": False,
        "fields": fields,
        "functions": [
            {
                "name": "retrieval_text_bm25",
                "type": "BM25",
                "input_fields": ["retrieval_text"],
                "output_fields": [settings.zilliz_cloud_sparse_field],
            }
        ],
        "indexes": indexes,
        "consistency": {
            "default": settings.zilliz_cloud_consistency_level,
            "security_reads": settings.zilliz_cloud_security_consistency_level,
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    database_is_default = settings.zilliz_cloud_database.casefold() == "default"
    approved_operations = ["create_collection", "create_indexes", "load"]
    if not database_is_default:
        approved_operations.insert(0, "create_database_if_custom_and_missing")
    return {
        "plan_revision": ZILLIZ_PLAN_REVISION,
        "database_name_from": "ZILLIZ_CLOUD_DATABASE",
        "collection_name_from": "ZILLIZ_CLOUD_COLLECTION",
        "schema": schema,
        "schema_fingerprint": fingerprint,
        "execution": {
            "approval_required": "ZILLIZ_COLLECTION_CREATE_APPROVAL_REQUIRED",
            "database_create_policy": (
                "forbidden_for_default" if database_is_default else "custom_database_only"
            ),
            "allowed_after_approval": approved_operations,
            "forbidden_without_separate_data_write_approval": [
                "insert",
                "upsert",
                "delete",
                "drop_database",
                "drop_collection",
            ],
            "mutating_call_performed": False,
        },
    }
