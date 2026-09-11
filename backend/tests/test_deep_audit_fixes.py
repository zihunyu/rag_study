"""Boundary regressions from the backend audit; no real provider calls."""

import json
import sqlite3
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache
from ragkb.adapters.embedding_contracts import EmbeddingContractRegistry, embedding_contract
from ragkb.adapters.evidence_selection import ModelEvidenceSelector
from ragkb.adapters.model_http import (
    HttpxJsonTransport,
    OpenAICompatibleClaimVerifier,
    OpenAICompatibleEmbeddingAdapter,
)
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.adapters.reuse_ledger import SQLiteReuseLedger
from ragkb.adapters.vector_indexing import ZillizChunkIndexingSink
from ragkb.adapters.zilliz import MilvusHybridAdapter
from ragkb.application.evidence import SearchBackedEvidenceProvider
from ragkb.application.evidence_packing import pack_sources
from ragkb.application.qa_budget import Limits, QuestionBudget, current
from ragkb.application.reuse_statistics import task_usage
from ragkb.application.search import HybridSearchService
from ragkb.application.worker import LocalIngestionWorker
from ragkb.contracts.rag import EvidenceSelection
from ragkb.domain.errors import SchemaMismatch
from ragkb.domain.question_coverage import required_aspects
from ragkb.domain.retrieval import SearchContext, SearchHit, SearchResult
from ragkb.evaluation.rag_quality import (
    answer_agreement,
    answer_token_f1,
    evaluate_quality,
    retrieval_metrics,
    semantic_review_digest,
)
from ragkb.infrastructure.visual_ledger import VisualLedger
from test_directory_sync import setup as setup_fixture
from test_embedding_reuse import Transport, settings
from test_model_http_adapters import _MockTransport
from test_trusted_qa import _evidence, _service
from test_zilliz_adapter import _ReadOnlyClient

directory_setup = setup_fixture


def completion(value):
    return {"choices": [{"message": {"content": json.dumps(value)}}]}


def selection(ids, coverage="sufficient"):
    return {"source_ids": ids, "coverage": coverage, "queries": [], "clarification": None}


def test_selector_reserves_review_evidence_before_long_display_hits():
    sources = tuple(
        _evidence(evidence_id=f"E{i}", text=chr(0x4E00 + i) * 1900) for i in range(1, 9)
    )
    sources += (
        _evidence(
            evidence_id="E9", text="需要提供购买发票。" + "凭" * 650, source_role="conflict_context"
        ),
    )
    transport = _MockTransport(completion(selection(["E9"], "partial")))
    ModelEvidenceSelector(settings(), transport).select("保修多久？需要哪些凭证？", sources)
    request = json.loads(transport.calls[0]["payload"]["messages"][1]["content"])
    assert "E9" in {row["id"] for row in request["candidates"]}
    assert len(request["candidates"]) < len(sources)


def test_packer_preserves_required_group_and_never_slices_its_conditions():
    sources = tuple(
        _evidence(evidence_id=f"E{i}", text=chr(0x4E00 + i) * 1050) for i in range(1, 8)
    )
    sources += (_evidence(evidence_id="E8", text="需要发票，仅限原购买人。" + "凭" * 900),)
    packed = pack_sources(
        "保修多久？需要哪些凭证？",
        sources,
        token_limit=8000,
        cost=lambda e: len(e.text),
        aspect_sources=[{"evidence_ids": ["E1"]}, {"evidence_ids": ["E8"]}],
    )
    assert {"E1", "E8"} <= {e.evidence_id for e in packed}
    assert sum(len(e.text) for e in packed) <= 8000
    assert all(e in sources for e in packed)


