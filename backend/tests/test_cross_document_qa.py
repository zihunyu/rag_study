"""Cross-file retrieval, evidence and citations through the real local API pipeline.

Only generation is replaced with an evidence-driven extractive test double. These
tests verify plumbing and isolation, not real-provider reasoning quality.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.domain.rag import AtomicClaim, DraftAnswer, Evidence
from ragkb.runtime_components import RuntimeComponents, build_runtime_components

FACTS = (
    "星云设备的保修期为三年。",
    "星云设备支持上门维修。",
    "星云设备报修需要提供购买凭证。",
)
QUESTION = "星云设备的保修期、维修方式和报修凭证分别是什么？"


class _ExtractRetrievedFacts:
    revision = "cross-document-extractive-test-only"

    def generate(self, question: str, evidence: tuple[Evidence, ...]) -> DraftAnswer:
        claims = tuple(
            AtomicClaim(item.display_text or item.text, (item.evidence_id,)) for item in evidence
        )
        return DraftAnswer(
            "\n".join(claim.text for claim in claims),
            tuple(item.evidence_id for item in evidence),
            claims,
        )


def _upload(
    client: TestClient,
    components: RuntimeComponents,
    space_id: str,
    name: str,
    text: str,
    *,
    publish: bool = True,
) -> dict[str, str]:
    content = (text + "\n").encode()
    created = client.post(
        f"/api/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": f"create-{name}"},
        json={
            "filename": name,
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/markdown",
        },
    )
    assert created.status_code == 201, created.text
    uploaded = client.put(
        created.json()["upload_path"],
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text
    completed = client.post(
        f"/api/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": f"complete-{name}"},
    )
    assert completed.status_code == 202, completed.text
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        components.parser_router,
        "cross-document-worker",
        chunker=components.chunker,
        indexing_sink=components.indexing_sink,
    )
    assert worker.run_once() is True
    version_id = completed.json()["document_version_id"]
    if publish:
        review = client.post(
            f"/api/document-versions/{version_id}/review",
            headers={"Idempotency-Key": f"review-{name}"},
            json={
                "decision": "APPROVED",
                "comment": "synthetic cross-file test evidence",
                "security_projection": {
                    "visibility": "TENANT",
                    "classification_level": 0,
                    "acl_scope_tokens": [],
                },
            },
        )
        assert review.status_code == 200, review.text
        published = client.post(
            f"/api/document-versions/{version_id}:publish",
            headers={"Idempotency-Key": f"publish-{name}"},
        )
        assert published.status_code == 200, published.text
    return completed.json()


@pytest.fixture
def corpus(tmp_path: Path):
    components = build_runtime_components(
        storage_root=tmp_path / "storage", database_path=tmp_path / "control.sqlite3"
    )
    components.qa_service.generator = _ExtractRetrievedFacts()
    with TestClient(create_app(components)) as client:
        created = client.post("/api/spaces", json={"name": "跨文件测试库"})
        assert created.status_code == 201
        space_id = created.json()["id"]
        documents = [
            _upload(client, components, space_id, f"facts-{index}.md", fact)
            for index, fact in enumerate(FACTS)
        ]
        yield components, client, space_id, documents


@pytest.mark.parametrize("file_count", [2, 3])
def test_one_answer_cites_each_selected_file_and_resolves_its_own_source(corpus, file_count):
    components, client, space_id, documents = corpus
    expected_ids = {doc["document_id"] for doc in documents[:file_count]}
    # Empty document_ids is the default UI's entire-knowledge-base scope.
    reading = {"mode": "fact"}
    if file_count == 2:
        reading["document_ids"] = sorted(expected_ids)
    search = client.post("/api/search", json={"query": QUESTION, "space_id": space_id})
    assert search.status_code == 200, search.text
    assert {hit["document_id"] for hit in search.json()["hits"]} == {
        doc["document_id"] for doc in documents
    }
    response = client.post(
        "/api/ask", json={"question": QUESTION, "space_id": space_id, "reading": reading}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "answered", result
    assert result["verified"] is True
    assert result["real_acceptance"] is False
    for fact in FACTS[:file_count]:
        assert fact in result["answer"]
    for fact in FACTS[file_count:]:
        assert fact not in result["answer"]
    package = components.rag_repository.get_package(result["rag_run_id"])
    assert {item.document_id for item in package.generation_evidence} == expected_ids
    cited_documents = set()
    for citation in result["citations"]:
        evidence = components.rag_repository.get_evidence(
            result["rag_run_id"], citation["evidence_id"]
        )
        cited_documents.add(evidence.document_id)
        source = client.get(citation["source_url"])
        assert source.status_code == 200, source.text
        assert source.json()["text"] == (evidence.display_text or evidence.text)
    assert cited_documents == expected_ids


def test_other_knowledge_base_and_unpublished_file_never_enter_answer(corpus):
    components, client, space_id, documents = corpus
    other_space = client.post("/api/spaces", json={"name": "隔离测试库"}).json()["id"]
    foreign = _upload(client, components, other_space, "foreign.md", "星云设备的保修期为九十九年。")
    draft = _upload(
        client, components, space_id, "draft.md", "星云设备报修需要提供秘密口令。", publish=False
    )
    search = client.post("/api/search", json={"query": QUESTION, "space_id": space_id})
    assert search.status_code == 200, search.text
    assert {hit["document_id"] for hit in search.json()["hits"]} == {
        doc["document_id"] for doc in documents
    }
    response = client.post(
        "/api/ask",
        json={"question": QUESTION, "space_id": space_id, "reading": {"mode": "fact"}},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["verified"] is True, result
    package = components.rag_repository.get_package(result["rag_run_id"])
    assert {foreign["document_id"], draft["document_id"]}.isdisjoint(
        item.document_id for item in package.evidence
    )
    assert "九十九年" not in result["answer"]
    assert "秘密口令" not in result["answer"]
    # Selecting only a foreign or staged document must not bypass either fence.
    excluded = client.post(
        "/api/ask",
        json={
            "question": QUESTION,
            "space_id": space_id,
            "reading": {
                "mode": "fact",
                "document_ids": [foreign["document_id"], draft["document_id"]],
            },
        },
    ).json()
    assert excluded["status"] == "insufficient_evidence", excluded
    assert excluded["answer"] is None
    assert excluded["citations"] == []
