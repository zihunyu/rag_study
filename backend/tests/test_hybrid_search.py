from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.retrieval_memory import InMemoryHybridIndex, InMemoryRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.api.app import create_app
from ragkb.application.search import HybridSearchService, classify_query, rrf_fuse
from ragkb.domain.errors import ProviderUnavailable
from ragkb.domain.lifecycle import LifecycleState
from ragkb.domain.retrieval import (
    AuthorizedChunk,
    IndexCandidate,
    SearchContext,
    SecurityWatermarkNotReady,
)
from ragkb.runtime_components import build_runtime_components


def _candidate(chunk_id: str, channel: str, rank: int) -> IndexCandidate:
    return IndexCandidate(
        chunk_id=chunk_id,
        document_version_id=f"version-{chunk_id}",
        parent_chunk_id="parent-1" if chunk_id == "chunk-1" else None,
        channel="bm25" if channel == "bm25" else "dense",
        rank=rank,
        score=1.0 / rank,
    )


def _chunk(
    chunk_id: str,
    text: str,
    checksum: str,
    *,
    visibility: str = "TENANT",
    acl: tuple[str, ...] = (),
) -> AuthorizedChunk:
    return AuthorizedChunk(
        chunk_id=chunk_id,
        tenant_id="tenant-1",
        space_id="space-1",
        document_id=f"document-{chunk_id}",
        document_version_id=f"version-{chunk_id}",
        parent_chunk_id="parent-1" if chunk_id == "chunk-1" else None,
        display_text=text,
        retrieval_text=text,
        locator={"page": 1},
        content_checksum=checksum,
        visibility="RESTRICTED" if visibility == "RESTRICTED" else "TENANT",
        acl_scope_tokens=acl,
        classification_level=1,
        lifecycle_projection="SERVING",
        valid_from_epoch=0,
        valid_to_epoch=0,
        permission_revision=4,
        current_version=True,
    )


def _context(*, watermark: int = 10) -> SearchContext:
    return SearchContext(
        tenant_id="tenant-1",
        space_ids=("space-1",),
        subject_scope_tokens=("group:reader",),
        clearance_level=2,
        as_of_epoch=int(time.time()),
        active_generation_id="generation-1",
        active_permission_revision=5,
        required_security_watermark=watermark,
    )


def test_rrf_dedup_acl_parent_recheck_and_rerank() -> None:
    bm25 = (
        _candidate("chunk-1", "bm25", 1),
        _candidate("unauthorized", "bm25", 2),
        _candidate("duplicate", "bm25", 3),
    )
    dense = (
        _candidate("chunk-2", "dense", 1),
        _candidate("chunk-1", "dense", 2),
    )
    chunks = {
        "chunk-1": _chunk(
            "chunk-1",
            "warranty is three years",
            "checksum-1",
            visibility="RESTRICTED",
            acl=("group:reader",),
        ),
        "chunk-2": _chunk("chunk-2", "unrelated noise", "checksum-2"),
        "duplicate": _chunk("duplicate", "duplicate", "checksum-1"),
        "unauthorized": _chunk(
            "unauthorized",
            "secret warranty",
            "checksum-secret",
            visibility="RESTRICTED",
            acl=("group:secret",),
        ),
        "parent-1": _chunk(
            "parent-1",
            "authorized parent context",
            "checksum-parent",
            visibility="RESTRICTED",
            acl=("group:reader",),
        ),
    }
    service = HybridSearchService(
        DeterministicEmbedding(),
        InMemoryHybridIndex(bm25=bm25, dense=dense, security_watermark=10),
        InMemoryRetrievalControlPlane(chunks),
        DeterministicReranker(),
        bm25_top_k=10,
        dense_top_k=10,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=5,
    )

    result = service.search("warranty", _context())

    assert [hit.chunk_id for hit in result.hits] == ["chunk-1", "chunk-2"]
    assert result.hits[0].channels == ("bm25", "dense")
    assert result.hits[0].parent_text == "authorized parent context"
    assert "unauthorized" not in {hit.chunk_id for hit in result.hits}
    assert "duplicate" not in {hit.chunk_id for hit in result.hits}
    assert result.real_acceptance is False
    assert result.degraded is False
    assert rrf_fuse((bm25, dense), rrf_k=60)[0][0].chunk_id == "chunk-1"


def test_chinese_natural_language_without_spaces_is_semantic() -> None:
    assert classify_query("劳动合同解除条件") == "semantic"
    assert classify_query("错误码 ERR-2048") == "identifier"


def test_security_watermark_fails_closed() -> None:
    service = HybridSearchService(
        DeterministicEmbedding(),
        InMemoryHybridIndex(security_watermark=9),
        InMemoryRetrievalControlPlane(),
        DeterministicReranker(),
        bm25_top_k=10,
        dense_top_k=10,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=5,
    )

    with pytest.raises(SecurityWatermarkNotReady):
        service.search("query", _context(watermark=10))