def test_nine_independent_short_sources_are_accepted_without_more_http_calls():
    sources = tuple(
        _evidence(evidence_id=f"E{i}", text=f"第{i}种产品保修{i}年。") for i in range(1, 10)
    )
    transport = _MockTransport(completion(selection([e.evidence_id for e in sources])))
    result = ModelEvidenceSelector(settings(), transport).select(
        "；".join(f"第{i}种产品保修多久" for i in range(1, 10)), sources
    )
    assert len(result.source_ids) == 9 and len(transport.calls) == 1


def test_generation_packing_recomputes_coverage_after_dropping_selected_ids():
    question = "保修多久？需要哪些凭证？"
    texts = [chr(0x4E00 + i) * 1050 for i in range(1, 8)] + ["需要发票。" + "凭" * 900]
    hits = tuple(
        SearchHit(
            f"c{i}",
            f"d{i}",
            f"v{i}",
            text,
            {"page": 1},
            1,
            i,
            ("bm25",),
            display_text=text,
            retrieval_text=text,
        )
        for i, text in enumerate(texts, 1)
    )
    search = SimpleNamespace(
        revision="test",
        max_subqueries=4,
        search=lambda *args, **kwargs: SearchResult(hits, 10, retrieval_queries=(question,)),
        expand_parents=lambda *args: (),
    )
    selector = SimpleNamespace(
        select=lambda q, e: EvidenceSelection(
            tuple(row.evidence_id for row in e),
            "sufficient",
            aspect_sources=(
                {"aspect_id": "A1", "status": "supported", "evidence_ids": ["E1"]},
                {"aspect_id": "A2", "status": "supported", "evidence_ids": ["E8"]},
            ),
        )
    )
    provider = SearchBackedEvidenceProvider(
        search,
        space_id="space",
        active_generation_id="g",
        active_permission_revision=lambda: 1,
        required_security_watermark=lambda: 0,
        prompt_revision="p",
        model_revision="m",
        final_evidence_count=8,
        evidence_selector=selector,
    )
    package = provider.build_package(question, "tenant", "user")
    assert "E8" in {e.evidence_id for e in package.generation_evidence}
    assert package.coverage == "partial" and package.coverage_report["complete"] is False
    assert package.coverage_report["packing_omitted_evidence_ids"]


@pytest.mark.parametrize("failure", ["begin", "event", "finish", "report"])
def test_statistics_io_failure_is_isolated_and_explicit(tmp_path, failure):
    class Ledger(SQLiteReuseLedger):
        def begin(self, *args):
            if failure == "begin":
                raise sqlite3.OperationalError("locked")
            return super().begin(*args)

        def event(self, *args):
            if failure == "event":
                raise sqlite3.OperationalError("locked")
            return super().event(*args)

        def finish(self, *args):
            if failure == "finish":
                raise sqlite3.OperationalError("locked")
            return super().finish(*args)

        def report(self, *args):
            if failure == "report":
                raise sqlite3.OperationalError("locked")
            return super().report(*args)

    with task_usage(Ledger(tmp_path / "usage.db"), "qa") as usage:
        usage.record("embedding", {"generated_vectors": 1})
        usage.report()
    assert usage.report()["available"] is False


def test_successful_paid_response_settles_even_when_final_receipt_fails(tmp_path):
    class Ledger(SQLiteReuseLedger):
        def event(self, attempt, event, kind, data):
            if kind == "http" and data.get("pending") is False:
                raise sqlite3.OperationalError("locked")
            return super().event(attempt, event, kind, data)

    transport = HttpxJsonTransport(
        settings(model_account_limit_enabled=False, model_usage_enabled=False)
    )
    transport._client.close()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"usage": {"prompt_tokens": 2}})

    transport._client = httpx.Client(transport=httpx.MockTransport(handler))
    budget = QuestionBudget("standard", Limits(24, 200000, 32768, 300))
    token = current.set(budget)
    ledger = Ledger(tmp_path / "usage.db")
    try:
        with task_usage(ledger, "qa") as usage:
            response = transport._account_post(
                "https://example.invalid/embeddings",
                headers={},
                json={"input": ["q" * 500]},
                timeout=httpx.Timeout(5),
            )
            assert response.status_code == 200 and usage.report()["available"] is False
    finally:
        current.reset(token)
        transport._client.close()
    assert len(calls) == 1 and budget.input_tokens == 2 and budget.unknown_usage_calls == 0
    assert ledger.report("qa")["statistics_unavailable"] is True


