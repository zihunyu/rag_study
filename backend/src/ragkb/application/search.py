"""Deterministic hybrid search orchestration without answer generation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

from ragkb.contracts.ports import (
    EmbeddingPort,
    HybridIndexPort,
    RerankerPort,
    RetrievalControlPlanePort,
)
from ragkb.domain.retrieval import (
    AuthorizedChunk,
    IndexCandidate,
    SearchChannel,
    SearchContext,
    SearchHit,
    SearchResult,
    SecurityWatermarkNotReady,
)


def rrf_fuse(
    channels: Sequence[Sequence[IndexCandidate]], *, rrf_k: int
) -> tuple[tuple[IndexCandidate, float, tuple[SearchChannel, ...]], ...]:
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    scores: dict[str, float] = defaultdict(float)
    candidates: dict[str, IndexCandidate] = {}
    seen_channels: dict[str, set[SearchChannel]] = defaultdict(set)
    for channel in channels:
        for rank, candidate in enumerate(channel, start=1):
            scores[candidate.chunk_id] += 1.0 / (rrf_k + rank)
            candidates.setdefault(candidate.chunk_id, candidate)
            seen_channels[candidate.chunk_id].add(candidate.channel)
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return tuple(
        (
            candidates[chunk_id],
            scores[chunk_id],
            tuple(sorted(seen_channels[chunk_id])),
        )
        for chunk_id in ordered
    )


class HybridSearchService:
    revision = "hybrid-search-service:g2-v1"

    def __init__(
        self,
        embedding: EmbeddingPort,
        index: HybridIndexPort,
        control_plane: RetrievalControlPlanePort,
        reranker: RerankerPort,
        *,
        bm25_top_k: int,
        dense_top_k: int,
        rrf_k: int,
        rerank_top_k: int,
        final_evidence_count: int,
        lifecycle_authorizer: Callable[[AuthorizedChunk, SearchContext], bool] | None = None,
    ) -> None:
        self.embedding = embedding
        self.index = index
        self.control_plane = control_plane
        self.reranker = reranker
        self.bm25_top_k = bm25_top_k
        self.dense_top_k = dense_top_k
        self.rrf_k = rrf_k
        self.rerank_top_k = rerank_top_k
        self.final_evidence_count = final_evidence_count
        self.lifecycle_authorizer = lifecycle_authorizer

    def _currently_authorized(self, chunk: AuthorizedChunk, context: SearchContext) -> bool:
        return self.lifecycle_authorizer is None or self.lifecycle_authorizer(chunk, context)

    def search(
        self, query: str, context: SearchContext, *, limit: int | None = None
    ) -> SearchResult:
        normalized = query.strip()
        if not normalized:
            raise ValueError("search query must be non-empty")
        observed = self.index.observed_security_watermark(context)
        if observed < context.required_security_watermark:
            raise SecurityWatermarkNotReady("SECURITY_WATERMARK_NOT_READY")
        warnings: list[str] = []
        try:
            vectors = self.embedding.embed([normalized])
            if len(vectors) != 1:
                raise ValueError("embedding adapter must return exactly one query vector")
            dense = self.index.search_dense(vectors[0], context, self.dense_top_k)
        except Exception:
            dense = ()
            warnings.append("DENSE_RETRIEVAL_UNAVAILABLE")
        bm25 = self.index.search_bm25(normalized, context, self.bm25_top_k)
        fused = rrf_fuse((bm25, dense), rrf_k=self.rrf_k)
        candidate_ids = [candidate.chunk_id for candidate, _, _ in fused]
        authorized = self.control_plane.authorize_chunks(candidate_ids, context)
        deduplicated: list[tuple[AuthorizedChunk, float, tuple[SearchChannel, ...]]] = []
        seen_checksums: set[str] = set()
        for candidate, score, channels in fused:
            chunk = authorized.get(candidate.chunk_id)
            if (
                chunk is None
                or not self._currently_authorized(chunk, context)
                or chunk.content_checksum in seen_checksums
            ):
                continue
            seen_checksums.add(chunk.content_checksum)
            deduplicated.append((chunk, score, channels))
            if len(deduplicated) >= self.rerank_top_k:
                break
        if deduplicated:
            try:
                order = self.reranker.rerank(
                    normalized, [chunk.retrieval_text for chunk, _, _ in deduplicated]
                )
            except Exception:
                order = tuple(range(len(deduplicated)))
                warnings.append("RERANKER_UNAVAILABLE")
        else:
            order = ()
        requested = min(limit or self.final_evidence_count, self.final_evidence_count)
        hits: list[SearchHit] = []
        for position in order:
            if position < 0 or position >= len(deduplicated):
                raise ValueError("reranker returned an invalid candidate index")
            chunk, score, channels = deduplicated[position]
            parent = (
                self.control_plane.authorize_parent(chunk.parent_chunk_id, context)
                if chunk.parent_chunk_id
                else None
            )
            if parent is not None and not self._currently_authorized(parent, context):
                parent = None
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_version_id=chunk.document_version_id,
                    text=chunk.display_text,
                    locator=chunk.locator,
                    fused_score=score,
                    rerank_position=len(hits) + 1,
                    channels=channels,
                    parent_chunk_id=parent.chunk_id if parent else None,
                    parent_text=parent.display_text if parent else None,
                    valid_from_epoch=chunk.valid_from_epoch,
                    valid_to_epoch=chunk.valid_to_epoch,
                    permission_revision=chunk.permission_revision,
                    current_version=chunk.current_version,
                )
            )
            if len(hits) >= requested:
                break
        return SearchResult(
            tuple(hits),
            observed,
            real_acceptance=False,
            degraded=bool(warnings),
            warnings=tuple(warnings),
        )
