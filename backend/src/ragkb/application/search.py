"""Hybrid retrieval, query-aware fusion, authorization and evidence diversity."""

from __future__ import annotations

import contextvars
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from ragkb.application.tracing import InMemoryTracer, TracerPort
from ragkb.contracts.ports import (
    EmbeddingPort,
    HybridIndexPort,
    RerankerPort,
    RetrievalControlPlanePort,
)
from ragkb.domain.errors import TransientProviderError
from ragkb.domain.numeric_facts import NumericTextSignature, numeric_text_signature
from ragkb.domain.retrieval import (
    AuthorizedChunk,
    IndexCandidate,
    RetrievalHealth,
    SearchChannel,
    SearchContext,
    SearchHit,
    SearchResult,
    SearchSource,
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


@dataclass(frozen=True)
class _DedupText:
    text: str
    signature: NumericTextSignature | None
    shingles: frozenset[str]

    @classmethod
    def build(cls, text: str) -> _DedupText:
        return cls(
            re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip(),
            numeric_text_signature(text),
            _shingles(text),
        )


def _same_meaning(left: _DedupText, right: _DedupText, *, threshold: float) -> bool:
    if not left.shingles or not right.shingles:
        return False
    if left.text != right.text and (left.signature is None or left.signature != right.signature):
        return False
    # Similarity proposes a merge; it does not establish semantic equivalence.
    # Preserve words/punctuation that carry scope, negation and object bindings.
    similarity = len(left.shingles & right.shingles) / len(left.shingles | right.shingles)
    return similarity >= threshold


def near_duplicate(left: str, right: str, *, threshold: float) -> bool:
    return _same_meaning(_DedupText.build(left), _DedupText.build(right), threshold=threshold)


def _source(chunk: AuthorizedChunk) -> SearchSource:
    return SearchSource(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        display_text=chunk.display_text,
        retrieval_text=chunk.retrieval_text,
        locator=chunk.locator,
        valid_from_epoch=chunk.valid_from_epoch,
        valid_to_epoch=chunk.valid_to_epoch,
        permission_revision=chunk.permission_revision,
        current_version=chunk.current_version,
    )


def _dedupe_context(chunk: AuthorizedChunk) -> tuple[object, ...]:
    return (
        chunk.valid_from_epoch,
        chunk.valid_to_epoch,
        chunk.visibility,
        tuple(sorted(chunk.acl_scope_tokens)),
        chunk.classification_level,
        str(chunk.locator.get("section_path") or "root").strip(),
    )


class HybridSearchService:
    revision = "hybrid-search-service:facet-batch-v5"

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
        query_planning_enabled: bool = True,
        max_subqueries: int = 4,
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
        self.query_planning_enabled = query_planning_enabled
        self.max_subqueries = max_subqueries

    def _query_vector(self, query: str) -> Sequence[float]:
        embed_query = getattr(self.embedding, "embed_query", None)
        if callable(embed_query):
            return cast(Sequence[float], embed_query(query))
        vectors = self.embedding.embed([query])
        if len(vectors) != 1:
            raise ValueError("embedding adapter must return exactly one query vector")
        return vectors[0]

    def _currently_authorized(self, chunk: AuthorizedChunk, context: SearchContext) -> bool:
        return self.lifecycle_authorizer is None or self.lifecycle_authorizer(chunk, context)

    def _retrieve(
        self, query: str, context: SearchContext
    ) -> tuple[
        Sequence[IndexCandidate], Sequence[IndexCandidate], list[str], tuple[SearchChannel, ...]
    ]:
        from ragkb.application.qa_budget import current

        budget = current.get()
        if budget:
            budget.start_query(query)
        warnings: list[str] = []
        native = getattr(self.index, "search_hybrid", None)
        if callable(native):
            try:
                with self.tracer.span("rag.retrieval.embedding"):
                    vector = self._query_vector(query)
                result = cast(Any, native)(
                    query,
                    vector,
                    context,
                    bm25_limit=self.bm25_top_k,
                    dense_limit=self.dense_top_k,
                )
                return result[0], result[1], warnings, ("bm25", "dense")
            except TransientProviderError:
                warnings.append("DENSE_RETRIEVAL_UNAVAILABLE")
                try:
                    with self.tracer.span("rag.retrieval.bm25"):
                        return (
                            self.index.search_bm25(query, context, self.bm25_top_k),
                            (),
                            warnings,
                            ("bm25",),
                        )
                except TransientProviderError:
                    warnings.append("BM25_RETRIEVAL_UNAVAILABLE")
                    return (), (), warnings, ()

        def dense_path() -> Sequence[IndexCandidate]:
            with self.tracer.span("rag.retrieval.embedding"):
                vector = self._query_vector(query)
            with self.tracer.span("rag.retrieval.dense"):
                return self.index.search_dense(vector, context, self.dense_top_k)

        def bm25_path() -> Sequence[IndexCandidate]:
            with self.tracer.span("rag.retrieval.bm25"):
                return self.index.search_bm25(query, context, self.bm25_top_k)

        available: list[SearchChannel] = []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid-retrieval") as pool:
            bm25_context = contextvars.copy_context()
            dense_context = contextvars.copy_context()
            bm25_future = pool.submit(bm25_context.run, bm25_path)
            dense_future = pool.submit(dense_context.run, dense_path)
            try:
                bm25 = bm25_future.result()
                available.append("bm25")
            except TransientProviderError:
                bm25 = ()
                warnings.append("BM25_RETRIEVAL_UNAVAILABLE")
            try:
                dense = dense_future.result()
                available.append("dense")
            except TransientProviderError:
                dense = ()
                warnings.append("DENSE_RETRIEVAL_UNAVAILABLE")
        return bm25, dense, warnings, tuple(available)

    def _retrieve_many(
        self, queries: tuple[str, ...], context: SearchContext
    ) -> list[
        tuple[
            Sequence[IndexCandidate], Sequence[IndexCandidate], list[str], tuple[SearchChannel, ...]
        ]
    ]:
        bm25_many = getattr(self.index, "search_bm25_many", None)
        dense_many = getattr(self.index, "search_dense_many", None)
        embed_many = getattr(self.embedding, "embed_queries", None)
        if len(queries) == 1 or not all(callable(x) for x in (bm25_many, dense_many, embed_many)):
            return [self._retrieve(query, context) for query in queries]
        from ragkb.application.qa_budget import current

        budget = current.get()
        if budget:
            budget.start_queries(queries)

        def sparse_path() -> Any:
            with self.tracer.span("rag.retrieval.bm25", {"query_count": len(queries)}):
                return cast(Any, bm25_many)(queries, context, self.bm25_top_k)

        def dense_path() -> Any:
            with self.tracer.span("rag.retrieval.embedding", {"query_count": len(queries)}):
                vectors = cast(Any, embed_many)(queries)
                if len(vectors) != len(queries):
                    raise ValueError("QUERY_EMBEDDING_BATCH_COUNT_INVALID")
            with self.tracer.span("rag.retrieval.dense", {"query_count": len(queries)}):
                return cast(Any, dense_many)(vectors, context, self.dense_top_k)

        warnings: list[str] = []
        available: list[SearchChannel] = []
        groups: list[Any] = []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid-batch") as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, fn) for fn in (sparse_path, dense_path)
            ]
            for channel, future in zip(("bm25", "dense"), futures, strict=True):
                try:
                    result = future.result()
                    if len(result) != len(queries):
                        raise ValueError("RETRIEVAL_BATCH_COUNT_INVALID")
                    groups.append(result)
                    available.append(cast(SearchChannel, channel))
                except TransientProviderError:
                    groups.append([()] * len(queries))
                    warnings.append(f"{channel.upper()}_RETRIEVAL_UNAVAILABLE")
        return [
            (groups[0][i], groups[1][i], list(warnings), tuple(available))
            for i in range(len(queries))
        ]

    def search(
        self,
        query: str,
        context: SearchContext,
        *,
        limit: int | None = None,
        query_limit: int | None = None,
        exclude_queries: Sequence[str] = (),
    ) -> SearchResult:
        """Cap this call by the caller's remaining budget and report every attempt."""
        normalized = query.strip()
        if not normalized:
            raise ValueError("search query must be non-empty")
        if query_limit is not None and query_limit < 0:
            raise ValueError("retrieval query limit must be non-negative")
        observed = self.index.observed_security_watermark(context)
        if observed < context.required_security_watermark:
            raise SecurityWatermarkNotReady("SECURITY_WATERMARK_NOT_READY")
        validate_contract = getattr(self.index, "validate_embedding_contract", None)
        if callable(validate_contract):
            validate_contract(context.active_generation_id)
        with self.tracer.span(
            "rag.retrieval", {"query_type": classify_query(normalized), "limit": limit or 0}
        ):
            from ragkb.application.query_planning import REVISION, merge_channel, plan_queries

            maximum = self.max_subqueries if self.query_planning_enabled else 1
            allowed = maximum if query_limit is None else min(maximum, query_limit)
            excluded = {value.strip() for value in exclude_queries}
            queries = tuple(
                value for value in plan_queries(normalized, maximum) if value not in excluded
            )[:allowed]
            if not queries:
                return SearchResult((), observed, real_acceptance=self.real_acceptance)
            with self.tracer.span(
                "rag.retrieval.plan", {"revision": REVISION, "query_count": len(queries)}
            ):
                results = self._retrieve_many(queries, context)
            bm25 = merge_channel([r[0] for r in results], self.bm25_top_k)
            dense = merge_channel([r[1] for r in results], self.dense_top_k)
            warnings = list(dict.fromkeys(w for r in results for w in r[2]))
            available_channels = tuple(dict.fromkeys(c for r in results for c in r[3]))
        if not available_channels:
            return SearchResult(
                (),
                observed,
                degraded=True,
                warnings=tuple(warnings),
                retrieval_health=RetrievalHealth.UNAVAILABLE,
                retrieval_queries=queries,
            )
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
        duplicates: dict[str, list[AuthorizedChunk]] = defaultdict(list)
        profiles: dict[str, _DedupText] = {}

        def duplicate_text(left: str, right: str) -> bool:
            # Per-request, bounded by the retrieved pool; do not retain tenant text
            # globally or repeatedly parse the same body for every candidate pair.
            for value in (left, right):
                if value not in profiles:
                    profiles[value] = _DedupText.build(value)
            return _same_meaning(
                profiles[left], profiles[right], threshold=self.near_duplicate_threshold
            )

        for candidate, score, channels in fused:
            chunk = authorized.get(candidate.chunk_id)
            if (
                chunk is None
                or (context.document_ids and chunk.document_id not in context.document_ids)
                or not self._currently_authorized(chunk, context)
            ):
                continue
            representative = next(
                (
                    selected
                    for selected, _, _ in authorized_candidates
                    if _dedupe_context(selected) == _dedupe_context(chunk)
                    and duplicate_text(
                        chunk.retrieval_text,
                        selected.retrieval_text,
                    )
                    and duplicate_text(
                        chunk.display_text,
                        selected.display_text,
                    )
                ),
                None,
            )
            if representative is not None:
                duplicates[representative.chunk_id].append(chunk)
            else:
                authorized_candidates.append((chunk, score, channels))
        if len(results) > 1 and len(authorized_candidates) > self.rerank_top_k:
            # Allocate half the pool round-robin across facets. Prefer evidence
            # seen in only one query; common hits must not consume every slot.
            # Membership is built only from current, authorized representatives.
            representatives = {
                item.chunk_id: chunk.chunk_id
                for chunk, _, _ in authorized_candidates
                for item in (chunk, *duplicates[chunk.chunk_id])
            }
            lanes: list[list[str]] = []
            for sparse, dense_items, _, _ in results:
                lane = []
                for item in sorted((*sparse, *dense_items), key=lambda item: item.rank):
                    chunk = authorized.get(item.chunk_id)
                    if chunk is None or (
                        chunk.locator.get("vector_pk")
                        and chunk.locator["vector_pk"] != item.vector_pk
                    ):
                        continue
                    key = representatives.get(item.chunk_id)
                    if key and key not in lane:
                        lane.append(key)
                lanes.append(lane)
            frequency = Counter(key for lane in lanes for key in lane)
            for lane in lanes:
                lane.sort(key=lambda key: frequency[key] != 1)
            reserved: list[str] = []
            lane_order = sorted(
                (*lanes[1:], lanes[0]),
                key=lambda lane: not any(frequency[key] == 1 for key in lane),
            )
            target = min(self.rerank_top_k, max(len(lanes), self.rerank_top_k // 2))
            while len(reserved) < target:
                before = len(reserved)
                for lane in lane_order:
                    key = next((key for key in lane if key not in reserved), None)
                    if key is not None:
                        reserved.append(key)
                    if len(reserved) == target:
                        break
                if len(reserved) == before:
                    break
            by_id = {row[0].chunk_id: row for row in authorized_candidates}
            authorized_candidates = [by_id[key] for key in reserved] + [
                row for row in authorized_candidates if row[0].chunk_id not in reserved
            ]
        authorized_candidates = authorized_candidates[: self.rerank_top_k]

        def source_queries(chunk: AuthorizedChunk) -> tuple[str, ...]:
            return tuple(
                query
                for query, (sparse, dense_items, _, _) in zip(queries, results, strict=True)
                if any(
                    item.chunk_id == chunk.chunk_id
                    and (
                        not chunk.locator.get("vector_pk")
                        or chunk.locator["vector_pk"] == item.vector_pk
                    )
                    for item in (*sparse, *dense_items)
                )
            )

        def annotated_source(chunk: AuthorizedChunk) -> SearchSource:
            return replace(_source(chunk), retrieval_queries=source_queries(chunk))

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
        document_counts: Counter[str] = Counter()
        section_counts: Counter[tuple[str, str, str]] = Counter()
        for position in order:
            if position < 0 or position >= len(authorized_candidates):
                raise ValueError("reranker returned an invalid candidate index")
            chunk, score, channels = authorized_candidates[position]
            canonical_section_path = str(chunk.locator.get("section_path") or "").strip() or "root"
            section_key = (
                chunk.document_id,
                chunk.document_version_id,
                canonical_section_path,
            )
            if (
                document_counts[chunk.document_id] >= self.max_chunks_per_document
                or section_counts[section_key] >= self.max_chunks_per_section
            ):
                continue
            document_counts[chunk.document_id] += 1
            section_counts[section_key] += 1
            parent = (
                self.control_plane.authorize_parent(chunk.parent_chunk_id, context)
                if chunk.parent_chunk_id
                else None
            )
            if parent is not None and (
                not self._currently_authorized(parent, context)
                or parent.document_id != chunk.document_id
                or parent.document_version_id != chunk.document_version_id
                # Older parent projections only locate their first child. They
                # need reindexing before the entire parent can be cited safely.
                or not parent.locator.get("source_spans")
            ):
                parent = None
            parent_source = _source(parent) if parent is not None else None
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_version_id=chunk.document_version_id,
                    text=chunk.retrieval_text,
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
                    # Compatibility field: only this hit's text. Evidence assembly
                    # owns prompt context, and parents have separate source IDs.
                    generation_context=chunk.retrieval_text,
                    parent_source=parent_source,
                    duplicate_sources=tuple(_source(item) for item in duplicates[chunk.chunk_id]),
                    retrieval_queries=source_queries(chunk),
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
            retrieval_health=(RetrievalHealth.DEGRADED if warnings else RetrievalHealth.HEALTHY),
            review_sources=tuple(
                annotated_source(item)
                for chunk, _, _ in authorized_candidates
                for item in (chunk, *duplicates[chunk.chunk_id])
            ),
            retrieval_queries=queries,
        )

    def expand_parents(
        self, sources: Sequence[SearchSource], context: SearchContext
    ) -> tuple[SearchSource, ...]:
        """Expand only currently readable, fully located parents of this result pool."""
        children = self.control_plane.authorize_chunks([item.chunk_id for item in sources], context)
        parent_ids = list(
            dict.fromkeys(
                item.parent_chunk_id for item in children.values() if item.parent_chunk_id
            )
        )
        parents = self.control_plane.authorize_chunks(parent_ids, context)
        accepted: dict[str, SearchSource] = {}
        for child in children.values():
            parent = parents.get(child.parent_chunk_id or "")
            if (
                parent is not None
                and parent.locator.get("source_spans")
                and parent.document_id == child.document_id
                and parent.document_version_id == child.document_version_id
                and self._currently_authorized(child, context)
                and self._currently_authorized(parent, context)
            ):
                accepted[parent.chunk_id] = _source(parent)
        return tuple(accepted.values())