def test_warm_query_bypasses_a_colliding_worker_lock(tmp_path):
    cache = SQLiteEmbeddingCache(tmp_path / "cache.db")
    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(llm_timeout_seconds=0.06), transport=transport, cache=cache
    )
    namespace, text = adapter._cache_namespace, "保修期限是多少"
    key = cache.key(text)
    stripe = cache.key(namespace + ":" + key)[:3]
    collision = next(
        f"worker-{i}"
        for i in range(200000)
        if cache.key(namespace + ":" + cache.key(f"worker-{i}"))[:3] == stripe
    )
    cache.put(namespace, {key: [1.0, 2.0]}, 2)
    ready, release = threading.Event(), threading.Event()

    def hold():
        with cache.lock(namespace, [cache.key(collision)], 5):
            ready.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert ready.wait(3)
        assert adapter.embed_query(text) == [1.0, 2.0]
    finally:
        release.set()
        thread.join(3)
    assert not transport.calls


def test_qa_result_survives_direct_finish_receipt_failure(tmp_path):
    class Ledger(SQLiteReuseLedger):
        def finish(self, *args):
            raise sqlite3.OperationalError("locked")

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))
    service.reuse_ledger = Ledger(tmp_path / "usage.db")
    result = service.ask("保修多久？", "tenant", "user")
    assert result.verified and result.answer
    assert result.coverage_report["reuse_statistics"]["available"] is False


def test_global_usage_failure_does_not_discard_successful_response():
    transport = HttpxJsonTransport(
        settings(model_account_limit_enabled=False, model_usage_enabled=False)
    )
    transport._client.close()
    transport._client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )

    def fail_usage(*args, **kwargs):
        raise sqlite3.OperationalError("locked")

    transport._usage = SimpleNamespace(usage=fail_usage)
    try:
        response = transport._account_post(
            "https://example.invalid/embeddings",
            headers={},
            json={"input": ["q"]},
            timeout=httpx.Timeout(5),
        )
        assert response.status_code == 200
    finally:
        transport._client.close()


def test_usage_writer_lock_has_a_short_wait_bound(tmp_path):
    ledger = VisualLedger(tmp_path / "visual.db")
    db = sqlite3.connect(ledger.path)
    try:
        db.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            ledger.usage(role="embedding", model="test", started=time.time(), outcome="200")
        assert time.monotonic() - started < 2
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("question", ["请给出设备的保修期限和申请材料", "保修多久？"])
def test_missing_coverage_receipt_is_never_presented_as_complete(tmp_path, question):
    class Provider(SyntheticEvidenceProvider):
        def build_package(self, *args, **kwargs):
            return replace(
                super().build_package(*args, **kwargs),
                coverage="sufficient",
                coverage_report={"complete": True},
            )

    service, _, _ = _service(
        tmp_path, Provider((_evidence(), _evidence(evidence_id="E2", text="申请材料为购买发票。")))
    )
    service.verifier = OpenAICompatibleClaimVerifier(
        settings(),
        transport=_MockTransport(
            completion(
                {
                    "verdicts": [{"verdict": "SUPPORTED", "reason_code": "ENTAILED"}],
                    "conflict_check": {"checked": True, "conflicting_evidence_ids": []},
                }
            )
        ),
    )
    result = service.ask(question, "tenant-1", "user-1")
    assert result.verified
    assert result.coverage_report["complete"] is False
    assert result.coverage_report["answer_scope"] == "partial"
    assert "REQUIRED_ASPECTS_INCOMPLETE" in result.warnings


