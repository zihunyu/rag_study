from dataclasses import replace

from ragkb.adapters.retrieval_memory import InMemoryHybridIndex, InMemoryRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.application.query_planning import merge_channel, plan_queries
from ragkb.application.search import HybridSearchService
from ragkb.domain.retrieval import IndexCandidate
from test_hybrid_search import _chunk, _context


def test_facets_keep_entities_conditions_negation_and_original_question():
    text = "仅限2026年购买的甲型号，保修期多久？进水是否不保修？需要哪些凭证？"
    result = plan_queries(text, 3)
    assert len(result) == 3 and result[0] == text
    assert all(query.startswith(text) for query in result)
    assert len(set(result)) == len(result)


def test_simple_quoted_and_nested_questions_are_not_split():
    for text in ("保修多久？", "“保修；例外”是什么意思？", "适用条件（国内；2026年）是什么？"):
        assert plan_queries(text) == (text,)


def test_comparison_has_separate_subject_facets_and_config_limit():
    text = "比较甲设备和乙设备的功率与保修期"
    assert len(plan_queries(text)) == 3
    assert plan_queries(text, 1) == (text,)


def test_multi_query_rrf_retains_distinct_ingestion_attempts_for_authorization():
    a = IndexCandidate("a", "v", None, "dense", 1, 0.9, "old")
    b = replace(a, vector_pk="new")
    merged = merge_channel([[a, a], [b]], 1)
    assert len(merged) == 2 and merged[0].score == merged[1].score


def test_facets_retrieve_complementary_evidence_before_one_rerank_and_keep_acl():
    class Index(InMemoryHybridIndex):
        def __init__(self):
            super().__init__(security_watermark=10)
            self.queries = []

        def search_bm25(self, query, context, limit):
            self.queries.append((query, context))
            key = "b" if query.endswith("检索重点：需要哪些凭证") else "a"
            return (
                IndexCandidate(key, f"version-{key}", None, "bm25", 1, 1.0),
                IndexCandidate("secret", "version-secret", None, "bm25", 2, 0.5),
            )

    class Reranker(DeterministicReranker):
        def __init__(self):
            self.calls = []

        def rerank(self, query, documents):
            self.calls.append((query, documents))
            return list(range(len(documents)))

    index, reranker = Index(), Reranker()
    service = HybridSearchService(
        DeterministicEmbedding(),
        index,
        InMemoryRetrievalControlPlane(
            {
                "a": _chunk("a", "保修三年", "a"),
                "b": _chunk("b", "需要发票", "b"),
                "secret": _chunk("secret", "秘密", "s", visibility="RESTRICTED", acl=("secret",)),
            }
        ),
        reranker,
        bm25_top_k=10,
        dense_top_k=10,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=5,
    )
    text = "保修期多久？需要哪些凭证？"
    context = _context()
    result = service.search(text, context)
    assert {h.chunk_id for h in result.hits} == {"a", "b"}
    assert len(reranker.calls) == 1 and reranker.calls[0][0] == text
    assert all(c is context for _, c in index.queries)
    assert "秘密" not in str(reranker.calls)
