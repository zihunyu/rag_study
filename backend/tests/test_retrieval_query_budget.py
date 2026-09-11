"""The initial plan and evidence supplements share one per-answer query budget."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from ragkb.adapters.retrieval_memory import InMemoryHybridIndex, InMemoryRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.application.evidence import SearchBackedEvidenceProvider
from ragkb.application.query_planning import plan_queries
from ragkb.application.search import HybridSearchService
from ragkb.contracts.rag import EvidenceSelection
from ragkb.domain.errors import ProviderUnavailable
from test_hybrid_search import _candidate, _chunk, _context

QUESTION = "保修多久？收费多少？需要什么凭证？"
SUPPLEMENTS = ("设备材料？设备颜色？设备重量？", "维修地点？联系电话？办理步骤？")


class RecordingIndex(InMemoryHybridIndex):
    def __init__(self):
        super().__init__(security_watermark=10)
        self.queries = []
        self.contexts = []

    def search_bm25(self, query, context, limit):
        self.queries.append(query)
        self.contexts.append(context)
        return (_candidate("warranty", "bm25", 1),)


class Selector:
    revision = "offline-partial"

    def __init__(self, queries=SUPPLEMENTS):
        self.queries = queries
        self.calls = 0

    def select(self, question, evidence):
        self.calls += 1
        return EvidenceSelection(tuple(e.evidence_id for e in evidence), "partial", self.queries)


class RecordingEmbedding(DeterministicEmbedding):
    def __init__(self):
        self.queries = []

    def embed(self, texts):
        self.queries.extend(texts)
        return super().embed(texts)


def services(*, maximum=4, planning=True, supplements=SUPPLEMENTS, index=None):
    index = index or RecordingIndex()
    search = HybridSearchService(
        RecordingEmbedding(),
        index,
        InMemoryRetrievalControlPlane({"warranty": _chunk("warranty", "保修三年", "w")}),
        DeterministicReranker(),
        bm25_top_k=50,
        dense_top_k=50,
        rrf_k=60,
        rerank_top_k=40,
        final_evidence_count=8,
        max_subqueries=maximum,
        query_planning_enabled=planning,
    )
    selector = Selector(supplements)
    provider = SearchBackedEvidenceProvider(
        search,
        space_id="space-1",
        active_generation_id="generation-1",
        active_permission_revision=lambda: 5,
        required_security_watermark=lambda: 10,
        prompt_revision="p",
        model_revision="m",
        final_evidence_count=8,
        evidence_selector=selector,
    )
    return index, search, provider, selector


def ask(provider, question=QUESTION):
    return provider.build_package(question, "tenant-1", "user", clearance_level=2)


@pytest.mark.parametrize("maximum", [1, 2, 4, 8])
def test_compound_initial_and_supplemental_queries_share_the_configured_limit(maximum):
    index, search, provider, _ = services(maximum=maximum)
    package = ask(provider)
    assert len(index.queries) == min(maximum, 3)
    assert search.embedding.queries == index.queries
    assert package.retrieval_queries == tuple(index.queries)
    assert package.coverage == "partial" and package.evidence
    assert all(context is index.contexts[0] for context in index.contexts)


def test_simple_initial_query_leaves_budget_for_a_compound_supplement():
    index, _, provider, _ = services()
    package = ask(provider, "保修多久？")
    assert index.queries == ["保修多久？", SUPPLEMENTS[0]]
    assert package.coverage_report["retrieval_budget"]["maximum"] == 2
    assert package.retrieval_queries == tuple(index.queries)


def test_already_executed_facet_does_not_repeat_or_consume_the_last_slot():
    question = "保修多久？需要什么凭证？"
    duplicate = "  " + plan_queries(question)[1] + "  "
    index, _, provider, _ = services(supplements=(duplicate, "维修地点在哪里？"))
    package = ask(provider, question)
    assert index.queries == [*plan_queries(question, 2), "维修地点在哪里？"]
    assert package.retrieval_queries == tuple(index.queries)


def test_disabling_planning_limits_the_whole_answer_to_one_query():
    index, _, provider, _ = services(planning=False)
    package = ask(provider)
    assert index.queries == [QUESTION]
    assert package.retrieval_queries == (QUESTION,)


def test_budget_is_reset_for_each_request_even_when_the_provider_is_shared():
    index, _, provider, _ = services()
    with ThreadPoolExecutor(max_workers=2) as pool:
        packages = list(pool.map(lambda _: ask(provider), range(2)))
    assert len(index.queries) == 6
    assert all(
        package.retrieval_queries == (*plan_queries(QUESTION, 2), SUPPLEMENTS[0])
        for package in packages
    )


def test_failed_dense_attempts_still_count_toward_the_query_limit():
    class UnavailableEmbedding(DeterministicEmbedding):
        def embed(self, texts):
            raise ProviderUnavailable("synthetic provider outage")

    index, search, provider, _ = services()
    search.embedding = UnavailableEmbedding()
    package = ask(provider)
    assert len(index.queries) == 3
    assert package.retrieval_queries == tuple(index.queries)
    assert "DENSE_RETRIEVAL_UNAVAILABLE" in package.retrieval_warnings


def test_standalone_search_reports_executed_queries_without_shared_request_state():
    index, search, _, _ = services()
    result = search.search(QUESTION, _context())
    assert result.retrieval_queries == tuple(index.queries) == plan_queries(QUESTION)


@pytest.mark.parametrize("skip", ["exhausted", "duplicate"])
def test_standalone_search_with_no_remaining_queries_does_not_call_models(skip):
    index, search, _, _ = services()
    result = search.search(
        QUESTION,
        _context(),
        query_limit=0 if skip == "exhausted" else 4,
        exclude_queries=plan_queries(QUESTION) if skip == "duplicate" else (),
    )
    assert result.retrieval_queries == ()
    assert index.queries == search.embedding.queries == []


def test_native_hybrid_index_obeys_the_same_per_answer_budget():
    class NativeIndex(RecordingIndex):
        def search_hybrid(self, query, vector, context, *, bm25_limit, dense_limit):
            return self.search_bm25(query, context, bm25_limit), ()

    index, search, provider, selector = services(index=NativeIndex())
    package = ask(provider)
    assert len(index.queries) == len(search.embedding.queries) == 3
    assert package.retrieval_queries == tuple(index.queries)
    assert selector.calls == 1  # Exhaustion must not trigger another assessment.


def test_total_retrieval_outage_still_records_the_attempted_queries():
    class FailedIndex(RecordingIndex):
        def search_bm25(self, query, context, limit):
            super().search_bm25(query, context, limit)
            raise ProviderUnavailable("synthetic lexical outage")

        def search_dense(self, vector, context, limit):
            raise ProviderUnavailable("synthetic dense outage")

    index, _, provider, selector = services(index=FailedIndex())
    package = ask(provider)
    assert len(index.queries) == 2
    assert package.retrieval_queries == tuple(index.queries)
    assert not package.evidence and selector.calls == 0