def test_chinese_parallel_attributes_keep_their_subject():
    assert [r["question"] for r in required_aspects("请给出设备的保修期限和申请材料")] == [
        "设备的保修期限",
        "设备的申请材料",
    ]


def test_completed_ingestion_outlives_queue_retention(directory_setup, monkeypatch):
    runtime, root, sync = directory_setup
    (root / "a.txt").write_text("保修三年，需要发票。", encoding="utf-8")
    first = sync.run(root, runtime.space_id, apply=True)["results"][0]
    worker = LocalIngestionWorker(
        runtime.queue,
        runtime.repository,
        runtime.storage,
        runtime.parser_router,
        "audit",
        chunker=runtime.chunker,
        indexing_sink=runtime.indexing_sink,
    )
    assert worker.run_once()
    assert runtime.repository.ingestion_complete(first["document_version_id"])
    monkeypatch.setattr(runtime.queue, "get", lambda key: None)
    assert sync.run(root, runtime.space_id)["reused"] == 1
    assert sync.run(root, runtime.space_id, apply=True)["reused"] == 1
    assert len(runtime.repository.get_versions(first["document_id"])) == 1
    runtime.repository.mark_version_failed(first["document_version_id"], "test")
    assert sync.run(root, runtime.space_id)["failed"] == 1


def test_model_identity_is_immutable_even_at_same_dimension(tmp_path):
    registry = EmbeddingContractRegistry(path=tmp_path / "contracts.db")
    original = settings(embedding_dimension=1024)
    registry.bind(original, "g1", provenance="verified-index-job")
    changed = original.model_copy(update={"embedding_model": "another"})
    with pytest.raises(SchemaMismatch, match="MISMATCH"):
        registry.require(changed, "g1")
    with pytest.raises(SchemaMismatch, match="MISMATCH"):
        registry.bind(changed, "g1", provenance="overwrite-attempt")
    assert registry.require(original, "g1")["contract"] == embedding_contract(original)


@pytest.mark.parametrize("entry", ["search", "ingestion"])
def test_model_mismatch_rejected_before_any_embedding_call(tmp_path, entry):
    registry = EmbeddingContractRegistry(path=tmp_path / "contracts.db")
    original = settings()
    registry.bind(original, "g1", provenance="fixture")
    changed = original.model_copy(update={"embedding_model": "other-model"})
    adapter = MilvusHybridAdapter(changed, embedding_contracts=registry)

    def unexpected(*args, **kwargs):
        pytest.fail("Embedding or vector retrieval must not start after a model mismatch")

    embedding = SimpleNamespace(embed=unexpected, embed_query=unexpected, embed_queries=unexpected)
    if entry == "ingestion":
        sink = ZillizChunkIndexingSink(adapter, None, embedding, changed, generation_id="g1")
        with pytest.raises(SchemaMismatch, match="MISMATCH"):
            sink.index(SimpleNamespace(chunks=()), document_id="d", tenant_id="t", space_id="s")
    else:
        index = SimpleNamespace(
            observed_security_watermark=lambda ctx: 0,
            validate_embedding_contract=adapter.validate_embedding_contract,
            search_bm25=unexpected,
            search_dense=unexpected,
        )
        service = HybridSearchService(
            embedding,
            index,
            None,
            None,
            bm25_top_k=10,
            dense_top_k=10,
            rrf_k=60,
            rerank_top_k=10,
            final_evidence_count=8,
        )
        with pytest.raises(SchemaMismatch, match="MISMATCH"):
            service.search("保修多久", SearchContext("t", ("s",), (), 0, 0, "g1", 1, 0))


def test_unregistered_nonempty_index_is_not_automatically_attested(tmp_path):
    registry = EmbeddingContractRegistry(path=tmp_path / "contracts.db")
    client = SimpleNamespace(query=lambda **kwargs: [{"zilliz_pk": "old-vector"}])
    adapter = MilvusHybridAdapter(
        settings(), client_factory=lambda **kwargs: client, embedding_contracts=registry
    )
    with pytest.raises(SchemaMismatch, match="UNREGISTERED"):
        adapter.prepare_generation("old")
    with pytest.raises(SchemaMismatch, match="UNREGISTERED"):
        adapter.validate_embedding_contract("old")


