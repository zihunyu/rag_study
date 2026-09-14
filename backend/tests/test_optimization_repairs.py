"""No network: cache failures, scoped reading, targeted completion and retention."""

import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from types import SimpleNamespace

import pytest
from ragkb.adapters.cache_access import EmbeddingCacheAccess
from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache
from ragkb.adapters.model_http import OpenAICompatibleEmbeddingAdapter
from ragkb.adapters.rag_stubs import SyntheticEvidenceProvider
from ragkb.adapters.reuse_ledger import SQLiteReuseLedger
from ragkb.application.aspect_repair import complete_aspects
from ragkb.application.overview_scope import search_documents
from ragkb.application.reading_scope import ReadingOptions, reading_scope
from ragkb.application.validation_scope import validate_once, validation_scope
from ragkb.contracts.rag import EvidenceSelection
from ragkb.domain.errors import InvalidProviderResponse, ProviderUnavailable, QABudgetExceeded
from ragkb.domain.question_coverage import question_aspects
from ragkb.domain.rag import AtomicClaim, ClaimVerdict, DraftAnswer, VerificationResult
from ragkb.domain.retrieval import SearchHit, SearchResult
from ragkb.infrastructure.file_fingerprint import fingerprint
from ragkb.infrastructure.overview import OverviewReader
from test_directory_sync import setup as setup_fixture
from test_embedding_reuse import Transport, settings
from test_trusted_qa import _evidence, _service

directory_setup = setup_fixture


class BrokenCache(SQLiteEmbeddingCache):
    broken = True

    def put(self, *args, **kwargs):
        if self.broken:
            raise sqlite3.OperationalError("simulated disk failure")
        return super().put(*args, **kwargs)


def test_paid_vectors_return_on_disk_failure_and_retry_without_provider(tmp_path):
    cache, transport = BrokenCache(tmp_path / "vectors.db"), Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(settings(), cache=cache, transport=transport)
    first = adapter.embed_queries(["short", "a little longer", "short"])
    assert adapter.embed_queries(["short", "a little longer", "short"]) == first
    assert len(transport.calls) == 1
    assert adapter.cache_stats()["persistence_failures"] >= 1
    cache.broken = False
    assert adapter.embed_query("short") == first[0]
    fresh = OpenAICompatibleEmbeddingAdapter(settings(), cache=cache, transport=transport)
    assert fresh.embed_query("short") == first[0]
    assert len(transport.calls) == 1


@pytest.mark.parametrize("operation", ["get_many", "lock"])
def test_read_or_lock_storage_failure_does_not_discard_result(tmp_path, monkeypatch, operation):
    cache, transport = SQLiteEmbeddingCache(tmp_path / "vectors.db"), Transport()

    def broken(*args, **kwargs):
        raise OSError("simulated auxiliary failure")

    if operation == "lock":
        from contextlib import contextmanager

        @contextmanager
        def lock(*args, **kwargs):
            broken()
            yield

        monkeypatch.setattr(cache, operation, lock)
    else:
        monkeypatch.setattr(cache, operation, broken)
    adapter = OpenAICompatibleEmbeddingAdapter(settings(), cache=cache, transport=transport)
    assert adapter.embed_query("hello") == [5.0, 1.0]


def test_cache_initialization_failure_is_retryable(tmp_path):
    parent = tmp_path / "blocked"
    parent.write_text("occupied")
    cache = SQLiteEmbeddingCache(parent / "vectors.db")
    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(settings(), cache=cache, transport=transport)
    assert adapter.embed_query("hello") == [5.0, 1.0]
    assert adapter.embed_query("hello") == [5.0, 1.0]
    assert len(transport.calls) == 1


def test_retry_buffer_has_size_and_time_bounds(tmp_path, monkeypatch):
    access = EmbeddingCacheAccess(BrokenCache(tmp_path / "cache.db"), max_bytes=350, ttl=10)
    clock = [100.0]
    monkeypatch.setattr("ragkb.adapters.cache_access.time.monotonic", lambda: clock[0])
    access.put("n", {"a": [1.0, 2.0], "b": [3.0, 4.0]}, 2)
    assert access.size <= 350 and len(access.memory) == 1
    clock[0] += 11
    assert access.get_many("n", ["a", "b"], 2) == {}
    assert access.size == 0


