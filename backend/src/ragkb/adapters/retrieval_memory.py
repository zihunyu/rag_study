"""Deterministic G2 retrieval adapters for contract and API tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ragkb.domain.retrieval import AuthorizedChunk, IndexCandidate, SearchContext


class InMemoryHybridIndex:
    revision = "in-memory-hybrid-index:g2-test-v1"

    def __init__(
        self,
        *,
        bm25: Sequence[IndexCandidate] = (),
        dense: Sequence[IndexCandidate] = (),
        security_watermark: int = 0,
    ) -> None:
        self._bm25 = tuple(bm25)
        self._dense = tuple(dense)
        self._watermark = security_watermark

    def observed_security_watermark(self, context: SearchContext) -> int:
        return self._watermark

    def search_bm25(
        self, query: str, context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        return self._bm25[:limit]

    def search_dense(
        self, vector: Sequence[float], context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        return self._dense[:limit]


class InMemoryRetrievalControlPlane:
    revision = "in-memory-retrieval-control:g2-test-v1"

    def __init__(self, chunks: Mapping[str, AuthorizedChunk] | None = None) -> None:
        self._chunks = dict(chunks or {})

    @staticmethod
    def _allowed(chunk: AuthorizedChunk, context: SearchContext) -> bool:
        acl_allowed = chunk.visibility == "TENANT" or bool(
            set(chunk.acl_scope_tokens).intersection(context.subject_scope_tokens)
        )
        temporally_valid = chunk.valid_from_epoch <= context.as_of_epoch and (
            chunk.valid_to_epoch == 0 or chunk.valid_to_epoch > context.as_of_epoch
        )
        return (
            chunk.tenant_id == context.tenant_id
            and chunk.space_id in context.space_ids
            and chunk.lifecycle_projection == "SERVING"
            and chunk.current_version
            and chunk.classification_level <= context.clearance_level
            and chunk.permission_revision <= context.active_permission_revision
            and temporally_valid
            and acl_allowed
        )

    def authorize_chunks(
        self, chunk_ids: Sequence[str], context: SearchContext
    ) -> Mapping[str, AuthorizedChunk]:
        return {
            chunk_id: chunk
            for chunk_id in chunk_ids
            if (chunk := self._chunks.get(chunk_id)) is not None and self._allowed(chunk, context)
        }

    def authorize_parent(
        self, parent_chunk_id: str, context: SearchContext
    ) -> AuthorizedChunk | None:
        chunk = self._chunks.get(parent_chunk_id)
        return chunk if chunk is not None and self._allowed(chunk, context) else None
