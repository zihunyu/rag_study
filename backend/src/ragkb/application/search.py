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
    normalized = unicodedata.normalize("NFKC", query).strip()
    words = normalized.split()
    if re.search(r"(?i)\b(?=[a-z0-9_-]*\d)[a-z0-9]+(?:[-_/][a-z0-9]+)+\b", query):
        return "identifier"
    if re.search(r"(?i)(?:错误码|条款|编号|订单|型号)\s*[:：#]?\s*[a-z0-9_-]*\d", normalized):
        return "identifier"
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", normalized))
    if cjk_count >= 4:
        return "semantic"
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


def score_calibrated_fuse(
    channels: Sequence[Sequence[IndexCandidate]],
    *,
    rrf_k: int,
    channel_weights: dict[SearchChannel, float],
) -> tuple[tuple[IndexCandidate, float, tuple[SearchChannel, ...]], ...]:
    """Blend per-channel normalized confidence with rank stability."""

    scores: dict[str, float] = defaultdict(float)
    candidates: dict[str, IndexCandidate] = {}
    seen_channels: dict[str, set[SearchChannel]] = defaultdict(set)
    for channel in channels:
        if not channel:
            continue
        raw = [candidate.score for candidate in channel]
        low, high = min(raw), max(raw)
        for rank, candidate in enumerate(channel, start=1):
            if candidate.channel == "dense":
                absolute = max(0.0, min(1.0, (candidate.score + 1.0) / 2.0))
            else:
                positive = max(0.0, candidate.score)
                absolute = positive / (1.0 + positive)
            relative = absolute if high == low else (candidate.score - low) / (high - low)
            confidence = 0.5 * absolute + 0.5 * relative
            rank_confidence = rrf_k / (rrf_k + rank)
            weight = channel_weights.get(candidate.channel, 1.0)
            scores[candidate.chunk_id] += weight * (
                0.75 * confidence + 0.25 * rank_confidence * absolute
            )
            candidates.setdefault(candidate.chunk_id, candidate)
            seen_channels[candidate.chunk_id].add(candidate.channel)
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return tuple(
        (candidates[chunk_id], scores[chunk_id], tuple(sorted(seen_channels[chunk_id])))
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
        max_chunks_per_section: int = 2,
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
        self.max_chunks_per_section = max_chunks_per_section
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
                warnings.append("DENSE_RETRIEVAL_UNAVAILABLE")
                try:
                    with self.tracer.span("rag.retrieval.bm25"):
                        return self.index.search_bm25(query, context, self.bm25_top_k), (), warnings
                except TransientProviderError:
                    warnings.append("BM25_RETRIEVAL_UNAVAILABLE")
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
        # Discard late writes from a retired ingestion attempt before score fusion.
        if any(item.vector_pk for item in (*bm25, *dense)):
            projected = self.control_plane.authorize_chunks(
                [item.chunk_id for item in (*bm25, *dense)], context
            )

            def current_attempt(item: IndexCandidate) -> bool:
                chunk = projected.get(item.chunk_id)
                return chunk is not None and (
                    not chunk.locator.get("vector_pk")
                    or chunk.locator["vector_pk"] == item.vector_pk
                )

            bm25 = tuple(item for item in bm25 if current_attempt(item))
            dense = tuple(item for item in dense if current_attempt(item))
        with self.tracer.span("rag.retrieval.fusion", {"query_type": query_type}):
            fused = score_calibrated_fuse(
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
        authorized_candidates: list[tuple[AuthorizedChunk, float, tuple[SearchChannel, ...]]] = []
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
            authorized_candidates.append((chunk, score, channels))
            if len(authorized_candidates) >= self.rerank_top_k:
                break
        if authorized_candidates:
            try:
                with self.tracer.span("rag.retrieval.rerank"):
                    order = self.reranker.rerank(
                        normalized,
                        [chunk.retrieval_text for chunk, _, _ in authorized_candidates],
                    )
            except TransientProviderError:
                order = tuple(range(len(authorized_candidates)))
                warnings.append("RERANKER_UNAVAILABLE")
        else:
            order = ()
        requested = min(limit or self.final_evidence_count, self.final_evidence_count)
        hits: list[SearchHit] = []
        selected_texts: list[str] = []
        document_counts: Counter[str] = Counter()
        section_counts: Counter[str] = Counter()
        for position in order:
            if position < 0 or position >= len(authorized_candidates):
                raise ValueError("reranker returned an invalid candidate index")
            chunk, score, channels = authorized_candidates[position]
            section_key = str(chunk.locator.get("section_path", chunk.document_id))
            if (
                document_counts[chunk.document_id] >= self.max_chunks_per_document
                or section_counts[section_key] >= self.max_chunks_per_section
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
            selected_texts.append(chunk.retrieval_text)
            document_counts[chunk.document_id] += 1
            section_counts[section_key] += 1
            parent = (
                self.control_plane.authorize_parent(chunk.parent_chunk_id, context)
                if chunk.parent_chunk_id
                else None
            )
            if parent is not None and not self._currently_authorized(parent, context):
                parent = None
            generation_parts: list[str] = []
            section_path = str(chunk.locator.get("section_path", "")).strip()
            if section_path and section_path != "root":
                generation_parts.append(f"SECTION_PATH: {section_path}")
            generation_parts.append(chunk.retrieval_text)
            if parent is not None and parent.retrieval_text != chunk.retrieval_text:
                generation_parts.append(f"PARENT_CONTEXT: {parent.retrieval_text}")
            generation_context = "\n".join(generation_parts)
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_version_id=chunk.document_version_id,
                    text=generation_context,
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
                    display_text=chunk.display_text,
                    retrieval_text=chunk.retrieval_text,
                    generation_context=generation_context,
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
