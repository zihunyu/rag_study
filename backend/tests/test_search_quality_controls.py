from __future__ import annotations

import threading

from ragkb.adapters.retrieval_memory import FakeHybridIndex, InMemoryRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.application.search import HybridSearchService, classify_query, near_duplicate, rrf_fuse
from ragkb.domain.retrieval import IndexCandidate, SearchContext


def _candidate(chunk_id: str, channel: str, rank: int) -> IndexCandidate:
    return IndexCandidate(chunk_id, "version", None, channel, rank, 1.0)


def test_identifier_queries_and_weighted_rrf_prefer_exact_channel() -> None:
    bm25 = (_candidate("exact", "bm25", 1),)
    dense = (_candidate("semantic", "dense", 1),)

    result = rrf_fuse((bm25, dense), rrf_k=60, channel_weights={"bm25": 2.0, "dense": 1.0})

    assert classify_query("ThinkPad P16-21FA") == "identifier"
    assert result[0][0].chunk_id == "exact"


def test_near_duplicate_normalizes_unicode_spacing_and_punctuation() -> None:
    assert near_duplicate("Employee hotel limit: 600.", "employee hotel limit 600", threshold=0.8)


def test_fake_index_is_named_as_a_fake() -> None:
    assert FakeHybridIndex.revision.startswith("fake-")


def test_independent_bm25_and_dense_requests_run_concurrently() -> None:
    barrier = threading.Barrier(2)

    class _ConcurrentIndex:
        revision = "concurrency-test"

        def observed_security_watermark(self, context):
            return 0

        def search_bm25(self, query, context, limit):
            barrier.wait(timeout=1)
            return ()

        def search_dense(self, vector, context, limit):
            barrier.wait(timeout=1)
            return ()

    service = HybridSearchService(
        DeterministicEmbedding(),
        _ConcurrentIndex(),
        InMemoryRetrievalControlPlane(),
        DeterministicReranker(),
        bm25_top_k=2,
        dense_top_k=2,
        rrf_k=60,
        rerank_top_k=2,
        final_evidence_count=2,
    )
    context = SearchContext("tenant", ("space",), (), 1, 1, "generation", 1, 0)

    assert service.search("并发查询", context).hits == ()
