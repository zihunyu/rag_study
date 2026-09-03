"""Persistent local chunk projection and query-driven hybrid index."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

from ragkb.adapters.retrieval_memory import LocalHybridIndex, LocalIndexRecord
from ragkb.adapters.sqlite_retrieval import SQLiteRetrievalControlPlane
from ragkb.contracts.ports import EmbeddingPort
from ragkb.document_processing.chunking import ChunkingResult
from ragkb.domain.retrieval import AuthorizedChunk, IndexCandidate, SearchContext
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLiteLocalHybridIndex:
    revision = "sqlite-local-bm25-cosine-index:v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()

    def _index(self, context: SearchContext) -> LocalHybridIndex:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, document_version_id, parent_chunk_id,
                       retrieval_text, vector_json, security_watermark
                FROM local_search_index WHERE index_generation_id = ?
                """,
                (context.active_generation_id,),
            ).fetchall()
        records = tuple(
            LocalIndexRecord(
                chunk_id=str(row["chunk_id"]),
                document_version_id=str(row["document_version_id"]),
                parent_chunk_id=(
                    str(row["parent_chunk_id"]) if row["parent_chunk_id"] is not None else None
                ),
                text=str(row["retrieval_text"]),
                vector=tuple(map(float, json.loads(str(row["vector_json"])))),
            )
            for row in rows
        )
        watermark = max((int(row["security_watermark"]) for row in rows), default=0)
        return LocalHybridIndex(records, security_watermark=watermark)

    def observed_security_watermark(self, context: SearchContext) -> int:
        return self._index(context).observed_security_watermark(context)

    def search_bm25(
        self, query: str, context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        return self._index(context).search_bm25(query, context, limit)

    def search_dense(
        self, vector: Sequence[float], context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        return self._index(context).search_dense(vector, context, limit)


class SQLiteLocalIndexingSink:
    revision = "sqlite-local-indexing-sink:v1"

    def __init__(
        self,
        database: SQLiteDatabase,
        control_plane: SQLiteRetrievalControlPlane,
        embedding: EmbeddingPort,
        *,
        generation_id: str,
        embedding_batch_size: int = 32,
    ) -> None:
        self.database = database
        self.control_plane = control_plane
        self.embedding = embedding
        self.generation_id = generation_id
        self.embedding_batch_size = embedding_batch_size

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
        for start in range(0, len(result.chunks), self.embedding_batch_size):
            batch = result.chunks[start : start + self.embedding_batch_size]
            vectors.extend(self.embedding.embed([item.retrieval_text for item in batch]))
        if len(vectors) != len(result.chunks):
            raise ValueError("LOCAL_INDEX_EMBEDDING_COUNT_MISMATCH")
        parent_by_id = {item.id: item for item in result.parent_chunks}
        with self.database.transaction(immediate=True) as connection:
            version_ids = {item.version_id for item in result.chunks}
            for version_id in version_ids:
                connection.execute(
                    "DELETE FROM local_search_index WHERE document_version_id = ?", (version_id,)
                )
            for chunk, vector in zip(result.chunks, vectors, strict=True):
                connection.execute(
                    """
                    INSERT INTO local_search_index(
                        chunk_id, document_version_id, parent_chunk_id, retrieval_text,
                        vector_json, index_generation_id, security_watermark, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.id,
                        chunk.version_id,
                        chunk.parent_chunk_id,
                        chunk.retrieval_text,
                        json.dumps(list(vector), separators=(",", ":")),
                        self.generation_id,
                        permission_revision,
                        time.time(),
                    ),
                )
        for chunk in (*result.parent_chunks, *result.chunks):
            parent = parent_by_id.get(chunk.id)
            self.control_plane.put_for_test(
                AuthorizedChunk(
                    chunk_id=chunk.id,
                    tenant_id=tenant_id,
                    space_id=space_id,
                    document_id=document_id,
                    document_version_id=chunk.version_id,
                    parent_chunk_id=None if parent is not None else chunk.parent_chunk_id,
                    display_text=chunk.display_text,
                    retrieval_text=chunk.retrieval_text,
                    locator=chunk.locator.to_dict(),
                    content_checksum=chunk.content_sha256,
                    visibility="TENANT",
                    acl_scope_tokens=(),
                    classification_level=0,
                    lifecycle_projection="SERVING",
                    valid_from_epoch=0,
                    valid_to_epoch=0,
                    permission_revision=permission_revision,
                    current_version=True,
                )
            )
