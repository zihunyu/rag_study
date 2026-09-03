"""Explicitly approved Zilliz G2 provisioning and synthetic validation."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from pymilvus import DataType, Function, FunctionType, MilvusClient

from ragkb.adapters.zilliz import ZillizCloudAdapter, required_zilliz_fields
from ragkb.application.search import rrf_fuse
from ragkb.config import EnvSettings
from ragkb.domain.retrieval import SearchContext
from ragkb.infrastructure.zilliz_plan import build_zilliz_collection_plan

CREATE_APPROVAL = "ZILLIZ_COLLECTION_CREATE_APPROVED"
ZILLIZ_SAFE_WRITE_BATCH_SIZE = 1


class ZillizSchemaConflict(RuntimeError):
    pass


class ZillizCollectionCapacityError(RuntimeError):
    pass


class ZillizCollectionNotReady(RuntimeError):
    pass


class ZillizSyntheticEntityNotVisible(RuntimeError):
    pass


class ZillizSyntheticCleanupFailed(RuntimeError):
    pass


class ZillizSyntheticLifecycleError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        error_type: str,
        confirmed_count: int,
        cleaned_count: int,
        remaining_count: int,
        error_code: str,
    ) -> None:
        super().__init__("ZILLIZ_SYNTHETIC_LIFECYCLE_FAILED")
        self.stage = stage
        self.error_type = error_type
        self.confirmed_count = confirmed_count
        self.cleaned_count = cleaned_count
        self.remaining_count = remaining_count
        self.error_code = error_code


def database_creation_required(settings: EnvSettings, listed_databases: set[str]) -> bool:
    return (
        settings.zilliz_cloud_database.casefold() != "default"
        and settings.zilliz_cloud_database not in listed_databases
    )


def database_switch_required(settings: EnvSettings) -> bool:
    return settings.zilliz_cloud_database.casefold() != "default"


def _datatype(name: str) -> DataType:
    return {
        "VARCHAR": DataType.VARCHAR,
        "ARRAY": DataType.ARRAY,
        "INT8": DataType.INT8,
        "INT32": DataType.INT32,
        "INT64": DataType.INT64,
        "FLOAT_VECTOR": DataType.FLOAT_VECTOR,
        "SPARSE_FLOAT_VECTOR": DataType.SPARSE_FLOAT_VECTOR,
    }[name]


def build_sdk_schema(client: Any, settings: EnvSettings) -> tuple[Any, Any]:
    plan = build_zilliz_collection_plan(settings)
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    for field in plan["schema"]["fields"]:
        field_name = str(field["name"])
        field_type = str(field["type"])
        kwargs = {
            key: value
            for key, value in field.items()
            if key not in {"name", "type", "primary", "dimension"}
        }
        if field.get("primary"):
            kwargs["is_primary"] = True
            kwargs["auto_id"] = False
        if field_type == "FLOAT_VECTOR":
            kwargs["dim"] = int(field["dimension"])
        if field_type == "ARRAY":
            kwargs["element_type"] = _datatype(str(field["element_type"]))
        schema.add_field(field_name=field_name, datatype=_datatype(field_type), **kwargs)
    schema.add_function(
        Function(
            name="retrieval_text_bm25",
            function_type=FunctionType.BM25,
            input_field_names=["retrieval_text"],
            output_field_names=[settings.zilliz_cloud_sparse_field],
        )
    )
    index_params = client.prepare_index_params()
    for index in plan["schema"]["indexes"]:
        kwargs = {
            key: value
            for key, value in index.items()
            if key not in {"field", "index_type", "index_name"}
        }
        index_params.add_index(
            field_name=str(index["field"]),
            index_type=str(index["index_type"]),
            index_name=str(index["index_name"]),
            **kwargs,
        )
    return schema, index_params


def wait_for_collection_ready(
    client: Any,
    settings: EnvSettings,
    *,
    total_timeout_seconds: float = 30,
    max_polls: int = 20,
    poll_interval_seconds: float = 1,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    if total_timeout_seconds <= 0 or max_polls < 1 or poll_interval_seconds < 0:
        raise ValueError("readiness timeout, polls and interval are invalid")
    expected_indexes = {
        str(index["index_name"])
        for index in build_zilliz_collection_plan(settings)["schema"]["indexes"]
    }
    deadline = monotonic() + total_timeout_seconds
    last_loaded = False
    last_index_count = 0
    for poll in range(1, max_polls + 1):
        load_state = client.get_load_state(
            collection_name=settings.zilliz_cloud_collection,
            timeout=settings.zilliz_cloud_timeout_seconds,
        )
        state = str(load_state.get("state", load_state.get("load_state", "")))
        last_loaded = state.casefold().endswith("loaded")
        indexes = set(
            map(
                str,
                client.list_indexes(collection_name=settings.zilliz_cloud_collection),
            )
        )
        last_index_count = len(indexes)
        if last_loaded and expected_indexes.issubset(indexes):
            return {
                "ready": True,
                "poll_count": poll,
                "loaded": True,
                "index_count": len(indexes),
                "expected_index_count": len(expected_indexes),
                "mutating_call_performed": False,
            }
        now = monotonic()
        if poll >= max_polls or now >= deadline:
            break
        sleep(min(poll_interval_seconds, max(0.0, deadline - now)))
    raise ZillizCollectionNotReady(
        f"ZILLIZ_COLLECTION_NOT_READY:loaded={last_loaded}:indexes={last_index_count}"
    )


def request_collection_load_if_needed(client: Any, settings: EnvSettings) -> str:
    load_state = client.get_load_state(
        collection_name=settings.zilliz_cloud_collection,
        timeout=settings.zilliz_cloud_timeout_seconds,
    )
    state = str(load_state.get("state", load_state.get("load_state", "")))
    if state.casefold().endswith("loaded"):
        return "already_loaded"
    try:
        client.load(
            collection_name=settings.zilliz_cloud_collection,
            timeout=settings.zilliz_cloud_timeout_seconds,
        )
    except AttributeError:
        return "load_return_attribute_error_requires_readiness_confirmation"
    return "load_requested"


def run_synthetic_lifecycle(
    client: Any,
    settings: EnvSettings,
    records: list[dict[str, Any]],
    validate: Callable[[tuple[str, ...]], dict[str, object]],
    **readiness_options: Any,
) -> dict[str, object]:
    confirmed_ids: list[str] = []
    cleaned_count = 0
    remaining_count = 0
    stage = "wait_collection_ready"
    primary_error: Exception | None = None
    validation: dict[str, object] | None = None
    readiness: dict[str, object] | None = None
    try:
        readiness = wait_for_collection_ready(client, settings, **readiness_options)
        for index, record in enumerate(records, start=1):
            primary_key = str(record["zilliz_pk"])
            stage = f"insert_synthetic_{index}"
            client.insert(
                collection_name=settings.zilliz_cloud_collection,
                data=[record],
                timeout=settings.zilliz_cloud_timeout_seconds,
            )
            stage = f"confirm_synthetic_{index}"
            visible = client.get(
                collection_name=settings.zilliz_cloud_collection,
                ids=[primary_key],
                output_fields=["zilliz_pk"],
                timeout=settings.zilliz_cloud_timeout_seconds,
                consistency_level=settings.zilliz_cloud_security_consistency_level,
            )
            if not any(str(item.get("zilliz_pk", "")) == primary_key for item in visible):
                raise ZillizSyntheticEntityNotVisible(
                    "ZILLIZ_SYNTHETIC_ENTITY_NOT_VISIBLE_AFTER_INSERT"
                )
            confirmed_ids.append(primary_key)
        stage = "validate_search"
        validation = validate(tuple(confirmed_ids))
    except Exception as error:
        primary_error = error
    finally:
        if confirmed_ids:
            try:
                client.delete(
                    collection_name=settings.zilliz_cloud_collection,
                    ids=confirmed_ids,
                    timeout=settings.zilliz_cloud_timeout_seconds,
                )
                cleaned_count = len(confirmed_ids)
                remaining = client.get(
                    collection_name=settings.zilliz_cloud_collection,
                    ids=confirmed_ids,
                    output_fields=["zilliz_pk"],
                    timeout=settings.zilliz_cloud_timeout_seconds,
                    consistency_level=settings.zilliz_cloud_security_consistency_level,
                )
                remaining_count = len(remaining)
                if remaining_count:
                    raise ZillizSyntheticCleanupFailed("ZILLIZ_SYNTHETIC_CLEANUP_NOT_CONFIRMED")
            except Exception as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                    stage = "cleanup_confirmed_synthetic"
    if primary_error is not None:
        error_code = (
            "ZILLIZ_COLLECTION_NOT_READY"
            if isinstance(primary_error, ZillizCollectionNotReady)
            else "ZILLIZ_SYNTHETIC_LIFECYCLE_FAILED"
        )
        raise ZillizSyntheticLifecycleError(
            stage=stage,
            error_type=type(primary_error).__name__,
            confirmed_count=len(confirmed_ids),
            cleaned_count=cleaned_count,
            remaining_count=remaining_count,
            error_code=error_code,
        ) from primary_error
    if readiness is None or validation is None:
        raise RuntimeError("synthetic lifecycle completed without evidence")
    return {
        "readiness": readiness,
        "validation": validation,
        "confirmed_ids": tuple(confirmed_ids),
        "inserted_count": len(confirmed_ids),
        "cleaned_count": cleaned_count,
        "remaining_count": remaining_count,
        "safe_batch_size": ZILLIZ_SAFE_WRITE_BATCH_SIZE,
    }


def _synthetic_records(settings: EnvSettings) -> tuple[list[dict[str, Any]], dict[str, str]]:
    marker = f"__ragkb_g2_auto__{uuid.uuid4().hex[:12]}"
    ids = {
        "authorized": f"{marker}_authorized",
        "denied": f"{marker}_denied",
        "expired": f"{marker}_expired",
        "wrong_generation": f"{marker}_wrong_generation",
    }
    now = int(time.time())
    dimension = settings.zilliz_cloud_dimension

    def vector(position: int) -> list[float]:
        values = [0.0] * dimension
        values[position % dimension] = 1.0
        return values

    def record(
        key: str,
        text: str,
        *,
        acl: list[str],
        generation: str = f"{marker}_generation",
        valid_to: int = 0,
        vector_position: int,
    ) -> dict[str, Any]:
        entity_id = ids[key]
        return {
            "zilliz_pk": entity_id,
            "tenant_id": f"{marker}_tenant",
            "space_id": f"{marker}_space",
            "corpus_id": f"{marker}_corpus",
            "document_id": f"{entity_id}_document",
            "document_version_id": f"{entity_id}_version",
            "chunk_id": entity_id,
            "parent_chunk_id": "",
            "chunk_type": "paragraph",
            "language": "zh",
            "valid_from_epoch": now - 60,
            "valid_to_epoch": valid_to,
            "lifecycle_projection": "SERVING",
            "visibility": "RESTRICTED",
            "acl_scope_tokens": acl,
            "permission_revision": 12,
            "classification_level": 1,
            "authority_rank": 1,
            "category_ids": [f"{marker}_category"],
            "tag_ids": ["automated-test"],
            "product_ids": [f"{marker}_product"],
            "applicable_versions": ["test-v1"],
            "region_codes": ["test-region"],
            "retrieval_text": text,
            settings.zilliz_cloud_dense_field: vector(vector_position),
            "index_generation_id": generation,
            "analyzer_revision": settings.zilliz_cloud_bm25_analyzer,
            "content_checksum": uuid.uuid5(uuid.NAMESPACE_URL, entity_id).hex * 2,
        }

    records = [
        record(
            "authorized",
            "自动化测试设备保修期为三年",
            acl=[f"{marker}_reader"],
            vector_position=0,
        ),
        record(
            "denied",
            "自动化测试机密设备保修期为十年",
            acl=[f"{marker}_secret"],
            vector_position=1,
        ),
        record(
            "expired",
            "自动化测试过期设备保修期为一年",
            acl=[f"{marker}_reader"],
            valid_to=now - 1,
            vector_position=2,
        ),
        record(
            "wrong_generation",
            "自动化测试旧代际设备保修期为五年",
            acl=[f"{marker}_reader"],
            generation=f"{marker}_old_generation",
            vector_position=3,
        ),
    ]
    return records, {
        **ids,
        "tenant": f"{marker}_tenant",
        "space": f"{marker}_space",
        "generation": f"{marker}_generation",
        "reader": f"{marker}_reader",
    }


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

    records, ids = _synthetic_records(settings)
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
        if not all(
            validation[key]
            for key in (
                "authorized_id_returned",
                "denied_id_filtered",
                "expired_id_filtered",
                "wrong_generation_filtered",
                "watermark_ready",
            )
        ):
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
            "insert_synthetic_one_by_one",
            "strong_confirm_each_insert",
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
            "four synthetic inserts",
            "two searches",
            "four synthetic deletes",
        ],
        "endpoint_in_output": False,
        "token_in_output": False,
        "non_project_resource_modified": False,
        "drop_operation_performed": False,
    }
