"""Offline regressions for protected facets, batch search and required coverage."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache
from ragkb.adapters.evidence_selection import ModelEvidenceSelector
from ragkb.adapters.model_http import (
    HttpxJsonTransport,
    OpenAICompatibleClaimVerifier,
    OpenAICompatibleEmbeddingAdapter,
)
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.adapters.retrieval_memory import InMemoryHybridIndex, InMemoryRetrievalControlPlane
from ragkb.adapters.reuse_ledger import SQLiteReuseLedger
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.adapters.zilliz import MilvusHybridAdapter
from ragkb.api.app import create_app
from ragkb.api.citation_projection import reader_report
from ragkb.application.qa_budget import Limits, QuestionBudget, current
from ragkb.application.reuse_statistics import current_usage, record, task_usage
from ragkb.application.search import HybridSearchService
from ragkb.application.worker import LocalIngestionWorker
from ragkb.domain.errors import InvalidProviderResponse, ProviderUnavailable, QABudgetExceeded
from ragkb.domain.question_coverage import coverage_report, required_aspects, validate_aspect_checks
from ragkb.domain.rag import AtomicClaim, ClaimVerdict, DraftAnswer, VerificationResult
from test_directory_sync import setup  # noqa: F401 -- shared isolated runtime fixture
from test_embedding_reuse import Transport, settings
from test_hybrid_search import _candidate, _chunk, _context
from test_model_http_adapters import _MockTransport
from test_trusted_qa import _evidence, _service
from test_workspace_redesign import stream_result
from test_workspace_redesign import workspace as workspace_fixture

workspace = workspace_fixture

QUESTION = "保修多久？需要哪些凭证？"


def search_service(index, embedding=None, cap=40, restricted=False):
    chunks = {
        f"common-{i}": _chunk(f"common-{i}", f"设备{i}的保修条款有效。", str(i)) for i in range(40)
    }
    chunks["invoice"] = _chunk(
        "invoice",
        "需要提供购买发票。",
        "invoice",
        visibility="RESTRICTED" if restricted else "TENANT",
        acl=("secret",),
    )
    return HybridSearchService(
        embedding or DeterministicEmbedding(),
        index,
        InMemoryRetrievalControlPlane(chunks),
        DeterministicReranker(),
        bm25_top_k=50,
        dense_top_k=50,
        rrf_k=60,
        rerank_top_k=cap,
        final_evidence_count=8,
        max_subqueries=4,
    )


class FacetIndex(InMemoryHybridIndex):
    def __init__(self):
        super().__init__(security_watermark=10)

    def search_bm25(self, query, context, limit):
        ids = [f"common-{i}" for i in range(40)]
        if query.endswith("检索重点：需要哪些凭证"):
            ids.append("invoice")  # Lowest rank, still the only proof for this facet.
        return tuple(_candidate(key, "bm25", i) for i, key in enumerate(ids, 1))[:limit]


@pytest.mark.parametrize("cap", [1, 3, 8, 40])
def test_exclusive_invoice_survives_even_at_low_rank(cap):
    service = search_service(FacetIndex(), cap=cap)
    result = service.search(QUESTION, _context())
    assert "invoice" in {row.chunk_id for row in result.review_sources}
    assert len(result.review_sources) <= cap


def test_reservation_never_promotes_unauthorized_evidence():
    result = search_service(FacetIndex(), restricted=True).search(QUESTION, _context())
    assert "invoice" not in {row.chunk_id for row in result.review_sources}


class BatchIndex(FacetIndex):
    def __init__(self, fail=""):
        super().__init__()
        self.sparse, self.dense, self.fail = [], [], fail

    def search_bm25_many(self, queries, context, limit):
        self.sparse.append((queries, context))
        if self.fail == "bm25":
            raise ProviderUnavailable("sparse unavailable")
        return [self.search_bm25(q, context, limit) for q in queries]

    def search_dense_many(self, vectors, context, limit):
        self.dense.append((vectors, context))
        if self.fail == "dense":
            raise ProviderUnavailable("dense unavailable")
        return [()] * len(vectors)


@pytest.mark.parametrize("fail", ["", "bm25", "dense"])
def test_batch_sends_one_embedding_and_one_call_per_channel(tmp_path, fail):
    transport = Transport()
    embedding = OpenAICompatibleEmbeddingAdapter(settings(), transport=transport)
    index = BatchIndex(fail)
    service = search_service(index, embedding)
    budget = QuestionBudget("standard", Limits(24, 200000, 32768, 300))
    token = current.set(budget)
    try:
        context = _context()
        result = service.search(QUESTION, context)
    finally:
        current.reset(token)
    assert len(result.retrieval_queries) == len(budget.queries) == 3
    assert len(transport.calls) == len(index.sparse) == len(index.dense) == 1
    assert transport.calls[0] == list(result.retrieval_queries)
    assert index.sparse[0][1] is index.dense[0][1] is context
    assert result.degraded == bool(fail)


def test_batch_quota_is_atomic_and_rejects_before_embedding():
    transport = Transport()
    service = search_service(
        BatchIndex(), OpenAICompatibleEmbeddingAdapter(settings(), transport=transport)
    )
    budget = QuestionBudget("standard", Limits(24, 200000, 32768, 300), max_queries=2)
    token = current.set(budget)
    try:
        with pytest.raises(QABudgetExceeded):
            service.search(QUESTION, _context())
    finally:
        current.reset(token)
    assert not budget.queries and not transport.calls


def test_milvus_batch_preserves_order_and_security_filter():
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return [
            [{"entity": {"chunk_id": f"c{i}", "document_version_id": f"v{i}"}, "distance": 1}]
            for i in range(len(kwargs["data"]))
        ]

    adapter = MilvusHybridAdapter(
        settings(), client_factory=lambda **kwargs: SimpleNamespace(search=search)
    )
    for result in (
        adapter.search_bm25_many(["a", "b"], _context(), 5),
        adapter.search_dense_many([[1, 0], [0, 1]], _context(), 5),
    ):
        assert [row[0].chunk_id for row in result] == ["c0", "c1"]
    assert len(calls) == 2 and calls[0]["filter"] == calls[1]["filter"]
    assert "tenant-1" in calls[0]["filter"]


@pytest.mark.parametrize("bad", [[], [[]], "xx", [{}, {}]])
def test_milvus_batch_rejects_bad_result_groups(bad):
    with pytest.raises(ValueError, match="BATCH"):
        MilvusHybridAdapter._candidate_groups(bad, 2, "dense")


def test_query_batch_uses_query_cache_flag(tmp_path):
    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(query_embedding_cache_enabled=False),
        transport=transport,
        cache=SQLiteEmbeddingCache(tmp_path / "cache.db"),
    )
    adapter.embed(["a", "b"])
    adapter.embed_queries(["a", "b"])
    assert transport.calls == [["a", "b"], ["a", "b"]]


def draft_and_checks():
    draft = DraftAnswer("保修三年。[E1]", ("E1",), (AtomicClaim("保修三年。", ("E1",)),))
    checks = [
        {
            "aspect_id": "A1",
            "status": "answered",
            "evidence_ids": ["E1"],
            "claim_ids": ["C1"],
            "answer_quote": "保修三年。",
        },
        {
            "aspect_id": "A2",
            "status": "answer_missing",
            "evidence_ids": ["E2"],
            "claim_ids": [],
            "answer_quote": "",
        },
    ]
    evidence = (_evidence(text="保修三年。"), _evidence(evidence_id="E2", text="需要发票。"))
    verdicts = (ClaimVerdict("保修三年。", ("E1",), "SUPPORTED", "OK"),)
    return draft, checks, evidence, verdicts


def test_answer_omission_has_explicit_missing_row_and_no_false_complete(tmp_path):
    draft, checks, evidence, verdicts = draft_and_checks()
    validated = validate_aspect_checks(QUESTION, checks, draft, evidence, verdicts)
    report = coverage_report(QUESTION, validated, [], draft.text, {"E1"})
    assert report["answered"] == 1 and report["total"] == 2 and not report["complete"]
    assert report["items"][1]["answer_status"] == "answer_missing"
    service, repo, _ = _service(tmp_path, SyntheticEvidenceProvider(evidence))
    package = service.evidence_provider.build_package(QUESTION, "tenant-1", "user-1")
    from ragkb.domain.rag import AnswerStatus, Citation

    result = service._save(
        package,
        AnswerStatus.ANSWERED,
        answer=draft.text,
        citations=(Citation("E1", "source", {}),),
        verified=True,
        aspect_checks=validated,
    )
    assert result.coverage == "partial" and result.verified
    assert repo.get_result(result.rag_run_id).coverage_report["required_aspects"] == report
    public = reader_report(result.coverage_report)
    assert public["required_aspects"]["items"][1]["evidence_ids"] == []


@pytest.mark.parametrize(
    "change", ["source", "claim", "quote", "duplicate", "missing", "unsupported"]
)
def test_coverage_rejects_fabricated_or_incomplete_receipts(change):
    draft, checks, evidence, verdicts = draft_and_checks()
    if change == "source":
        checks[0]["evidence_ids"] = ["E2"]
    if change == "claim":
        checks[0]["claim_ids"] = ["C9"]
    if change == "quote":
        checks[0]["answer_quote"] = "五年"
    if change == "duplicate":
        checks[1]["aspect_id"] = "A1"
    if change == "missing":
        checks.pop()
    if change == "unsupported":
        verdicts = (replace(verdicts[0], verdict="INSUFFICIENT"),)
    with pytest.raises(InvalidProviderResponse):
        validate_aspect_checks(QUESTION, checks, draft, evidence, verdicts)


def test_rebuilt_answer_invalidates_old_coverage_and_legacy_is_unchecked():
    draft, checks, evidence, verdicts = draft_and_checks()
    checked = validate_aspect_checks(QUESTION, checks, draft, evidence, verdicts)
    report = coverage_report(QUESTION, checked, [], "需要发票。", {"E2"})
    assert report["items"][0]["answer_status"] == "unchecked"
    assert not coverage_report(QUESTION, (), [], draft.text, {"E1"})["complete"]


def test_required_items_are_not_capped_by_retrieval_budget():
    rows = required_aspects("费用多少？保修多久？需要发票吗？去哪里办理？如何申请？")
    assert len(rows) == 5
    many = required_aspects("？".join(f"第{i}项是什么" for i in range(40)))
    assert len(many) == 32 and "第39项" in many[-1]["question"]


def test_selector_maps_sources_and_downgrades_false_sufficiency():
    value = {
        "source_ids": ["E1"],
        "coverage": "sufficient",
        "queries": [],
        "clarification": None,
        "aspect_sources": [
            {"aspect_id": "A1", "status": "supported", "evidence_ids": ["E1"]},
            {"aspect_id": "A2", "status": "missing", "evidence_ids": []},
        ],
    }
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(value)}}]})
    result = ModelEvidenceSelector(settings(), transport).select(QUESTION, (_evidence(),))
    assert result.coverage == "partial" and len(result.aspect_sources) == 2
    assert "required_aspects" in transport.calls[0]["payload"]["messages"][1]["content"]


def test_statistics_survive_failed_batch_retry_and_restart(tmp_path):
    ledger = SQLiteReuseLedger(tmp_path / "usage.db")
    transport = Transport()
    transport.fail_on = 2
    cache = SQLiteEmbeddingCache(tmp_path / "cache.db")
    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(embedding_batch_size=1), transport=transport, cache=cache
    )
    with pytest.raises(RuntimeError), task_usage(ledger, "job", kind="ingestion", tenant_id="t"):
        adapter.embed(["a", "bb"])
    reopened = SQLiteReuseLedger(ledger.path)
    assert reopened.report("job")["generated_vectors"] == 1
    with task_usage(reopened, "job", kind="ingestion", tenant_id="t"):
        adapter.embed(["a", "bb", "bb"])
    result = reopened.report("job")
    assert result["attempt_count"] == 2 and result["generated_vectors"] == 2
    assert result["cache_reused_vectors"] == result["duplicate_reused_vectors"] == 1
    assert result["http_attempts"] == 0  # Mock adapter bypasses HTTP; do not fabricate calls.
    assert result["attempts"][0]["state"] == "failed"


def test_statistics_are_isolated_between_concurrent_tasks(tmp_path):
    ledger = SQLiteReuseLedger(tmp_path / "usage.db")

    def run(i):
        with task_usage(ledger, f"task-{i}", tenant_id=f"tenant-{i}"):
            record("embedding", generated_vectors=i)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, range(12)))
    assert [ledger.report(f"task-{i}")["generated_vectors"] for i in range(12)] == list(range(12))
    assert current_usage.get() is None and ledger.report("old")["available"] is False
    with pytest.raises(ValueError), task_usage(ledger, "task-1", tenant_id="another"):
        pass


def test_http_retry_and_unknown_usage_are_accounted_at_transport_boundary(tmp_path):
    ledger = SQLiteReuseLedger(tmp_path / "usage.db")
    transport = HttpxJsonTransport(
        settings(model_account_limit_enabled=False, model_usage_enabled=False)
    )
    responses = [httpx.Response(429), httpx.Response(200, json={"usage": {"prompt_tokens": 3}})]
    transport._client.close()
    transport._client = httpx.Client(
        transport=httpx.MockTransport(lambda request: responses.pop(0))
    )
    with task_usage(ledger, "task"):
        for _ in range(2):
            transport._account_post(
                "https://example.invalid/embeddings",
                headers={},
                json={"input": ["a"]},
                timeout=httpx.Timeout(5),
            )
    transport.close()
    result = ledger.report("task")
    assert result["http_attempts"] == result["embedding_http_attempts"] == 2
    assert result["failed_http_attempts"] == result["unknown_usage_attempts"] == 1
    assert result["input_tokens"] == 3


def test_interrupted_dispatch_is_reported_as_unknown_not_success(tmp_path):
    ledger = SQLiteReuseLedger(tmp_path / "usage.db")
    ledger.begin("task", "attempt", {})
    ledger.event("attempt", "http", "http", {"pending": True})
    result = SQLiteReuseLedger(ledger.path).report("task")
    assert result["unknown_http_outcomes"] == 1 and result["http_attempts"] == 0
    assert result["attempts"][0]["state"] == "running"


def test_worker_statistics_remain_readable_after_queue_retention(setup, monkeypatch):  # noqa: F811
    runtime, root, sync = setup
    (root / "manual.txt").write_text("保修三年，需要发票。", encoding="utf-8")
    job_id = sync.run(root, runtime.space_id, apply=True)["results"][0]["job_id"]
    original = runtime.indexing_sink.index

    def index(*args, **kwargs):
        record("embedding", cache_reused_vectors=4, generated_vectors=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.indexing_sink, "index", index)
    worker = LocalIngestionWorker(
        runtime.queue,
        runtime.repository,
        runtime.storage,
        runtime.parser_router,
        "test",
        chunker=runtime.chunker,
        indexing_sink=runtime.indexing_sink,
        reuse_ledger=runtime.reuse_ledger,
    )
    assert worker.run_once()
    monkeypatch.setattr(runtime.queue, "get", lambda identity: None)
    client = TestClient(create_app(runtime))
    response = client.get(f"/api/ingestion-jobs/{job_id}/reuse-statistics")
    assert response.status_code == 200
    assert response.json()["cache_reused_vectors"] == 4
    assert response.json()["attempts"][0]["state"] == "SUCCEEDED"
    assert client.get("/api/ingestion-jobs/unknown/reuse-statistics").status_code == 404


def test_qa_statistics_persist_with_run_and_reset_for_each_ask(tmp_path):
    service, repo, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))
    service.reuse_ledger = SQLiteReuseLedger(tmp_path / "usage.db")
    one = service.ask("保修多久？", "tenant-1", "u")
    two = service.ask("保修多久？", "tenant-1", "u")
    assert (
        one.coverage_report["reuse_statistics"]["task_id"]
        != two.coverage_report["reuse_statistics"]["task_id"]
    )
    saved = repo.get_result(two.rag_run_id).coverage_report["reuse_statistics"]
    assert saved["available"] and saved["http_attempts"] == 0
    assert saved["attempts"][0]["state"] == "answered"


def test_existing_verifier_call_checks_aspects_without_an_extra_request():
    draft, checks, evidence, _ = draft_and_checks()
    evidence = (evidence[0], replace(evidence[1], text="凭证：发票。"))
    body = {
        "verdicts": [{"claim_id": "C1", "verdict": "SUPPORTED", "reason_code": "OK"}],
        "conflict_check": {"checked": True, "conflicting_evidence_ids": []},
        "aspect_checks": checks,
    }
    transport = _MockTransport({"choices": [{"message": {"content": json.dumps(body)}}]})
    result = OpenAICompatibleClaimVerifier(settings(), transport=transport).verify(
        QUESTION, draft, evidence
    )
    assert len(transport.calls) == 1
    assert result.supported and result.aspect_checks[1]["status"] == "answer_missing"
    payload = json.loads(transport.calls[0]["payload"]["messages"][1]["content"])
    assert len(payload["required_aspects"]) == 2


def test_verified_result_cache_keeps_coverage_but_uses_new_statistics(tmp_path):
    from test_qa_speed_guards import cached_service

    service, _, _, _, _ = cached_service(tmp_path)
    service.reuse_ledger = SQLiteReuseLedger(tmp_path / "stats.db")

    def verify(question, draft, evidence):
        record("embedding", cache_reused_vectors=2)
        return VerificationResult(
            (ClaimVerdict(draft.claims[0].text, ("E1",), "SUPPORTED", "OK"),),
            "test",
            aspect_checks=(
                {
                    "aspect_id": "A1",
                    "status": "answered",
                    "evidence_ids": ["E1"],
                    "claim_ids": ["C1"],
                    "answer_quote": draft.text,
                },
                {
                    "aspect_id": "A2",
                    "status": "evidence_missing",
                    "evidence_ids": [],
                    "claim_ids": [],
                    "answer_quote": "",
                },
            ),
        )

    service.verifier = SimpleNamespace(verify=verify)
    first = service.ask(QUESTION, "tenant", "user")
    assert first.verified
    service.generator.fail = True
    second = service.ask(QUESTION, "tenant", "user")
    assert second.verified and second.coverage == "partial"
    assert second.coverage_report["required_aspects"] == first.coverage_report["required_aspects"]
    assert first.coverage_report["reuse_statistics"]["cache_reused_vectors"] == 2
    assert second.coverage_report["reuse_statistics"]["cache_reused_vectors"] == 0


def test_conversation_resolution_and_qa_share_one_task_receipt(workspace, monkeypatch):
    client, runtime, service, space = workspace
    original = service.resolver.resolve

    def resolve(question, history):
        record("embedding", cache_reused_vectors=3)
        return original(question, history)

    monkeypatch.setattr(service.resolver, "resolve", resolve)
    conversation = client.post(
        "/api/conversations", headers={"Idempotency-Key": "stats"}, json={"space_id": space}
    ).json()["id"]
    turn = stream_result(client, conversation, "保修期是多久？", "stats-turn")
    stats = turn["result"]["coverage_report"]["reuse_statistics"]
    assert stats["task_id"] == turn["id"] and stats["cache_reused_vectors"] == 3
    assert runtime.reuse_ledger.report(turn["id"])["attempt_count"] == 1


def test_reuse_endpoint_rejects_other_qa_owner(workspace):
    client, runtime, _, _ = workspace
    result = runtime.qa_service.ask("保修多久？", runtime.tenant_id, "another-user")
    assert client.get(f"/api/rag-runs/{result.rag_run_id}/reuse-statistics").status_code == 404


def test_reuse_endpoint_rejects_other_tenant_task(workspace):
    client, runtime, _, _ = workspace
    with task_usage(runtime.reuse_ledger, "foreign", kind="ingestion", tenant_id="another"):
        record("embedding", generated_vectors=99)
    response = client.get("/api/ingestion-jobs/foreign/reuse-statistics")
    assert response.status_code == 404 and "generated_vectors" not in response.json()


def test_batch_threads_preserve_task_statistics_context(tmp_path):
    ledger = SQLiteReuseLedger(tmp_path / "usage.db")
    service = search_service(
        BatchIndex(), OpenAICompatibleEmbeddingAdapter(settings(), transport=Transport())
    )
    with task_usage(ledger, "batch"):
        service.search(QUESTION, _context())
    assert ledger.report("batch")["generated_vectors"] == 3


def test_revoked_conversation_does_not_leak_answer_through_coverage(workspace, monkeypatch):
    from test_workspace_redesign import publish, upload

    client, runtime, _, space = workspace
    done = upload(client, runtime, space)
    publish(client, done["document_version_id"])
    original = runtime.qa_service.verifier.verify

    def verify(question, draft, evidence):
        checked = original(question, draft, evidence)
        return replace(
            checked,
            aspect_checks=(
                {
                    "aspect_id": "A1",
                    "status": "answered",
                    "evidence_ids": list(draft.citation_ids),
                    "claim_ids": ["C1"],
                    "answer_quote": "三年",
                },
            ),
        )

    monkeypatch.setattr(runtime.qa_service.verifier, "verify", verify)
    conversation = client.post(
        "/api/conversations", headers={"Idempotency-Key": "revoke-stats"}, json={"space_id": space}
    ).json()["id"]
    turn = stream_result(client, conversation, "星云 X1 保修多久？", "revoke-turn")
    assert (
        turn["result"]["coverage_report"]["required_aspects"]["items"][0]["answer_quote"] == "三年"
    )
    client.post(
        f"/api/documents/{done['document_id']}:revoke", headers={"Idempotency-Key": "revoke"}
    )
    result = client.get(f"/api/conversations/{conversation}/turns/{turn['id']}").json()["result"]
    assert result["sources_stale"] and result["answer"] is None
    assert "三年" not in json.dumps(result["coverage_report"], ensure_ascii=False)


def test_failed_conversation_context_still_saves_task_usage(workspace, monkeypatch):
    client, runtime, service, space = workspace

    def resolve(*args):
        record("embedding", generated_vectors=1)
        raise ProviderUnavailable("unavailable")

    monkeypatch.setattr(service.resolver, "resolve", resolve)
    conversation = client.post(
        "/api/conversations", headers={"Idempotency-Key": "failed-stats"}, json={"space_id": space}
    ).json()["id"]
    turn = stream_result(client, conversation, "保修多久？", "failed-turn")
    assert turn["state"] == "failed"
    assert turn["result"]["coverage_report"]["reuse_statistics"]["generated_vectors"] == 1
    assert runtime.reuse_ledger.report(turn["id"])["attempts"][0]["state"] == "failed"


def test_all_required_parts_can_be_confirmed_complete():
    first, checks, evidence, verdicts = draft_and_checks()
    draft = replace(
        first,
        text=first.text + "需要发票。[E2]",
        citation_ids=("E1", "E2"),
        claims=first.claims + (AtomicClaim("需要发票。", ("E2",)),),
    )
    verdicts += (ClaimVerdict("需要发票。", ("E2",), "SUPPORTED", "OK"),)
    checks[1].update(status="answered", claim_ids=["C2"], answer_quote="需要发票。")
    validated = validate_aspect_checks(QUESTION, checks, draft, evidence, verdicts)
    report = coverage_report(QUESTION, validated, [], draft.text, {"E1", "E2"})
    assert report["complete"] and report["answered"] == report["total"] == 2
    assert all(row["evidence_status"] == "supported" for row in report["items"])


def test_visual_plan_reuse_remaps_required_aspect_evidence_ids():
    from ragkb.contracts.rag import EvidenceSelection
    from ragkb.infrastructure.visual_evidence import VisualEvidenceSession

    session = VisualEvidenceSession(
        SimpleNamespace(analyzer=SimpleNamespace(revision="test")), QUESTION
    )
    session.text_selection_sources = (_evidence(),)
    session.text_selection = EvidenceSelection(
        ("E1",),
        aspect_sources=({"aspect_id": "A1", "status": "supported", "evidence_ids": ["E1"]},),
    )
    selected = session.preselected((_evidence(evidence_id="E8"),))
    assert selected.source_ids == ("E8",)
    assert selected.aspect_sources[0]["evidence_ids"] == ["E8"]
    assert session.text_selection.aspect_sources[0]["evidence_ids"] == ["E1"]