def test_cache_io_protection_does_not_hide_invalid_provider_result(tmp_path):
    class Wrong(Transport):
        def post_json(self, *args, **kwargs):
            return {"data": [{"index": 0, "embedding": [float("nan"), 1.0]}]}

    adapter = OpenAICompatibleEmbeddingAdapter(
        settings(), cache=BrokenCache(tmp_path / "cache.db"), transport=Wrong()
    )
    with pytest.raises(InvalidProviderResponse):
        adapter.embed_query("hello")


@pytest.mark.parametrize(
    "question",
    [
        "这款产品保修多久，申请需要哪些材料？",
        "保修多久,申请需要哪些材料?",
        "“黑与白”保修多久，申请需要哪些材料？",
        "设备保修多久和申请需要哪些材料？",
        "设备的保修期限和申请材料是什么？",
        "保修期限与申请材料分别是什么？",
        "请给出设备的保修期限和申请材料",
        "How long is the warranty, what documents are required?",
        "How long is the warranty and what documents are required?",
    ],
)
def test_parallel_questions_are_separate_requirements(question):
    assert len(question_aspects(question)) == 2


@pytest.mark.parametrize(
    "question",
    [
        "申请需同时满足A和B吗？",
        "同时提供发票和保修卡才能申请吗？",
        "如果产品仍在保修期，能否申请？",
        "“黑与白”的保修期是多少？",
        "设备的申请条件是同时提供发票和保修卡吗？",
    ],
)
def test_joint_conditions_and_quoted_names_remain_together(question):
    assert len(question_aspects(question)) == 1


QUESTION = "保修多久，申请需要哪些材料？"
OLD = DraftAnswer(
    "保修三年。[E1]", ("E1",), (AtomicClaim("保修三年。", ("E1",)),), synthesized=True
)
ADD = DraftAnswer(
    "申请需要发票。[E2]", ("E2",), (AtomicClaim("申请需要发票。", ("E2",)),), synthesized=True
)
SOURCES = (
    _evidence(text="保修三年。"),
    _evidence(evidence_id="E2", chunk_id="invoice", text="申请需要发票。"),
)


def receipt(draft, *, complete=False, conflict=False):
    return VerificationResult(
        tuple(ClaimVerdict(c.text, c.evidence_ids, "SUPPORTED", "verified") for c in draft.claims),
        "fixture",
        conflicting_evidence_ids=("E1", "E2") if conflict else (),
        aspect_checks=(
            {
                "aspect_id": "A1",
                "status": "answered",
                "evidence_ids": ["E1"],
                "claim_ids": ["C1"],
                "answer_quote": "保修三年。",
            },
            {
                "aspect_id": "A2",
                "status": "answered" if complete else "answer_missing",
                "evidence_ids": ["E2"],
                "claim_ids": ["C2"] if complete else [],
                "answer_quote": "申请需要发票。" if complete else "",
            },
        ),
    )


def test_completion_appends_only_gaps_then_verifies_full_answer_and_pool():
    seen = []

    def repair(question, draft, evidence, gaps):
        assert question == QUESTION and draft == OLD
        assert evidence == SOURCES[1:]
        assert [r["aspect_id"] for r in gaps] == ["A2"]
        seen.append("repair")
        return ADD

    def verify(question, draft, evidence):
        assert question == QUESTION and evidence == SOURCES
        assert draft.text.startswith(OLD.text) and draft.claims == (*OLD.claims, *ADD.claims)
        seen.append("verify")
        return receipt(draft, complete=True)

    draft, verified, outcome = complete_aspects(
        QUESTION, OLD, receipt(OLD), SOURCES, repair, verify
    )
    assert ADD.text in draft.text and verified.supported
    assert outcome == "ASPECT_COMPLETION_VERIFIED" and seen == ["repair", "verify"]


@pytest.mark.parametrize(
    "error",
    [
        QABudgetExceeded("QA_TIME_BUDGET_EXHAUSTED"),
        ProviderUnavailable("simulated"),
        InvalidProviderResponse("simulated"),
    ],
)
@pytest.mark.parametrize("phase", ["repair", "verify"])
def test_optional_completion_failure_keeps_previously_verified_answer(error, phase):
    def broken(*args):
        raise error

    draft, verified, outcome = complete_aspects(
        QUESTION,
        OLD,
        receipt(OLD),
        SOURCES,
        broken if phase == "repair" else lambda *args: ADD,
        broken if phase == "verify" else lambda *args: receipt(args[1], complete=True),
    )
    assert draft == OLD and verified == receipt(OLD)
    assert outcome in {"ASPECT_COMPLETION_BUDGET_EXHAUSTED", "ASPECT_COMPLETION_UNAVAILABLE"}