def test_search_api_is_independent_and_does_not_expose_acl_or_tenant_filters(
    tmp_path: Path,
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    chunk = AuthorizedChunk(
        chunk_id="chunk-api",
        tenant_id=components.tenant_id,
        space_id=components.space_id,
        document_id="document-api",
        document_version_id="version-api",
        parent_chunk_id=None,
        display_text="searchable contract",
        retrieval_text="searchable contract",
        locator={"page": 1},
        content_checksum="checksum-api",
        visibility="TENANT",
        acl_scope_tokens=(),
        classification_level=1,
        lifecycle_projection="SERVING",
        valid_from_epoch=0,
        valid_to_epoch=0,
        permission_revision=1,
        current_version=True,
    )
    service = HybridSearchService(
        DeterministicEmbedding(),
        InMemoryHybridIndex(bm25=(_candidate("chunk-api", "bm25", 1),), security_watermark=0),
        InMemoryRetrievalControlPlane({"chunk-api": chunk}),
        DeterministicReranker(),
        bm25_top_k=10,
        dense_top_k=10,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=5,
        lifecycle_authorizer=components.lifecycle_store.authorizes_chunk,
    )
    components.lifecycle_service.register_document("document-api", "version-api", trace_id="setup")
    record = components.lifecycle_store.documents["document-api"]
    record.lifecycle_state = LifecycleState.ACTIVE
    record.visible = True
    components.lifecycle_store.persist_state(tenant_id=components.tenant_id)
    app = create_app(replace(components, search_service=service))
    client = TestClient(app)

    response = client.post("/api/v1/search", json={"query": "searchable"})
    schema = app.openapi()
    request_properties = schema["components"]["schemas"]["SearchRequest"]["properties"]

    assert response.status_code == 200
    assert response.json()["hits"][0]["chunk_id"] == "chunk-api"
    assert response.json()["real_acceptance"] is False
    assert "/api/v1/search" in schema["paths"]
    assert "/api/v1/ask" in schema["paths"]
    assert (
        schema["paths"]["/api/v1/search"]["post"]["operationId"]
        != schema["paths"]["/api/v1/ask"]["post"]["operationId"]
    )
    assert {"tenant_id", "acl_scope_tokens", "subject_scope_tokens"}.isdisjoint(request_properties)

    components.lifecycle_service.delete("document-api", event_id="delete", trace_id="trace")
    after_delete = client.post("/api/v1/search", json={"query": "searchable"})
    assert after_delete.json()["hits"] == []


def test_tombstoned_text_never_reaches_reranker(tmp_path: Path) -> None:
    class _TrackingReranker:
        revision = "tracking"

        def __init__(self) -> None:
            self.documents: list[str] = []

        def rerank(self, query, documents):
            self.documents.extend(documents)
            return tuple(range(len(documents)))

    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    chunk = _chunk("chunk-1", "must stay private", "checksum")
    components.lifecycle_service.register_document(
        chunk.document_id, chunk.document_version_id, trace_id="setup"
    )
    components.lifecycle_service.delete(chunk.document_id, event_id="delete", trace_id="delete")
    reranker = _TrackingReranker()
    service = HybridSearchService(
        DeterministicEmbedding(),
        InMemoryHybridIndex(bm25=(_candidate("chunk-1", "bm25", 1),), security_watermark=10),
        InMemoryRetrievalControlPlane({"chunk-1": chunk}),
        reranker,
        bm25_top_k=10,
        dense_top_k=10,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=5,
        lifecycle_authorizer=components.lifecycle_store.authorizes_chunk,
    )

    result = service.search("private", _context())

    assert result.hits == ()
    assert reranker.documents == []


def test_dense_and_reranker_failures_degrade_to_rrf_bm25_without_bypassing_acl() -> None:
    class _FailingEmbedding:
        revision = "failing-embedding"
        dimension = 8

        def embed(self, texts):
            raise ProviderUnavailable("embedding unavailable")

    class _FailingReranker:
        revision = "failing-reranker"

        def rerank(self, query, documents):
            raise ProviderUnavailable("reranker unavailable")

    candidate = _candidate("chunk-1", "bm25", 1)
    chunk = _chunk(
        "chunk-1",
        "authorized evidence",
        "checksum-1",
        visibility="RESTRICTED",
        acl=("group:reader",),
    )
    service = HybridSearchService(
        _FailingEmbedding(),
        InMemoryHybridIndex(bm25=(candidate,), security_watermark=10),
        InMemoryRetrievalControlPlane({"chunk-1": chunk}),
        _FailingReranker(),
        bm25_top_k=10,
        dense_top_k=10,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=5,
    )

    result = service.search("evidence", _context())

    assert [hit.chunk_id for hit in result.hits] == ["chunk-1"]
    assert result.degraded is True
    assert result.warnings == ("DENSE_RETRIEVAL_UNAVAILABLE", "RERANKER_UNAVAILABLE")
