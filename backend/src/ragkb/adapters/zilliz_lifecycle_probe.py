"""Synthetic security/search lifecycle probe with guaranteed cleanup."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from ragkb.adapters.vector_indexing import ZillizSafeProjectionWriter
from ragkb.adapters.zilliz_readiness import ZillizCollectionNotReady, wait_for_collection_ready
from ragkb.config import EnvSettings

ZILLIZ_SAFE_WRITE_BATCH_SIZE = 256


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
        stage = "upsert_synthetic_batches"
        inserted_ids = ZillizSafeProjectionWriter(client, settings).insert_records(records)
        stage = "confirm_synthetic_batch"
        visible = client.get(
            collection_name=settings.zilliz_cloud_collection,
            ids=list(inserted_ids),
            output_fields=["zilliz_pk"],
            timeout=settings.zilliz_cloud_timeout_seconds,
            consistency_level=settings.zilliz_cloud_security_consistency_level,
        )
        visible_ids = {str(item.get("zilliz_pk", "")) for item in visible}
        if not set(inserted_ids).issubset(visible_ids):
            raise ZillizSyntheticEntityNotVisible(
                "ZILLIZ_SYNTHETIC_ENTITY_NOT_VISIBLE_AFTER_UPSERT"
            )
        confirmed_ids.extend(inserted_ids)
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


def synthetic_records(settings: EnvSettings) -> tuple[list[dict[str, Any]], dict[str, str]]:
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
            "current_version": True,
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
