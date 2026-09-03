"""Local real retrieval plus explicit deterministic fakes for unit tests."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ragkb.domain.retrieval import AuthorizedChunk, IndexCandidate, SearchContext

_TERM_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+", re.UNICODE)


def analyze_terms(text: str) -> tuple[str, ...]:
    """Small deterministic analyzer that handles CJK characters and Latin terms."""

    normalized = text.casefold()
    base = [match.group(0) for match in _TERM_PATTERN.finditer(normalized)]
    cjk_bigrams = [
        left + right
        for left, right in zip(base, base[1:], strict=False)
        if len(left) == len(right) == 1
        and "\u3400" <= left <= "\u9fff"
        and "\u3400" <= right <= "\u9fff"
    ]
    return tuple(base + cjk_bigrams)


@dataclass(frozen=True)
class LocalIndexRecord:
    chunk_id: str
    document_version_id: str
    text: str
    vector: tuple[float, ...]
    parent_chunk_id: str | None = None


class LocalHybridIndex:
    """A query-driven BM25 and cosine index intended for local acceptance tests."""

    revision = "local-bm25-cosine-index:v1"

    def __init__(
        self,
        records: Sequence[LocalIndexRecord] = (),
        *,
        security_watermark: int = 0,
        bm25_k1: float = 1.5,
        bm25_b: float = 0.75,
    ) -> None:
        self._records: dict[str, LocalIndexRecord] = {item.chunk_id: item for item in records}
        self._watermark = security_watermark
        self._k1 = bm25_k1
        self._b = bm25_b

    def upsert(self, records: Sequence[LocalIndexRecord]) -> None:
        for record in records:
            self._records[record.chunk_id] = record

    def delete(self, chunk_ids: Sequence[str]) -> None:
        for chunk_id in chunk_ids:
            self._records.pop(chunk_id, None)

    def observed_security_watermark(self, context: SearchContext) -> int:
        del context
        return self._watermark

    @staticmethod
    def _candidate(
        record: LocalIndexRecord, channel: str, rank: int, score: float
    ) -> IndexCandidate:
        return IndexCandidate(
            chunk_id=record.chunk_id,
            document_version_id=record.document_version_id,
            parent_chunk_id=record.parent_chunk_id,
            channel="bm25" if channel == "bm25" else "dense",
            rank=rank,
            score=score,
        )

    def search_bm25(
        self, query: str, context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        del context
        query_terms = analyze_terms(query)
        if not query_terms or not self._records:
            return ()
        tokenized = {key: analyze_terms(item.text) for key, item in self._records.items()}
        document_count = len(tokenized)
        average_length = sum(map(len, tokenized.values())) / document_count or 1.0
        document_frequency: Counter[str] = Counter()
        for terms in tokenized.values():
            document_frequency.update(set(terms))
        scored: list[tuple[float, str]] = []
        for chunk_id, terms in tokenized.items():
            frequencies = Counter(terms)
            score = 0.0
            for term in query_terms:
                frequency = frequencies[term]
                if not frequency:
                    continue
                frequency_in_documents = document_frequency[term]
                inverse_frequency = math.log(
                    1.0
                    + (document_count - frequency_in_documents + 0.5)
                    / (frequency_in_documents + 0.5)
                )
                denominator = frequency + self._k1 * (
                    1.0 - self._b + self._b * len(terms) / average_length
                )
                score += inverse_frequency * frequency * (self._k1 + 1.0) / denominator
            if score > 0:
                scored.append((score, chunk_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(
            self._candidate(self._records[chunk_id], "bm25", rank, score)
            for rank, (score, chunk_id) in enumerate(scored[:limit], start=1)
        )

    def search_dense(
        self, vector: Sequence[float], context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        del context
        query = tuple(float(value) for value in vector)
        query_norm = math.sqrt(sum(value * value for value in query))
        if not query or query_norm == 0:
            return ()
        scored: list[tuple[float, str]] = []
        for chunk_id, record in self._records.items():
            if len(record.vector) != len(query):
                raise ValueError("local index vector dimension mismatch")
            record_norm = math.sqrt(sum(value * value for value in record.vector))
            if record_norm == 0:
                continue
            score = sum(left * right for left, right in zip(query, record.vector, strict=True))
            scored.append((score / (query_norm * record_norm), chunk_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(
            self._candidate(self._records[chunk_id], "dense", rank, score)
            for rank, (score, chunk_id) in enumerate(scored[:limit], start=1)
        )

    def search_hybrid(
        self,
        query: str,
        vector: Sequence[float],
        context: SearchContext,
        *,
        bm25_limit: int,
        dense_limit: int,
    ) -> tuple[Sequence[IndexCandidate], Sequence[IndexCandidate]]:
        return (
            self.search_bm25(query, context, bm25_limit),
            self.search_dense(vector, context, dense_limit),
        )


class FakeHybridIndex:
    """Predetermined candidates used only when a unit test needs a strict fake."""

    revision = "fake-hybrid-index:v1"

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
        del context
        return self._watermark

    def search_bm25(
        self, query: str, context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        del query, context
        return self._bm25[:limit]

    def search_dense(
        self, vector: Sequence[float], context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]:
        del vector, context
        return self._dense[:limit]


# Compatibility name for existing callers. New code should choose LocalHybridIndex or
# FakeHybridIndex explicitly so a predetermined fake cannot be mistaken for real retrieval.
InMemoryHybridIndex = FakeHybridIndex


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