def test_empty_generation_binds_before_any_embedding(tmp_path):
    registry = EmbeddingContractRegistry(path=tmp_path / "contracts.db")
    adapter = MilvusHybridAdapter(
        settings(),
        client_factory=lambda **kwargs: SimpleNamespace(query=lambda **kwargs: []),
        embedding_contracts=registry,
    )
    adapter.prepare_generation("new")
    assert (
        registry.require(settings(), "new")["provenance"]
        == "empty-generation-before-first-embedding"
    )


def test_readiness_rejects_same_dimension_model_change(tmp_path):
    registry = EmbeddingContractRegistry(path=tmp_path / "contracts.db")
    original = settings(embedding_dimension=1024)
    registry.bind(original, original.retrieval_active_generation_id, provenance="fixture")
    changed = original.model_copy(update={"embedding_model": "different"})
    adapter = MilvusHybridAdapter(
        changed, client_factory=lambda **kwargs: _ReadOnlyClient(), embedding_contracts=registry
    )
    report = adapter.read_only_inspect()
    assert report["dense_dimension_matches"] is True
    assert report["embedding_contract_compatible"] is False and report["schema_compatible"] is False


def test_existing_cache_namespace_is_preserved_when_no_model_revision_is_added():
    cfg = settings()
    expected = SQLiteEmbeddingCache.key(
        json.dumps(
            {
                "endpoint": cfg.embedding_base_url.rstrip("/"),
                "model": cfg.embedding_model,
                "dimension": cfg.embedding_dimension,
                "normalize": cfg.embedding_normalize,
                "input_contract": "exact-utf8-provider-output-v1",
                "revision": cfg.embedding_cache_revision,
            },
            sort_keys=True,
        )
    )
    assert OpenAICompatibleEmbeddingAdapter(cfg, transport=Transport())._cache_namespace == expected


def test_duplicate_ndcg_is_bounded_and_preserves_unique_ranking():
    result = retrieval_metrics([{"E1"}], [["E1"] * 8], k=8)
    assert result.ndcg_at_k == 1 and result.precision_at_k == 1


def test_swapped_numeric_bindings_fail_even_with_perfect_lexical_scores():
    gold = "甲产品保修一年，乙产品保修三年。"
    wrong = "甲产品保修三年，乙产品保修一年。"
    assert answer_token_f1(gold, wrong) == 1
    report = evaluate_quality(
        [
            {
                "answerable": True,
                "expected_answer": gold,
                "actual_answer": wrong,
                "relevant_chunk_ids": ["E1", "E2"],
                "actual_citation_chunk_ids": ["E1", "E2"],
                "retrieved_chunk_ids": ["E1", "E2"],
            }
        ],
        k=8,
        thresholds={"answer_token_f1": 0.8},
    )
    assert report["metric_gate_passed"] is True and report["passed"] is False
    assert report["semantic_assessment"]["cases"][0]["basis"] == "numeric_binding_mismatch"


def test_semantic_review_is_required_for_paraphrases_and_bound_to_response():
    case = {
        "answerable": True,
        "expected_answer": "申请需要发票。",
        "actual_answer": "办理时请携带购买凭证。",
    }
    assert answer_agreement(case)["status"] == "unreviewed"
    case["semantic_review"] = {
        "digest": semantic_review_digest(case),
        "reviewer_id": "business-reviewer",
        "reviewed_at": "2026-09-11",
        "correct": True,
        "complete": True,
        "citations_supported": True,
    }
    assert answer_agreement(case)["status"] == "passed"
    case["actual_answer"] = "无需材料。"
    assert answer_agreement(case)["status"] == "unreviewed"
