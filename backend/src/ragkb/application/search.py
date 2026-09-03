"""Hybrid retrieval, query-aware fusion, authorization and evidence diversity."""

from __future__ import annotations

import contextvars
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, cast

from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.ports import (
    EmbeddingPort,
    HybridIndexPort,
    RerankerPort,
    RetrievalControlPlanePort,
)
from ragkb.domain.errors import TransientProviderError
from ragkb.domain.retrieval import (
    AuthorizedChunk,
    IndexCandidate,
    SearchChannel,
    SearchContext,
    SearchHit,
    SearchResult,
    SecurityWatermarkNotReady,
)

QueryType = Literal["identifier", "keyword", "semantic"]


def classify_query(query: str) -> QueryType:
    words = query.split()
    if re.search(r"(?i)\b(?=[a-z0-9_-]*\d)[a-z0-9]+(?:[-_/][a-z0-9]+)+\b", query):
        return "identifier"
    if len(words) <= 3 and not any(character in query for character in "?？怎么为何什么"):
        return "keyword"
    return "semantic"


def rrf_fuse(
    channels: Sequence[Sequence[IndexCandidate]],
    *,
    rrf_k: int,
    channel_weights: dict[SearchChannel, float] | None = None,
) -> tuple[tuple[IndexCandidate, float, tuple[SearchChannel, ...]], ...]:
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    scores: dict[str, float] = defaultdict(float)
    candidates: dict[str, IndexCandidate] = {}
    seen_channels: dict[str, set[SearchChannel]] = defaultdict(set)
    weights = channel_weights or {"bm25": 1.0, "dense": 1.0}
    for channel in channels:
        for rank, candidate in enumerate(channel, start=1):
            scores[candidate.chunk_id] += weights.get(candidate.channel, 1.0) / (rrf_k + rank)
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


def _canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _shingles(value: str, size: int = 3) -> frozenset[str]:
    normalized = _canonical_text(value)
    if len(normalized) <= size:
        return frozenset((normalized,)) if normalized else frozenset()
    return frozenset(
        normalized[index : index + size] for index in range(len(normalized) - size + 1)
    )


def near_duplicate(left: str, right: str, *, threshold: float) -> bool:
    left_shingles = _shingles(left)
    right_shingles = _shingles(right)
    if not left_shingles or not right_shingles:
        return False
    similarity = len(left_shingles & right_shingles) / len(left_shingles | right_shingles)
    return similarity >= threshold