def test_new_conflict_from_completion_still_blocks_answer():
    draft, verified, outcome = complete_aspects(
        QUESTION,
        OLD,
        receipt(OLD),
        SOURCES,
        lambda *args: ADD,
        lambda *args: receipt(args[1], complete=True, conflict=True),
    )
    assert not verified.supported and verified.conflicting_evidence_ids
    assert outcome == "ASPECT_COMPLETION_CONFLICT"


def test_completion_cannot_invent_source_ids_or_skip_reverification():
    invalid = replace(ADD, citation_ids=("E99",), claims=(AtomicClaim("invented", ("E99",)),))
    draft, verified, outcome = complete_aspects(
        QUESTION,
        OLD,
        receipt(OLD),
        SOURCES,
        lambda *args: invalid,
        lambda *args: pytest.fail("invalid evidence reached verifier"),
    )
    assert draft == OLD and outcome == "ASPECT_COMPLETION_UNAVAILABLE"


def test_actual_qa_path_completes_missing_item(tmp_path):
    class Generator:
        revision = "fixture"
        calls = 0

        def generate(self, *args):
            return OLD

        def repair_aspects(self, *args):
            self.calls += 1
            return ADD

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(SOURCES), generator=Generator())
    service.verifier = SimpleNamespace(
        verify=lambda q, d, e: receipt(d, complete=len(d.claims) == 2)
    )
    result = service.ask(QUESTION, "tenant", "user")
    assert result.verified and "申请需要发票" in result.answer
    assert result.coverage_report["required_aspects"]["complete"]
    assert service.generator.calls == 1
    assert "ASPECT_COMPLETION_VERIFIED" not in result.warnings
    assert result.coverage_report["answer_completion"]["attempts"] == 1


def test_answer_cache_write_failure_returns_answer_and_reuses_draft(tmp_path):
    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider((_evidence(),)))

    class Cache:
        def get(self, *args):
            raise OSError("unavailable")

        def put(self, *args):
            raise sqlite3.OperationalError("full")

    service.cache = Cache()
    first = service.ask("保修多久？", "tenant", "user")
    service.generator.fail = True
    second = service.ask("保修多久？", "tenant", "user")
    assert first.verified and second.verified and first.answer == second.answer
    assert "ANSWER_CACHE_WRITE_UNAVAILABLE" in first.warnings


def test_validation_is_shared_across_paths_but_not_requests():
    reads = []

    def check():
        reads.append("db")

    @validation_scope
    def search():
        validate_once(("model-a", "generation-1"), check)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [
                pool.submit(copy_context().run, validate_once, ("model-a", "generation-1"), check)
                for _ in range(2)
            ]
            for job in jobs:
                job.result()
        validate_once(("model-b", "generation-1"), check)

    search()
    assert len(reads) == 2
    search()
    assert len(reads) == 4


def test_validation_failure_is_not_cached():
    calls = []

    def failed():
        calls.append(1)
        raise ValueError("contract mismatch")

    @validation_scope
    def search():
        for _ in range(2):
            with pytest.raises(ValueError):
                validate_once(("x",), failed)

    search()
    assert len(calls) == 2


def reader_stub(documents, calls, *, search=None):
    class Repository:
        def list_documents_page(self, *args, **kwargs):
            return SimpleNamespace(items=documents, next_key=None)

        def list_chunks_page(self, version, **kwargs):
            calls.append(version)
            return SimpleNamespace(items=[], next_key=None)

    return OverviewReader(
        Repository(),
        SimpleNamespace(authorize_chunks=lambda *args: {}),
        SimpleNamespace(),
        settings(),
        None,
        scope_search=search,
    )


DOCS = [
    {"document_id": "other", "version_id": "v-other", "filename": "无关操作手册.md"},
    {"document_id": "target", "version_id": "v-target", "filename": "星云设备.md"},
]


def test_overview_determines_document_scope_before_reading_any_chunks():
    calls = []
    reader = reader_stub(DOCS, calls)
    context = SimpleNamespace(space_ids=("s",))
    assert reader.resolve_scope("总结星云设备", context) == ({"target"}, "inferred_documents")
    assert calls == []
    evidence, report = reader_stub(DOCS, calls).read("总结全文", context)
    assert evidence == () and report["scope_resolution"] == "unresolved" and calls == []
    with reading_scope(ReadingOptions(document_ids=("target",))):
        assert reader.resolve_scope("总结全文", context) == ({"target"}, "explicit_documents")
    assert reader.resolve_scope("总结所有文档", context) == (set(), "whole_space")


