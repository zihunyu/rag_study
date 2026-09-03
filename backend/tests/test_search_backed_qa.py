from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    LifecycleAwareFinalPermission,
)
from ragkb.adapters.retrieval_memory import InMemoryHybridIndex, InMemoryRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.api.app import create_app
from ragkb.application.evidence import SearchBackedEvidenceProvider
from ragkb.application.qa import TrustedQAService
from ragkb.application.search import HybridSearchService
from ragkb.domain.lifecycle import LifecycleState
from ragkb.domain.rag import AnswerStatus
from ragkb.domain.retrieval import AuthorizedChunk, IndexCandidate
from ragkb.runtime_components import build_runtime_components


def _components_with_search_qa(tmp_path: Path, *, revoke_during_generation: bool = False):
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    document_id = "qa-document"
    version_id = "qa-version"
    components.lifecycle_service.register_document(document_id, version_id, trace_id="setup")
    record = components.lifecycle_store.documents[document_id]
    record.lifecycle_state = LifecycleState.ACTIVE
    record.visible = True
    components.lifecycle_store.persist_state(tenant_id=components.tenant_id)
    chunk = AuthorizedChunk(
        chunk_id="qa-chunk",
        tenant_id=components.tenant_id,
        space_id=components.space_id,
        document_id=document_id,
        document_version_id=version_id,
        parent_chunk_id=None,
        display_text="保修期为三年。",
        retrieval_text="保修期 三年",
        locator={"page": 2},
        content_checksum="qa-checksum",
        visibility="TENANT",
        acl_scope_tokens=(),
        classification_level=1,
        lifecycle_projection="SERVING",
        valid_from_epoch=0,
        valid_to_epoch=0,
        permission_revision=1,
        current_version=True,
    )
    candidate = IndexCandidate("qa-chunk", version_id, None, "bm25", 1, 1.0)
    search = HybridSearchService(
        DeterministicEmbedding(),
        InMemoryHybridIndex(bm25=(candidate,), security_watermark=0),
        InMemoryRetrievalControlPlane({"qa-chunk": chunk}),
        DeterministicReranker(),
        bm25_top_k=10,
        dense_top_k=10,
        rrf_k=60,
        rerank_top_k=10,
        final_evidence_count=5,
        lifecycle_authorizer=components.lifecycle_store.authorizes_chunk,
    )
    generator: DeterministicBufferedGenerator
    if revoke_during_generation:

        class _RevokingGenerator(DeterministicBufferedGenerator):
            def generate(self, question, evidence):
                components.lifecycle_service.revoke(
                    document_id, event_id="concurrent-revoke", trace_id="concurrent"
                )
                return super().generate(question, evidence)

        generator = _RevokingGenerator()
    else:
        generator = DeterministicBufferedGenerator()
    provider = SearchBackedEvidenceProvider(
        search,
        space_id=components.space_id,
        active_generation_id="local-test-generation",
        active_permission_revision=lambda: 1,
        required_security_watermark=lambda: 0,
        prompt_revision=generator.revision,
        model_revision=generator.revision,
        final_evidence_count=5,
    )
    qa = TrustedQAService(
        provider,
        generator,
        LifecycleAwareFinalPermission(components.lifecycle_store, components.tenant_id),
        components.reference_signer,
        components.rag_repository,
    )
    return replace(components, search_service=search, qa_service=qa)


def test_search_to_evidence_to_ask_local_synthetic_e2e(tmp_path: Path) -> None:
    components = _components_with_search_qa(tmp_path)

    response = TestClient(create_app(components)).post(
        "/api/v1/ask", json={"question": "保修期多久？"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == AnswerStatus.ANSWERED.value
    assert response.json()["verified"] is True
    package = components.rag_repository.get_package(response.json()["rag_run_id"])
    assert package is not None
    assert package.evidence[0].chunk_id == "qa-chunk"


def test_generation_time_revoke_discards_search_backed_answer(tmp_path: Path) -> None:
    components = _components_with_search_qa(tmp_path, revoke_during_generation=True)

    response = TestClient(create_app(components)).post(
        "/api/v1/ask", json={"question": "保修期多久？"}
    )

    assert response.json()["status"] == AnswerStatus.SYSTEM_ERROR.value
    assert response.json()["answer"] is None
    assert response.json()["warnings"] == ["FINAL_PERMISSION_RECHECK_FAILED"]
