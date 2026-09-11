from dataclasses import replace
from types import SimpleNamespace

import pytest
from ragkb.adapters.zilliz import build_zilliz_filter
from ragkb.application.qa_performance import performance_report, performance_scope
from ragkb.application.reading_scope import ReadingOptions, reading_scope
from ragkb.contracts.rag import EvidenceSelection
from ragkb.domain.rag import AtomicClaim
from ragkb.domain.retrieval import SearchContext, SecurityWatermarkNotReady
from ragkb.domain.table_citations import attach_bound_citations
from ragkb.infrastructure.readable_scope import ReadableScope
from test_search_backed_qa import _components_with_search_qa


def context(**changes):
    return replace(SearchContext("tenant", ("space",), (), 1, 100, "generation", 1, 0), **changes)


def test_selected_documents_reach_vector_filter_with_escaped_ids():
    expression = build_zilliz_filter(context(document_ids=('a"b', "second")))
    assert 'document_id in ["a\\"b", "second"]' in expression
    assert 'tenant_id == "tenant"' in expression
    assert 'lifecycle_projection == "SERVING"' in expression


def test_empty_scope_never_calls_assessor_embedding_or_reranker(tmp_path):
    runtime = _components_with_search_qa(tmp_path)
    provider = runtime.qa_service.evidence_provider
    provider.readable_scope = lambda c: False

    def forbidden(*args, **kwargs):
        pytest.fail("No model request is needed for a proven empty scope")

    provider.question_assessor.assess = forbidden
    runtime.search_service.embedding.embed = forbidden
    with performance_scope():
        result = provider.build_package("some question", runtime.tenant_id, "local-admin")
        events = performance_report()["events"]
    assert not result.evidence and result.coverage == "missing"
    assert result.disposition_reason == "NO_READABLE_CURRENT_SOURCES"
    assert any(e["kind"] == "reading_scope" and e["outcome"] == "empty" for e in events)


def test_empty_scope_still_requires_security_watermark(tmp_path):
    runtime = _components_with_search_qa(tmp_path)
    provider = runtime.qa_service.evidence_provider
    provider.readable_scope = lambda c: False
    provider.required_security_watermark = lambda: 10
    with pytest.raises(SecurityWatermarkNotReady):
        provider.build_package("question", runtime.tenant_id, "local-admin")


def test_scope_lookup_error_is_not_insufficient_evidence(tmp_path):
    runtime = _components_with_search_qa(tmp_path)
    provider = runtime.qa_service.evidence_provider

    def unavailable(c):
        raise OSError("control database unavailable")

    provider.readable_scope = unavailable
    with pytest.raises(OSError):
        provider.build_package("question", runtime.tenant_id, "local-admin")


def test_different_selected_file_is_filtered_before_reranker(tmp_path):
    runtime = _components_with_search_qa(tmp_path)
    provider = runtime.qa_service.evidence_provider
    runtime.search_service.reranker.rerank = lambda *a: pytest.fail("Out-of-scope rerank")
    with reading_scope(ReadingOptions(document_ids=("another-file",))):
        result = provider.build_package("保修期多久？", runtime.tenant_id, "local-admin")
    assert not result.evidence


def test_identical_selection_input_reused_but_conflict_pool_retained(tmp_path):
    runtime = _components_with_search_qa(tmp_path)
    provider = runtime.qa_service.evidence_provider
    calls = []

    def select(question, evidence):
        calls.append(evidence)
        return EvidenceSelection((), "missing", ("保修期限", "设备保修"))

    provider.evidence_selector = SimpleNamespace(select=select)
    result = provider.build_package(
        "保修期多久？", runtime.tenant_id, "local-admin", clearance_level=3
    )
    assert len(calls) == 1
    assert len(result.retrieval_queries) == 2  # Simple profile stops on unchanged evidence.
    assert result.evidence and all(e.source_role == "conflict_context" for e in result.evidence)
    provider.build_package("保修期多久？", runtime.tenant_id, "local-admin", clearance_level=3)
    assert len(calls) == 2  # This reuse never crosses a QA request boundary.


def test_changed_visual_metadata_invalidates_selection_reuse(tmp_path):
    runtime = _components_with_search_qa(tmp_path)
    provider = runtime.qa_service.evidence_provider
    selections, reviews = [], []

    def select(question, evidence):
        selections.append(1)
        return EvidenceSelection((), "missing", ("另一种检索表达",))

    def enrich(question, evidence):
        reviews.append(1)
        evidence[0].locator["review_revision"] = len(reviews)
        return evidence

    provider.visual_enricher = enrich
    provider.evidence_selector = SimpleNamespace(select=select)
    provider.build_package("保修期多久？", runtime.tenant_id, "local-admin", clearance_level=3)
    assert len(selections) == 2


def test_scope_scan_limit_is_unknown_not_empty():
    row = {"document_id": "outside", "version_id": "v"}
    repo = SimpleNamespace(
        list_documents_page=lambda *a, **k: SimpleNamespace(items=[row] * 50, next_key="next")
    )
    assert ReadableScope(repo, None)(context(document_ids=("selected",))) is None


def test_filtered_empty_pages_cannot_make_scope_probe_unbounded():
    pages = []

    def page(*args, **kwargs):
        pages.append(1)
        return SimpleNamespace(items=[], next_key="next")

    repo = SimpleNamespace(list_documents_page=page)
    assert ReadableScope(repo, None)(context()) is None
    assert len(pages) == 20


def test_scope_scan_requires_authorized_current_chunks():
    repo = SimpleNamespace(
        list_documents_page=lambda *a, **k: SimpleNamespace(
            items=[{"document_id": "d", "version_id": "v"}], next_key=None
        )
    )
    auth = SimpleNamespace(
        lifecycle=SimpleNamespace(
            reload=lambda: None, documents={"d": SimpleNamespace(acl_revision=2)}
        ),
        has_readable_chunks=lambda *a, **k: False,
    )
    probe = ReadableScope(repo, auth)
    assert probe(context()) is False
    auth.has_readable_chunks = lambda *a, **k: True
    assert probe(context()) is True


def test_exact_citation_binding_keeps_all_source_ids_and_text():
    text = "标准按每人每晚计费。\n\n| 地区 | 上限 |\n| --- | --- |\n| 甲 | 600 |\n"
    claims = (
        AtomicClaim("各地区每人每晚上限", ("E1", "E2")),
        AtomicClaim("甲地区上限600", ("E3",)),
    )
    answer = attach_bound_citations(text, claims, ("标准按每人每晚计费。", "| 甲 | 600 |"))
    assert answer.startswith("标准按每人每晚计费。 [E1][E2]")
    assert "| 甲 | 600  [E3]|" in answer
    assert "| 地区 | 上限 |\n| --- | --- |" in answer


@pytest.mark.parametrize("quote", ["每人每晚", "标准按每人每晚计费", "标准按每人每月计费。", ""])
def test_partial_or_changed_witness_never_guesses_citation(quote):
    answer = "标准按每人每晚计费。"
    assert attach_bound_citations(answer, (AtomicClaim("按夜计费", ("E1",)),), (quote,)) == answer


def test_exact_claim_fallback_does_not_copy_neighbor_source():
    answer = "保修三年。\n容量600。"
    assert attach_bound_citations(answer, (AtomicClaim("保修三年。", ("E1",)),), ("",)) == (
        "保修三年。 [E1]\n容量600。"
    )