def test_overview_search_filters_irrelevant_documents_before_chapter_reading():
    hits = tuple(
        SearchHit(
            str(i),
            d["document_id"],
            d["version_id"],
            d["filename"],
            {"page": 1},
            0.8,
            i,
            ("dense",),
        )
        for i, d in enumerate(DOCS)
    )
    seen = []

    def search(question, context, **kwargs):
        seen.append(kwargs)
        return SearchResult(hits, 0)

    selector = SimpleNamespace(
        select=lambda *args: EvidenceSelection(source_ids=("E2",), coverage="partial")
    )
    assert search_documents(
        SimpleNamespace(search=search), selector, "概述星云产品", SimpleNamespace()
    ) == ("target",)
    assert seen == [{"limit": 20, "query_limit": 1}]


def test_unchanged_sync_uses_metadata_and_bulk_state(directory_setup, monkeypatch):
    runtime, root, sync = directory_setup
    for i in range(4):
        (root / f"{i}.txt").write_text(f"document {i}")
    first = sync.run(root, runtime.space_id, apply=True)
    if fingerprint(root / "0.txt") is None:
        pytest.skip("filesystem has no reliable change identity")
    original_bulk = runtime.repository.directory_sync_states
    calls = []

    def bulk(ids):
        calls.append(ids)
        return original_bulk(ids)

    monkeypatch.setattr(runtime.repository, "directory_sync_states", bulk)
    monkeypatch.setattr(
        runtime.repository, "get_version", lambda *args: pytest.fail("per-file query")
    )
    monkeypatch.setattr(runtime.queue, "get", lambda *args: pytest.fail("per-file queue query"))
    result = sync.run(root, runtime.space_id, apply=True)
    assert result["scan_statistics"] == {"metadata_reused": 4, "content_hashed": 0}
    assert result["reused"] == 4 and len(calls) == 1 and len(calls[0]) == 4
    assert result["manifest"] == first["manifest"]
    verified = sync.run(root, runtime.space_id, verify_content=True)
    assert verified["scan_statistics"]["content_hashed"] == 4


def test_same_size_change_with_restored_mtime_is_not_missed(directory_setup):
    runtime, root, sync = directory_setup
    path = root / "a.txt"
    path.write_text("aaaa")
    sync.run(root, runtime.space_id, apply=True)
    stat = path.stat()
    path.write_text("bbbb")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = sync.run(root, runtime.space_id)
    assert result["updated"] == 1 and result["scan_statistics"]["content_hashed"] == 1


def test_periodic_content_recheck_does_not_slide_on_metadata_reuse(directory_setup, monkeypatch):
    runtime, root, sync = directory_setup
    (root / "a.txt").write_text("content")
    first = sync.run(root, runtime.space_id, apply=True)
    verified_at = first["results"][0]["content_verified_at"]
    monkeypatch.setattr("ragkb.application.directory_sync.time.time", lambda: verified_at + 90000)
    result = sync.run(root, runtime.space_id)
    assert result["scan_statistics"]["content_hashed"] == 1


def test_cache_retention_distinguishes_query_and_document_and_promotes(tmp_path):
    cache = SQLiteEmbeddingCache(tmp_path / "cache.db", query_days=1, document_days=30)
    adapter = OpenAICompatibleEmbeddingAdapter(settings(), cache=cache, transport=Transport())
    adapter.embed_queries(["query", "promoted"])
    adapter.embed(["document", "promoted"])
    now = time.time()
    with sqlite3.connect(cache.path) as db:
        kinds = dict(db.execute("SELECT input_hash,purpose FROM vector_retention"))
        assert kinds[cache.key("query")] == "query"
        assert kinds[cache.key("promoted")] == "document"
    assert cache.maintain(now=now + 2 * 86400) == {"document": 0, "query": 1}
    assert cache.get(adapter._cache_namespace, cache.key("document"), 2)
    assert cache.get(adapter._cache_namespace, cache.key("query"), 2) is None


