"""Bounded Milvus/Zilliz writes and chunk projection orchestration."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from pymilvus.exceptions import MilvusException

from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.config import EnvSettings
from ragkb.contracts.ports import EmbeddingPort, RetrievalProjectionPort
from ragkb.document_processing.chunking import ChunkingResult
from ragkb.domain.errors import VectorBatchWriteError
from ragkb.domain.retrieval import AuthorizedChunk, SecurityProjection


def vector_collection_name(settings: EnvSettings) -> str:
    return (
        settings.vector_collection
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_collection
    )


def vector_dense_field(settings: EnvSettings) -> str:
    return (
        settings.vector_dense_field
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_dense_field
    )


def vector_sparse_field(settings: EnvSettings) -> str:
    return (
        settings.vector_sparse_field
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_sparse_field
    )


def vector_metric_type(settings: EnvSettings) -> str:
    return (
        settings.vector_metric_type
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_metric_type
    )


def vector_dimension(settings: EnvSettings) -> int:
    return (
        settings.vector_dimension
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_dimension
    )


def vector_analyzer(settings: EnvSettings) -> str:
    return (
        settings.vector_bm25_analyzer
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_bm25_analyzer
    )


def vector_timeout(settings: EnvSettings) -> float:
    return (
        settings.vector_timeout_seconds
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_timeout_seconds
    )


def vector_security_consistency(settings: EnvSettings) -> str:
    return (
        settings.vector_security_consistency_level
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_security_consistency_level
    )


class ConnectedVectorAdapter(Protocol):
    def _connected(self) -> Any: ...


class IndexSagaPort(Protocol):
    def begin(
        self,
        *,
        tenant_id: str,
        space_id: str,
        document_id: str,
        document_version_id: str,
        generation_id: str,
        records: Sequence[Mapping[str, object]],
    ) -> str: ...

    def confirm_batch(
        self,
        index_job_id: str,
        batch_number: int,
        records: Sequence[Mapping[str, object]],
        *,
        vector: bool,
        control: bool,
    ) -> None: ...

    def mark_ready(self, index_job_id: str) -> None: ...

    def fail(self, index_job_id: str, error_code: str) -> None: ...


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
                response = self._client.upsert(
                    collection_name=vector_collection_name(self._settings),
                    data=[dict(record) for record in batch],
                    timeout=vector_timeout(self._settings),
                )
                if isinstance(response, Mapping):
                    inserted = response.get("upsert_count", response.get("insert_count"))
                    if inserted is not None and int(str(inserted)) != len(batch):
                        raise ValueError("ZILLIZ_BATCH_INSERT_COUNT_MISMATCH")
                return
            except (MilvusException, TimeoutError, ConnectionError) as error:
                try:
                    primary_keys = [str(record["zilliz_pk"]) for record in batch]
                    confirmed = self._client.query(
                        collection_name=vector_collection_name(self._settings),
                        filter=(
                            "zilliz_pk in ["
                            + ",".join(json.dumps(key) for key in primary_keys)
                            + "]"
                        ),
                        output_fields=["zilliz_pk"],
                        timeout=vector_timeout(self._settings),
                    )
                    if {str(item["zilliz_pk"]) for item in confirmed} == set(primary_keys):
                        return
                except Exception:
                    confirmed = ()
                if attempt >= self._settings.zilliz_write_max_retries:
                    chunk_ids = tuple(str(record["chunk_id"]) for record in batch)
                    raise VectorBatchWriteError(
                        "VECTOR_BATCH_UPSERT_FAILED",
                        batch_number=batch_number,
                        chunk_ids=chunk_ids,
                    ) from error
                self._sleep(self._settings.model_http_backoff_seconds * (2**attempt))

    def insert_records(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        on_batch_confirmed: (Callable[[int, Sequence[Mapping[str, Any]]], None] | None) = None,
    ) -> tuple[str, ...]:
        inserted: list[str] = []
        for batch_number, batch in enumerate(self._batches(records), start=1):
            self._insert_batch(batch, batch_number)
            if on_batch_confirmed is not None:
                on_batch_confirmed(batch_number, batch)
            inserted.extend(str(record["zilliz_pk"]) for record in batch)
        return tuple(inserted)


class ZillizChunkIndexingSink:
    """Embed chunks, batch them to a vector DB, and persist display projections."""

    revision = "zilliz-chunk-indexing-sink:v1"

    def __init__(
        self,
        adapter: ConnectedVectorAdapter,
        control_plane: RetrievalProjectionPort,
        embedding: EmbeddingPort,
        settings: EnvSettings,
        *,
        generation_id: str,
        tracer: TracerPort | None = None,
        saga: IndexSagaPort | None = None,
    ) -> None:
        self.adapter = adapter
        self.control_plane = control_plane
        self.embedding = embedding
        self.settings = settings
        self.generation_id = generation_id
        self.tracer = tracer or InMemoryTracer()
        self.saga = saga

    def index(
        self,
        result: ChunkingResult,
        *,
        document_id: str,
        tenant_id: str,
        space_id: str,
        permission_revision: int = 1,
        security_projection: SecurityProjection | None = None,
    ) -> None:
        vectors: list[Sequence[float]] = []
        for start in range(0, len(result.chunks), self.settings.embedding_batch_size):
            batch = result.chunks[start : start + self.settings.embedding_batch_size]
            with self.tracer.span(
                "document.embedding.batch",
                {"batch_size": len(batch), "provider": self.settings.embedding_model},
            ):
                vectors.extend(self.embedding.embed([item.retrieval_text for item in batch]))
        if len(vectors) != len(result.chunks):
            raise ValueError("ZILLIZ_INDEX_EMBEDDING_COUNT_MISMATCH")
        now = int(time.time())
        security = security_projection or SecurityProjection.unapproved(
            permission_revision=permission_revision,
            now=now,
        )
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
                    "valid_from_epoch": security.valid_from_epoch,
                    "valid_to_epoch": security.valid_to_epoch,
                    "lifecycle_projection": security.lifecycle_projection,
                    "current_version": False,
                    "visibility": security.visibility,
                    "acl_scope_tokens": list(security.acl_scope_tokens),
                    "permission_revision": security.permission_revision,
                    "classification_level": security.classification_level,
                    "authority_rank": 1,
                    "category_ids": [],
                    "tag_ids": [],
                    "product_ids": [],
                    "applicable_versions": [],
                    "region_codes": [],
                    "retrieval_text": chunk.retrieval_text,
                    vector_dense_field(self.settings): list(vector),
                    "index_generation_id": self.generation_id,
                    "analyzer_revision": vector_analyzer(self.settings),
                    "content_checksum": chunk.content_sha256,
                }
            )
        with self.tracer.span(
            "document.vector.write",
            {"chunk_count": len(records), "backend": self.settings.vector_backend},
        ):
            writer = ZillizSafeProjectionWriter(self.adapter._connected(), self.settings)
            saga = self.saga
            index_job_id = (
                saga.begin(
                    tenant_id=tenant_id,
                    space_id=space_id,
                    document_id=document_id,
                    document_version_id=result.chunks[0].version_id,
                    generation_id=self.generation_id,
                    records=records,
                )
                if saga is not None
                else None
            )
            try:
                writer.insert_records(
                    records,
                    on_batch_confirmed=(
                        (
                            lambda batch_number, batch: saga.confirm_batch(
                                index_job_id,
                                batch_number,
                                batch,
                                vector=True,
                                control=False,
                            )
                        )
                        if saga is not None and index_job_id is not None
                        else None
                    ),
                )
            except Exception:
                if saga is not None and index_job_id is not None:
                    saga.fail(index_job_id, "VECTOR_BATCH_WRITE_FAILED")
                raise
        projections: list[AuthorizedChunk] = []
        for chunk in (*result.parent_chunks, *result.chunks):
            projections.append(
                AuthorizedChunk(
                    chunk_id=chunk.id,
                    tenant_id=tenant_id,
                    space_id=space_id,
                    document_id=document_id,
                    document_version_id=chunk.version_id,
                    parent_chunk_id=chunk.parent_chunk_id,
                    display_text=chunk.display_text,
                    retrieval_text=chunk.retrieval_text,
                    locator={
                        **chunk.locator.to_dict(),
                        "section_id": chunk.section_id,
                        "section_path": chunk.metadata.get("section_path", "root"),
                        "heading": chunk.metadata.get("heading", ""),
                    },
                    content_checksum=chunk.content_sha256,
                    visibility=security.visibility,
                    acl_scope_tokens=security.acl_scope_tokens,
                    classification_level=security.classification_level,
                    lifecycle_projection=security.lifecycle_projection,
                    valid_from_epoch=security.valid_from_epoch,
                    valid_to_epoch=security.valid_to_epoch,
                    permission_revision=security.permission_revision,
                    current_version=False,
                )
            )
        try:
            self.control_plane.upsert_chunks(projections)
            if saga is not None and index_job_id is not None:
                for batch_number, record_batch in enumerate(writer._batches(records), start=1):
                    saga.confirm_batch(
                        index_job_id,
                        batch_number,
                        record_batch,
                        vector=False,
                        control=True,
                    )
                saga.mark_ready(index_job_id)
        except Exception:
            if saga is not None and index_job_id is not None:
                saga.fail(index_job_id, "CONTROL_PROJECTION_WRITE_FAILED")
            raise