class HybridSearchService:
    revision = "hybrid-search-service:g2-v2"

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
        bm25_weight: float = 1.0,
        dense_weight: float = 1.0,
        identifier_bm25_weight: float = 2.0,
        near_duplicate_threshold: float = 0.92,
        max_chunks_per_document: int = 3,
        real_acceptance: bool = False,
        tracer: TracerPort | None = None,
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
        self.bm25_weight = bm25_weight
        self.dense_weight = dense_weight
        self.identifier_bm25_weight = identifier_bm25_weight
        self.near_duplicate_threshold = near_duplicate_threshold
        self.max_chunks_per_document = max_chunks_per_document
        self.real_acceptance = real_acceptance
        self.tracer = tracer or InMemoryTracer()
        self.lifecycle_authorizer = lifecycle_authorizer

    def _currently_authorized(self, chunk: AuthorizedChunk, context: SearchContext) -> bool:
        return self.lifecycle_authorizer is None or self.lifecycle_authorizer(chunk, context)

    def _retrieve(
        self, query: str, context: SearchContext
    ) -> tuple[Sequence[IndexCandidate], Sequence[IndexCandidate], list[str]]:
        warnings: list[str] = []
        native = getattr(self.index, "search_hybrid", None)
        if callable(native):
            try:
                with self.tracer.span("rag.retrieval.embedding"):
                    vectors = self.embedding.embed([query])
                if len(vectors) != 1:
                    raise ValueError("embedding adapter must return exactly one query vector")
                result = cast(Any, native)(
                    query,
                    vectors[0],
                    context,
                    bm25_limit=self.bm25_top_k,
                    dense_limit=self.dense_top_k,
                )
                return result[0], result[1], warnings
            except TransientProviderError:
                warnings.extend(("BM25_RETRIEVAL_UNAVAILABLE", "DENSE_RETRIEVAL_UNAVAILABLE"))
                return (), (), warnings

        def dense_path() -> Sequence[IndexCandidate]:
            with self.tracer.span("rag.retrieval.embedding"):
                vectors = self.embedding.embed([query])
            if len(vectors) != 1:
                raise ValueError("embedding adapter must return exactly one query vector")
            with self.tracer.span("rag.retrieval.dense"):
                return self.index.search_dense(vectors[0], context, self.dense_top_k)

        def bm25_path() -> Sequence[IndexCandidate]:
            with self.tracer.span("rag.retrieval.bm25"):
                return self.index.search_bm25(query, context, self.bm25_top_k)

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid-retrieval") as pool:
            bm25_context = contextvars.copy_context()
            dense_context = contextvars.copy_context()
            bm25_future = pool.submit(bm25_context.run, bm25_path)
            dense_future = pool.submit(dense_context.run, dense_path)
            try:
                bm25 = bm25_future.result()
            except TransientProviderError:
                bm25 = ()
                warnings.append("BM25_RETRIEVAL_UNAVAILABLE")
            try:
                dense = dense_future.result()
            except TransientProviderError:
                dense = ()
                warnings.append("DENSE_RETRIEVAL_UNAVAILABLE")
        return bm25, dense, warnings

    def search(
        self, query: str, context: SearchContext, *, limit: int | None = None
    ) -> SearchResult:
        normalized = query.strip()
        if not normalized:
            raise ValueError("search query must be non-empty")
        observed = self.index.observed_security_watermark(context)
        if observed < context.required_security_watermark:
            raise SecurityWatermarkNotReady("SECURITY_WATERMARK_NOT_READY")
        with self.tracer.span(
            "rag.retrieval", {"query_type": classify_query(normalized), "limit": limit or 0}
        ):
            bm25, dense, warnings = self._retrieve(normalized, context)
        query_type = classify_query(normalized)
        with self.tracer.span("rag.retrieval.fusion", {"query_type": query_type}):
            fused = rrf_fuse(
                (bm25, dense),
                rrf_k=self.rrf_k,
                channel_weights={
                    "bm25": (
                        self.identifier_bm25_weight
                        if query_type in {"identifier", "keyword"}
                        else self.bm25_weight
                    ),
                    "dense": self.dense_weight,
                },
            )
        candidate_ids = [candidate.chunk_id for candidate, _, _ in fused]
        authorized = self.control_plane.authorize_chunks(candidate_ids, context)
        deduplicated: list[tuple[AuthorizedChunk, float, tuple[SearchChannel, ...]]] = []
        seen_checksums: set[str] = set()
        selected_texts: list[str] = []
        document_counts: Counter[str] = Counter()
        for candidate, score, channels in fused:
            chunk = authorized.get(candidate.chunk_id)
            if (
                chunk is None
                or not self._currently_authorized(chunk, context)
                or chunk.content_checksum in seen_checksums
                or document_counts[chunk.document_id] >= self.max_chunks_per_document
                or any(
                    near_duplicate(
                        chunk.retrieval_text,
                        selected,
                        threshold=self.near_duplicate_threshold,
                    )
                    for selected in selected_texts
                )
            ):
                continue
            seen_checksums.add(chunk.content_checksum)
            selected_texts.append(chunk.retrieval_text)
            document_counts[chunk.document_id] += 1
            deduplicated.append((chunk, score, channels))
            if len(deduplicated) >= self.rerank_top_k:
                break
        if deduplicated:
            try:
                with self.tracer.span("rag.retrieval.rerank"):
                    order = self.reranker.rerank(
                        normalized, [chunk.retrieval_text for chunk, _, _ in deduplicated]
                    )
            except TransientProviderError:
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
            real_acceptance=self.real_acceptance,
            degraded=bool(warnings),
            warnings=tuple(warnings),
        )
