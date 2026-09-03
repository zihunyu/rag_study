from __future__ import annotations

from ragkb.adapters.retrieval_memory import LocalHybridIndex, LocalIndexRecord
from ragkb.domain.retrieval import SearchContext


def _context() -> SearchContext:
    return SearchContext("tenant", ("space",), (), 1, 1, "generation", 1, 0)


def test_local_index_computes_bm25_from_each_query() -> None:
    index = LocalHybridIndex(
        (
            LocalIndexRecord("apple", "v1", "苹果公司首席执行官", (1.0, 0.0)),
            LocalIndexRecord("banana", "v2", "香蕉是一种热带水果", (0.0, 1.0)),
        )
    )

    assert index.search_bm25("苹果公司", _context(), 2)[0].chunk_id == "apple"
    assert index.search_bm25("香蕉水果", _context(), 2)[0].chunk_id == "banana"


def test_local_index_computes_cosine_similarity_and_checks_dimension() -> None:
    index = LocalHybridIndex(
        (
            LocalIndexRecord("x", "v1", "x", (1.0, 0.0)),
            LocalIndexRecord("y", "v2", "y", (0.0, 1.0)),
        )
    )

    assert index.search_dense((0.1, 0.9), _context(), 2)[0].chunk_id == "y"