def test_cache_capacity_and_legacy_migration(tmp_path):
    path = tmp_path / "legacy.db"
    encoded = json.dumps([1.0, 2.0], separators=(",", ":"))
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE vectors(namespace TEXT,input_hash TEXT,dimension INTEGER,"
            "vector TEXT,checksum TEXT,PRIMARY KEY(namespace,input_hash))"
        )
        for key in ["a", "b", "c"]:
            db.execute(
                "INSERT INTO vectors VALUES (?,?,?,?,?)",
                ("n", key, 2, encoded, SQLiteEmbeddingCache.key(encoded)),
            )
    cache = SQLiteEmbeddingCache(path, document_bytes=300)
    assert len(cache.get_many("n", ["a", "b", "c"], 2)) == 3
    assert cache.maintain()["document"] == 2
    assert len(cache.get_many("n", ["a", "b", "c"], 2)) == 1


def test_statistics_rollup_preserves_totals_unknowns_and_task_identity(tmp_path):
    ledger = SQLiteReuseLedger(tmp_path / "reuse.db")
    identity = {"kind": "qa", "tenant_id": "t", "user_id": "u"}
    for attempt in ["a", "b"]:
        ledger.begin("task", attempt, identity)
        ledger.event(
            attempt, attempt + "e", "embedding", {"generated_vectors": 3, "cache_reused_vectors": 5}
        )
        ledger.event(attempt, attempt + "h", "http", {"pending": True})
        ledger.finish(attempt, "done")
    ledger.begin("active", "running", identity)
    before = ledger.report("task")
    assert ledger.maintain(now=time.time() + 100 * 86400)["archived_attempts"] == 2
    after = ledger.report("task")
    for key in [
        "generated_vectors",
        "cache_reused_vectors",
        "unknown_http_outcomes",
        "attempt_count",
        "complete",
    ]:
        assert after[key] == before[key]
    assert after["archived_attempt_count"] == 2 and after["attempts"] == []
    assert ledger.identity("task")["tenant_id"] == "t"
    assert len(ledger.report("active")["attempts"]) == 1
    ledger.event("a", "late", "embedding", {"generated_vectors": 999})
    assert ledger.report("task")["generated_vectors"] == 6
    ledger.maintain(now=time.time() + 800 * 86400)
    assert ledger.identity("task") is None and not ledger.report("task")["available"]
    assert ledger.identity("active") is not None


def test_statistics_capacity_rolls_up_only_finished_attempts(tmp_path):
    ledger = SQLiteReuseLedger(tmp_path / "reuse.db")
    for i in range(4):
        ledger.begin("task", str(i), {})
        if i < 3:
            ledger.finish(str(i), "done")
    result = ledger.maintain(max_finished_attempts=1)
    assert result["archived_attempts"] == 2
    report = ledger.report("task")
    assert report["attempt_count"] == 4 and len(report["attempts"]) == 2 and not report["complete"]


def test_incomplete_answer_does_not_become_a_permanent_exact_cache_hit(tmp_path):
    from ragkb.infrastructure.exact_answer_reuse import ExactAnswerReuse
    from test_qa_speed_guards import Cache

    class Generator:
        revision = "fixture"
        repairs = 0

        def generate(self, *args):
            return OLD

        def repair_aspects(self, *args):
            self.repairs += 1
            if self.repairs == 1:
                raise QABudgetExceeded("QA_TIME_BUDGET_EXHAUSTED")
            return ADD

    service, _, _ = _service(tmp_path, SyntheticEvidenceProvider(SOURCES), generator=Generator())
    service.verifier = SimpleNamespace(
        verify=lambda q, d, e: receipt(d, complete=len(d.claims) == 2)
    )
    cache = Cache()
    service.result_reuse = ExactAnswerReuse(
        cache, lambda *args: "snapshot", config_revision="test", ttl_seconds=3600
    )
    first = service.ask(QUESTION, "t", "u")
    assert first.verified and not first.coverage_report["required_aspects"]["complete"]
    assert not cache.values
    second = service.ask(QUESTION, "t", "u")
    assert second.coverage_report["required_aspects"]["complete"] and cache.values
    assert service.generator.repairs == 2


def test_cache_blob_and_oversized_number_are_treated_as_corrupt_entries(tmp_path):
    cache = SQLiteEmbeddingCache(tmp_path / "vectors.db")
    blob = b"[]"
    assert cache._decode(blob, "bad", 2) is None
    encoded = "[" + "9" * 1000 + ",1]"
    assert cache._decode(encoded, cache.key(encoded), 2) is None
