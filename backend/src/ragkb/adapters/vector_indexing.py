"""Bounded Milvus/Zilliz writes and chunk projection orchestration."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from pymilvus.exceptions import MilvusException

from ragkb.config import EnvSettings
from ragkb.contracts.ports import EmbeddingPort
from ragkb.document_processing.chunking import ChunkingResult
from ragkb.domain.retrieval import AuthorizedChunk


def vector_collection_name(settings: EnvSettings) -> str:
    return (
        settings.vector_collection
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_collection
    )


class ConnectedVectorAdapter(Protocol):
    def _connected(self) -> Any: ...


class ZillizSafeProjectionWriter:
    """Bounded, idempotent projection batches with retryable failure context."""

    revision = "zilliz-batch-writer:g2-v2"

    def __init__(
        self,
        client: Any,
        settings: EnvSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._settings = settings
        self._sleep = sleep
        self.safe_batch_size = settings.zilliz_write_batch_size

    @staticmethod
    def _record_size(record: Mapping[str, Any]) -> int:
        return len(
            json.dumps(dict(record), ensure_ascii=False, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        )

    def _batches(
        self, records: Sequence[Mapping[str, Any]]
    ) -> tuple[tuple[Mapping[str, Any], ...], ...]:
        batches: list[tuple[Mapping[str, Any], ...]] = []
        current: list[Mapping[str, Any]] = []
        current_bytes = 0
        for record in records:
            record_bytes = self._record_size(record)
            if record_bytes > self._settings.zilliz_write_max_bytes:
                raise ValueError("ZILLIZ_RECORD_EXCEEDS_WRITE_MAX_BYTES")
            if current and (
                len(current) >= self._settings.zilliz_write_batch_size
                or current_bytes + record_bytes > self._settings.zilliz_write_max_bytes
            ):
                batches.append(tuple(current))
                current = []
                current_bytes = 0
            current.append(record)
            current_bytes += record_bytes
        if current:
            batches.append(tuple(current))
        return tuple(batches)

    def _insert_batch(self, batch: Sequence[Mapping[str, Any]], batch_number: int) -> None:
        for attempt in range(self._settings.zilliz_write_max_retries + 1):
            try:
                response = self._client.insert(
                    collection_name=vector_collection_name(self._settings),
                    data=[dict(record) for record in batch],
                    timeout=self._settings.zilliz_cloud_timeout_seconds,
                )
                if isinstance(response, Mapping):
                    inserted = response.get("insert_count")
                    if inserted is not None and int(str(inserted)) != len(batch):
                        raise ValueError("ZILLIZ_BATCH_INSERT_COUNT_MISMATCH")
                return
            except (MilvusException, TimeoutError, ConnectionError) as error:
                if attempt >= self._settings.zilliz_write_max_retries:
                    chunk_ids = tuple(str(record["zilliz_pk"]) for record in batch)
                    raise RuntimeError(
                        f"ZILLIZ_BATCH_INSERT_FAILED:batch={batch_number}:count={len(batch)}:"
                        f"first_pk={chunk_ids[0]}"
                    ) from error
                self._sleep(self._settings.model_http_backoff_seconds * (2**attempt))

    def insert_records(self, records: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        inserted: list[str] = []
        for batch_number, batch in enumerate(self._batches(records), start=1):
            self._insert_batch(batch, batch_number)
            inserted.extend(str(record["zilliz_pk"]) for record in batch)
        return tuple(inserted)


class ZillizChunkIndexingSink:
    """Embed chunks, batch them to a vector DB, and persist display projections."""

    revision = "zilliz-chunk-indexing-sink:v1"

    def __init__(
        self,
        adapter: ConnectedVectorAdapter,
        control_plane: Any,
        embedding: EmbeddingPort,
        settings: EnvSettings,
        *,
        generation_id: str,
    ) -> None:
        self.adapter = adapter
        self.control_plane = control_plane
        self.embedding = embedding
        self.settings = settings
        self.generation_id = generation_id

    def index(
        self,
        result: ChunkingResult,
        *,
        document_id: str,
        tenant_id: str,
        space_id: str,
        permission_revision: int = 1,
    ) -> None:
        vectors: list[Sequence[float]] = []
        for start in range(0, len(result.chunks), self.settings.embedding_batch_size):
            batch = result.chunks[start : start + self.settings.embedding_batch_size]
            vectors.extend(self.embedding.embed([item.retrieval_text for item in batch]))
        if len(vectors) != len(result.chunks):
            raise ValueError("ZILLIZ_INDEX_EMBEDDING_COUNT_MISMATCH")
        now = int(time.time())
        records: list[dict[str, Any]] = []
        for chunk, vector in zip(result.chunks, vectors, strict=True):
            records.append(
                {
                    "zilliz_pk": f"{tenant_id}:{self.generation_id}:{chunk.id}",
                    "tenant_id": tenant_id,
                    "space_id": space_id,
                    "corpus_id": space_id,
                    "document_id": document_id,
                    "document_version_id": chunk.version_id,
                    "chunk_id": chunk.id,
                    "parent_chunk_id": chunk.parent_chunk_id or "",
                    "chunk_type": chunk.kind,
                    "language": "und",
                    "valid_from_epoch": now,
                    "valid_to_epoch": 0,
                    "lifecycle_projection": "SERVING",
                    "visibility": "TENANT",
                    "acl_scope_tokens": [],
                    "permission_revision": permission_revision,
                    "classification_level": 0,
                    "authority_rank": 1,
                    "category_ids": [],
                    "tag_ids": [],
                    "product_ids": [],
                    "applicable_versions": [],
                    "region_codes": [],
                    "retrieval_text": chunk.retrieval_text,
                    self.settings.zilliz_cloud_dense_field: list(vector),
                    "index_generation_id": self.generation_id,
                    "analyzer_revision": self.settings.zilliz_cloud_bm25_analyzer,
                    "content_checksum": chunk.content_sha256,
                }
            )
        ZillizSafeProjectionWriter(self.adapter._connected(), self.settings).insert_records(records)
        put_projection = getattr(self.control_plane, "put_for_test", None)
        if not callable(put_projection):
            raise TypeError("production retrieval projection sink is not writable")
        for chunk in (*result.parent_chunks, *result.chunks):
            put_projection(
                AuthorizedChunk(
                    chunk_id=chunk.id,
                    tenant_id=tenant_id,
                    space_id=space_id,
                    document_id=document_id,
                    document_version_id=chunk.version_id,
                    parent_chunk_id=chunk.parent_chunk_id,
                    display_text=chunk.display_text,
                    retrieval_text=chunk.retrieval_text,
                    locator=chunk.locator.to_dict(),
                    content_checksum=chunk.content_sha256,
                    visibility="TENANT",
                    acl_scope_tokens=(),
                    classification_level=0,
                    lifecycle_projection="SERVING",
                    valid_from_epoch=now,
                    valid_to_epoch=0,
                    permission_revision=permission_revision,
                    current_version=True,
                )
            )
