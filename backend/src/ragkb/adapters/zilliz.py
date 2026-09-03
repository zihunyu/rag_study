"""Zilliz Cloud China connection contract backed by pymilvus.MilvusClient."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pymilvus import FunctionType, MilvusClient

from ragkb.config import EnvSettings
from ragkb.domain.retrieval import IndexCandidate, SearchContext

ZILLIZ_BASE_FIELDS = frozenset(
    {
        "zilliz_pk",
        "tenant_id",
        "space_id",
        "corpus_id",
        "document_id",
        "document_version_id",
        "chunk_id",
        "parent_chunk_id",
        "chunk_type",
        "language",
        "valid_from_epoch",
        "valid_to_epoch",
        "lifecycle_projection",
        "visibility",
        "acl_scope_tokens",
        "permission_revision",
        "classification_level",
        "authority_rank",
        "category_ids",
        "tag_ids",
        "product_ids",
        "applicable_versions",
        "region_codes",
        "retrieval_text",
        "index_generation_id",
        "analyzer_revision",
        "content_checksum",
    }
)
ZILLIZ_REQUIRED_FIELDS = ZILLIZ_BASE_FIELDS.union({"dense_vector", "sparse_vector"})


def required_zilliz_fields(settings: EnvSettings) -> frozenset[str]:
    return ZILLIZ_BASE_FIELDS.union(
        {settings.zilliz_cloud_dense_field, settings.zilliz_cloud_sparse_field}
    )


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_zilliz_filter(context: SearchContext) -> str:
    spaces = ", ".join(_quoted(item) for item in context.space_ids)
    subject_tokens = ", ".join(_quoted(item) for item in context.subject_scope_tokens)
    acl = f"ARRAY_CONTAINS_ANY(acl_scope_tokens, [{subject_tokens}])" if subject_tokens else "false"
    return " and ".join(
        (
            f"tenant_id == {_quoted(context.tenant_id)}",
            f"space_id in [{spaces}]",
            f"index_generation_id == {_quoted(context.active_generation_id)}",
            'lifecycle_projection == "SERVING"',
            f"classification_level <= {context.clearance_level}",
            f"permission_revision <= {context.active_permission_revision}",
            f"valid_from_epoch <= {context.as_of_epoch}",
            f"(valid_to_epoch == 0 or valid_to_epoch > {context.as_of_epoch})",
            f'(visibility == "TENANT" or {acl})',
        )
    )


class ZillizSafeProjectionWriter:
    """Compatibility writer fixed to one entity per SDK insert call."""

    revision = "zilliz-safe-writer:g2-v1"
    safe_batch_size = 1

    def __init__(self, client: Any, settings: EnvSettings) -> None:
        self._client = client
        self._settings = settings

    def insert_records(self, records: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        inserted: list[str] = []
        for record in records:
            primary_key = str(record["zilliz_pk"])
            self._client.insert(
                collection_name=self._settings.zilliz_cloud_collection,
                data=[dict(record)],
                timeout=self._settings.zilliz_cloud_timeout_seconds,
            )
            inserted.append(primary_key)
        return tuple(inserted)


class ZillizCloudAdapter:
    revision = "zilliz-cloud-pymilvus:g2-v1"

    def __init__(
        self,
        settings: EnvSettings,
        *,
        client_factory: Callable[..., Any] = MilvusClient,
        watermark_provider: Callable[[SearchContext], int] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: Any | None = None
        self._real_connection_attempted = False
        self._watermark_provider = watermark_provider

    def connect(self) -> Any:
        token = self._settings.zilliz_cloud_token
        self._real_connection_attempted = True
        self._client = self._client_factory(
            uri=self._settings.zilliz_cloud_uri,
            token=token.get_secret_value() if token is not None else "",
            db_name=self._settings.zilliz_cloud_database,
            timeout=self._settings.zilliz_cloud_timeout_seconds,
        )
        return self._client

    def _connected(self) -> Any:
        return self._client if self._client is not None else self.connect()

    def read_only_inspect(self) -> dict[str, object]:
        client = self._connected()
        required_fields = required_zilliz_fields(self._settings)
        databases = client.list_databases(timeout=self._settings.zilliz_cloud_timeout_seconds)
        database_list_contains_name = self._settings.zilliz_cloud_database in set(
            map(str, databases)
        )
        collections = client.list_collections(timeout=self._settings.zilliz_cloud_timeout_seconds)
        session_usable = isinstance(collections, Sequence)
        collection_exists = bool(
            client.has_collection(
                collection_name=self._settings.zilliz_cloud_collection,
                timeout=self._settings.zilliz_cloud_timeout_seconds,
            )
        )
        if not collection_exists:
            return {
                "inspection_mode": "read_only",
                "database_exists": True,
                "database_session_usable": session_usable,
                "database_list_contains_configured_name": database_list_contains_name,
                "database_list_entry_count": len(databases),
                "collection_count": len(collections),
                "capacity_available_under_last_observed_limit": len(collections) < 5,
                "collection_exists": False,
                "schema_compatible": False,
                "missing_fields": sorted(required_fields),
                "zilliz_collection_create_approval_required": True,
                "mutating_call_performed": False,
            }
        description = client.describe_collection(
            collection_name=self._settings.zilliz_cloud_collection,
            timeout=self._settings.zilliz_cloud_timeout_seconds,
        )
        fields = description.get("fields", []) if isinstance(description, Mapping) else []
        by_name = {str(field.get("name")): field for field in fields if isinstance(field, Mapping)}
        missing = sorted(required_fields.difference(by_name))
        dense = by_name.get(self._settings.zilliz_cloud_dense_field, {})
        dense_params = dense.get("params", {}) if isinstance(dense, Mapping) else {}
        dimension = int(dense_params.get("dim", 0)) if isinstance(dense_params, Mapping) else 0
        text_field = by_name.get("retrieval_text", {})
        analyzer_enabled = bool(
            isinstance(text_field, Mapping)
            and (
                text_field.get("enable_analyzer")
                or (
                    isinstance(text_field.get("params"), Mapping)
                    and text_field["params"].get("enable_analyzer")
                )
            )
        )
        functions = description.get("functions", []) if isinstance(description, Mapping) else []
        bm25_function = False
        for function in functions:
            if not isinstance(function, Mapping):
                continue
            function_type = function.get("type", function.get("function_type", ""))
            if function_type == FunctionType.BM25.value or "BM25" in str(function_type).upper():
                bm25_function = True
                break
        compatible = (
            not missing
            and dimension == self._settings.zilliz_cloud_dimension
            and analyzer_enabled
            and bm25_function
        )
        return {
            "inspection_mode": "read_only",
            "database_exists": True,
            "database_session_usable": session_usable,
            "database_list_contains_configured_name": database_list_contains_name,
            "database_list_entry_count": len(databases),
            "collection_count": len(collections),
            "capacity_available_under_last_observed_limit": True,
            "collection_exists": True,
            "schema_compatible": compatible,
            "missing_fields": missing,
            "dense_dimension_matches": dimension == self._settings.zilliz_cloud_dimension,
            "analyzer_enabled": analyzer_enabled,
            "bm25_function_present": bm25_function,
            "zilliz_collection_create_approval_required": not compatible,
            "mutating_call_performed": False,
        }

    @staticmethod
    def _candidates(results: Any, channel: str) -> tuple[IndexCandidate, ...]:
        first = results[0] if isinstance(results, Sequence) and results else []
        candidates: list[IndexCandidate] = []
        for rank, hit in enumerate(first, start=1):
            entity = hit.get("entity", {}) if isinstance(hit, Mapping) else {}
            if not isinstance(entity, Mapping) or "chunk_id" not in entity:
                continue
            candidates.append(
                IndexCandidate(
                    chunk_id=str(entity["chunk_id"]),
                    document_version_id=str(entity["document_version_id"]),
                    parent_chunk_id=(
                        str(entity["parent_chunk_id"]) if entity.get("parent_chunk_id") else None
                    ),
                    channel="bm25" if channel == "bm25" else "dense",
                    rank=rank,
                    score=float(hit.get("distance", 0.0)),
                )
            )
        return tuple(candidates)

    def observed_security_watermark(self, context: SearchContext) -> int:
        return self._watermark_provider(context) if self._watermark_provider else 0

    def search_bm25(
        self, query: str, context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        results = self._connected().search(
            collection_name=self._settings.zilliz_cloud_collection,
            data=[query],
            anns_field=self._settings.zilliz_cloud_sparse_field,
            filter=build_zilliz_filter(context),
            limit=limit,
            output_fields=["chunk_id", "document_version_id", "parent_chunk_id"],
            consistency_level=self._settings.zilliz_cloud_security_consistency_level,
        )
        return self._candidates(results, "bm25")

    def search_dense(
        self, vector: Sequence[float], context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        results = self._connected().search(
            collection_name=self._settings.zilliz_cloud_collection,
            data=[list(vector)],
            anns_field=self._settings.zilliz_cloud_dense_field,
            filter=build_zilliz_filter(context),
            limit=limit,
            output_fields=["chunk_id", "document_version_id", "parent_chunk_id"],
            search_params={"metric_type": self._settings.zilliz_cloud_metric_type},
            consistency_level=self._settings.zilliz_cloud_security_consistency_level,
        )
        return self._candidates(results, "dense")

    def safe_status(self) -> dict[str, object]:
        return {
            "adapter": self.revision,
            "database_configured": bool(self._settings.zilliz_cloud_database),
            "collection_configured": bool(self._settings.zilliz_cloud_collection),
            "bm25_enabled": self._settings.zilliz_cloud_enable_bm25,
            "security_consistency": self._settings.zilliz_cloud_security_consistency_level,
            "token_in_status": False,
            "real_connection_attempted": self._real_connection_attempted,
            "mutating_call_performed": False,
        }
