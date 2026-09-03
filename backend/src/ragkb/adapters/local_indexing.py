"""Persistent local chunk projection and query-driven hybrid index."""

# Dynamic placeholder counts are generated internally; all values remain bound parameters.
# ruff: noqa: S608

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from usearch.index import Index

from ragkb.adapters.retrieval_memory import analyze_terms
from ragkb.adapters.sqlite_retrieval import SQLiteRetrievalControlPlane
from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.ports import EmbeddingPort
from ragkb.document_processing.chunking import ChunkingResult
from ragkb.domain.retrieval import (
    AuthorizedChunk,
    IndexCandidate,
    SearchContext,
    SecurityProjection,
)
from ragkb.infrastructure.sqlite import SQLiteDatabase


@dataclass(frozen=True)
class _SnapshotRecord:
    chunk_id: str
    document_version_id: str
    parent_chunk_id: str | None


@dataclass(frozen=True)
class _LocalANNSnapshot:
    signature: tuple[int, float]
    index: Index | None
    key_to_record: dict[int, _SnapshotRecord]


class SQLiteLocalHybridIndex:
    revision = "sqlite-fts5-usearch-snapshot-index:v2"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()
        self._lock = threading.Lock()
        self._snapshots: dict[str, _LocalANNSnapshot] = {}
        self._ann_root = self.database.path.parent / "local-ann"

    @staticmethod
    def _key(chunk_id: str) -> int:
        digest = hashlib.blake2b(chunk_id.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") & ((1 << 63) - 1)

    def _signature(self, generation_id: str) -> tuple[int, float]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS item_count, COALESCE(MAX(updated_at), 0) AS latest
                FROM local_search_index WHERE index_generation_id = ?
                """,
                (generation_id,),
            ).fetchone()
        return int(row["item_count"]), float(row["latest"])

    def _snapshot(self, generation_id: str) -> _LocalANNSnapshot:
        signature = self._signature(generation_id)
        with self._lock:
            cached = self._snapshots.get(generation_id)
            if cached is not None and cached.signature == signature:
                return cached
            with self.database.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT chunk_id, document_version_id, parent_chunk_id, vector_json
                    FROM local_search_index WHERE index_generation_id = ? ORDER BY chunk_id
                    """,
                    (generation_id,),
                ).fetchall()
            records: dict[int, _SnapshotRecord] = {}
            vectors: list[list[float]] = []
            keys: list[int] = []
            for row in rows:
                chunk_id = str(row["chunk_id"])
                key = self._key(chunk_id)
                if key in records:
                    raise ValueError("LOCAL_ANN_KEY_COLLISION")
                records[key] = _SnapshotRecord(
                    chunk_id,
                    str(row["document_version_id"]),
                    str(row["parent_chunk_id"]) if row["parent_chunk_id"] else None,
                )
                keys.append(key)
                vectors.append(list(map(float, json.loads(str(row["vector_json"])))))
            index = None
            if vectors:
                dimension = len(vectors[0])
                if any(len(vector) != dimension for vector in vectors):
                    raise ValueError("LOCAL_ANN_DIMENSION_MISMATCH")
                fingerprint = hashlib.sha256(
                    f"{generation_id}:{signature}:{dimension}".encode()
                ).hexdigest()[:24]
                path = self._ann_root / f"{fingerprint}.usearch"
                index = Index(ndim=dimension, metric="cos", dtype="f32")
                if path.exists():
                    index.view(path)
                else:
                    self._ann_root.mkdir(parents=True, exist_ok=True)
                    index.add(
                        np.asarray(keys, dtype=np.uint64),
                        np.asarray(vectors, dtype=np.float32),
                    )
                    temporary = Path(f"{path}.tmp")
                    index.save(temporary)
                    temporary.replace(path)
            snapshot = _LocalANNSnapshot(signature, index, records)
            self._snapshots[generation_id] = snapshot
            return snapshot

    @staticmethod
    def _security_clause(context: SearchContext) -> tuple[str, list[object]]:
        spaces = ",".join("?" for _ in context.space_ids)
        params: list[object] = [
            context.tenant_id,
            *context.space_ids,
            context.clearance_level,
            context.active_permission_revision,
            context.as_of_epoch,
            context.as_of_epoch,
        ]
        acl = "0"
        if context.subject_scope_tokens:
            scopes = ",".join("?" for _ in context.subject_scope_tokens)
            acl = (
                "EXISTS (SELECT 1 FROM json_each(p.acl_scope_tokens_json) "
                f"WHERE value IN ({scopes}))"
            )  # noqa: S608
            params.extend(context.subject_scope_tokens)
        return (
            f"p.tenant_id = ? AND p.space_id IN ({spaces}) "
            "AND p.lifecycle_projection = 'SERVING' AND p.current_version = 1 "
            "AND p.classification_level <= ? AND p.permission_revision <= ? "
            "AND p.valid_from_epoch <= ? AND (p.valid_to_epoch = 0 OR p.valid_to_epoch > ?) "
            f"AND (p.visibility = 'TENANT' OR {acl})",
            params,
        )

    def observed_security_watermark(self, context: SearchContext) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(security_watermark), 0) AS watermark
                FROM local_search_index WHERE index_generation_id = ?
                """,
                (context.active_generation_id,),
            ).fetchone()
        return int(row["watermark"])

    def search_bm25(
        self, query: str, context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        terms = analyze_terms(query)
        if not terms:
            return ()
        security, params = self._security_clause(context)
        expression = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.chunk_id, p.document_version_id, p.parent_chunk_id,
                       bm25(local_search_fts) AS distance
                FROM local_search_fts
                JOIN retrieval_projections p ON p.chunk_id = local_search_fts.chunk_id
                WHERE local_search_fts MATCH ? AND {security}
                ORDER BY distance ASC, p.chunk_id ASC LIMIT ?
                """,  # noqa: S608
                (expression, *params, limit),
            ).fetchall()
        return tuple(
            IndexCandidate(
                str(row["chunk_id"]),
                str(row["document_version_id"]),
                str(row["parent_chunk_id"]) if row["parent_chunk_id"] else None,
                "bm25",
                rank,
                -float(row["distance"]),
            )
            for rank, row in enumerate(rows, start=1)
        )

    def search_dense(
        self, vector: Sequence[float], context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        snapshot = self._snapshot(context.active_generation_id)
        if snapshot.index is None:
            return ()
        security, params = self._security_clause(context)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT p.chunk_id FROM retrieval_projections p WHERE {security}",  # noqa: S608
                params,
            ).fetchall()
        eligible = {str(row["chunk_id"]) for row in rows}
        requested = min(len(snapshot.key_to_record), max(limit * 4, 32))
        accepted: list[tuple[_SnapshotRecord, float]] = []
        while requested:
            matches = snapshot.index.search(np.asarray(vector, dtype=np.float32), count=requested)
            accepted = [
                (snapshot.key_to_record[int(key)], 1.0 - float(distance))
                for key, distance in zip(matches.keys, matches.distances, strict=True)
                if snapshot.key_to_record[int(key)].chunk_id in eligible
            ]
            if len(accepted) >= limit or requested == len(snapshot.key_to_record):
                break
            requested = min(len(snapshot.key_to_record), requested * 2)
        return tuple(
            IndexCandidate(
                record.chunk_id,
                record.document_version_id,
                record.parent_chunk_id,
                "dense",
                rank,
                score,
            )
            for rank, (record, score) in enumerate(accepted[:limit], start=1)
        )


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
        tracer: TracerPort | None = None,
    ) -> None:
        self.database = database
        self.control_plane = control_plane
        self.embedding = embedding
        self.generation_id = generation_id
        self.embedding_batch_size = embedding_batch_size
        self.tracer = tracer or InMemoryTracer()

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
        security = security_projection or SecurityProjection.unapproved(
            permission_revision=permission_revision,
            now=int(time.time()),
        )
        vectors: list[Sequence[float]] = []
        for start in range(0, len(result.chunks), self.embedding_batch_size):
            batch = result.chunks[start : start + self.embedding_batch_size]
            with self.tracer.span(
                "document.embedding.batch", {"batch_size": len(batch), "provider": "local"}
            ):
                vectors.extend(self.embedding.embed([item.retrieval_text for item in batch]))
        if len(vectors) != len(result.chunks):
            raise ValueError("LOCAL_INDEX_EMBEDDING_COUNT_MISMATCH")
        parent_by_id = {item.id: item for item in result.parent_chunks}
        with self.tracer.span("document.vector.write", {"chunk_count": len(result.chunks)}):
            self._write_index(result, vectors, security.permission_revision)
        projections: list[AuthorizedChunk] = []
        for chunk in (*result.parent_chunks, *result.chunks):
            parent = parent_by_id.get(chunk.id)
            projections.append(
                AuthorizedChunk(
                    chunk_id=chunk.id,
                    tenant_id=tenant_id,
                    space_id=space_id,
                    document_id=document_id,
                    document_version_id=chunk.version_id,
                    parent_chunk_id=None if parent is not None else chunk.parent_chunk_id,
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
        self.control_plane.upsert_chunks(projections)

    def _write_index(
        self,
        result: ChunkingResult,
        vectors: Sequence[Sequence[float]],
        permission_revision: int,
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            version_ids = {item.version_id for item in result.chunks}
            for version_id in version_ids:
                chunk_rows = connection.execute(
                    "SELECT chunk_id FROM local_search_index WHERE document_version_id = ?",
                    (version_id,),
                ).fetchall()
                for row in chunk_rows:
                    connection.execute(
                        "DELETE FROM local_search_fts WHERE chunk_id = ?", (str(row["chunk_id"]),)
                    )
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
                connection.execute(
                    "INSERT INTO local_search_fts(chunk_id, retrieval_terms) VALUES (?, ?)",
                    (chunk.id, " ".join(analyze_terms(chunk.retrieval_text))),
                )
