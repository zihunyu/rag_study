"""Exercise actual adapter wiring using isolated stores and transports."""

import sqlite3
from types import SimpleNamespace

from mysql_sql_harness import SQLControl
from ragkb.adapters.embedding_cache import SQLiteEmbeddingCache
from ragkb.adapters.model_http import OpenAICompatibleEmbeddingAdapter
from ragkb.adapters.mysql_upload import MySQLUploadRepository
from ragkb.adapters.retrieval_memory import InMemoryRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.adapters.zilliz import MilvusHybridAdapter
from ragkb.application.search import HybridSearchService
from ragkb.infrastructure.overview import OverviewReader
from test_embedding_reuse import Transport, settings
from test_hybrid_search import _chunk, _context


def test_real_search_adapter_checks_contract_once_per_search():
    validations = []
    index = MilvusHybridAdapter(
        settings(),
        embedding_contracts=SimpleNamespace(
            require=lambda cfg, generation: validations.append(generation)
        ),
    )
    index._client = SimpleNamespace(search=lambda **kw: [[] for _ in kw["data"]])
    service = HybridSearchService(
        DeterministicEmbedding(),
        index,
        InMemoryRetrievalControlPlane({}),
        DeterministicReranker(),
        bm25_top_k=50,
        dense_top_k=50,
        rrf_k=60,
        rerank_top_k=40,
        final_evidence_count=8,
    )
    service.search("保修多久", _context(watermark=0))
    assert len(validations) == 1
    service.search("保修多久", _context(watermark=0))
    assert len(validations) == 2


def test_overview_small_read_budget_goes_to_target_document():
    first = _chunk("first", "食堂营业时间说明。", "a")
    target = _chunk("target", "目标产品保修三年。", "b")
    documents = [
        {"document_id": c.document_id, "version_id": c.document_version_id, "filename": name}
        for c, name in ((first, "无关资料.txt"), (target, "目标产品.txt"))
    ]
    chunks = {c.chunk_id: c for c in (first, target)}
    reads = []

    def page(version, **kw):
        reads.append(version)
        return SimpleNamespace(
            items=[
                {"chunk_id": c.chunk_id}
                for c in chunks.values()
                if c.document_version_id == version
            ],
            next_key=None,
        )

    repository = SimpleNamespace(
        list_documents_page=lambda *args, **kw: SimpleNamespace(items=documents, next_key=None),
        list_chunks_page=page,
    )
    auth = SimpleNamespace(authorize_chunks=lambda ids, context: {key: chunks[key] for key in ids})
    store = SimpleNamespace(
        ledger=SimpleNamespace(get=lambda *args: {}), list_assets=lambda version: []
    )
    reader = OverviewReader(
        repository, auth, store, settings(overview_max_chunks=1), SimpleNamespace()
    )
    selected, report = reader.read("总结目标产品的保修政策", _context(watermark=0))
    assert [s.document_id for s in selected] == [target.document_id]
    assert reads == [target.document_version_id]
    assert report["selected_document_ids"] == [target.document_id]


def test_mysql_bulk_sync_uses_bounded_queries_and_preserves_authority(tmp_path):
    control = SQLControl(tmp_path / "mysql-harness.db")
    repository = MySQLUploadRepository(control, "tenant", "generation")

    def seed(state):
        for i in range(12):
            doc, version = f"d{i}", f"v{i}"
            state["documents"][doc] = {
                "id": doc,
                "tenant_id": "tenant",
                "state": "ACTIVE",
                "space_id": "space",
            }
            state["versions"][version] = {
                "id": version,
                "document_id": doc,
                "tenant_id": "tenant",
                "version_no": 1,
                "processing_state": "VALIDATED",
                "content_sha256": str(i),
            }
            state["quality"][version] = {"id": version, "passed": True}
            state["candidates"][version] = {
                "projection_state": "ACTIVE",
                "expected_checksum": str(i),
                "observed_checksum": str(i),
                "observed_watermark": 2,
                "required_watermark": 2,
            }

    repository._mutate(seed)
    control.statements.clear()
    states = repository.directory_sync_states([f"v{i}" for i in range(12)] + ["missing"])
    assert len(states) == 12 and all(r["ingestion_complete"] for r in states.values())
    reads = [q for q, _ in control.statements if q.lstrip().upper().startswith("SELECT")]
    assert len(reads) == 5
    foreign = MySQLUploadRepository(control, "foreign", "generation")
    assert foreign.directory_sync_states(["v0"]) == {}
    repository._mutate(
        lambda state: state["versions"].__setitem__(
            "new",
            {
                **state["versions"]["v0"],
                "id": "new",
                "version_no": 2,
            },
        )
    )
    assert repository.directory_sync_states(["v0"])["v0"]["latest_version_id"] == "new"


def test_cache_metadata_failure_rolls_back_vector_but_paid_result_survives(tmp_path):
    cache = SQLiteEmbeddingCache(tmp_path / "cache.db")
    with sqlite3.connect(cache.path) as db:
        db.execute(
            "CREATE TRIGGER fail_metadata BEFORE INSERT ON vector_retention "
            "BEGIN SELECT RAISE(ABORT,'simulated'); END"
        )
    transport = Transport()
    adapter = OpenAICompatibleEmbeddingAdapter(settings(), transport=transport, cache=cache)
    assert adapter.embed_query("same") == adapter.embed_query("same") == [4.0, 1.0]
    assert len(transport.calls) == 1
    with sqlite3.connect(cache.path) as db:
        assert db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0
