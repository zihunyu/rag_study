from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.runtime_components import build_runtime_components


def test_created_knowledge_base_owns_documents_chunks_search_and_ask(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("RAG_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("VECTOR_BACKEND", "local")
    monkeypatch.setenv("AUTH_MODE", "local_single_user")
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    client = TestClient(create_app(components))

    created = client.post("/api/v1/spaces", json={"name": "产品手册"})
    assert created.status_code == 201
    space_id = created.json()["id"]
    duplicate = client.post("/api/v1/spaces", json={"name": "产品手册"})
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == space_id

    content = "# 产品政策\nThinkPad P16 Gen 3 21FA 的保修期为三年。\n".encode()
    upload = client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": "knowledge-base-upload"},
        json={
            "filename": "service-policy.md",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/markdown",
        },
    )
    assert upload.status_code == 201
    uploaded = client.put(
        upload.json()["upload_path"],
        headers={"If-Match": upload.headers["etag"]},
        content=content,
    )
    completed = client.post(
        f"/api/v1/upload-sessions/{upload.json()['upload_session_id']}:complete",
        headers={
            "If-Match": uploaded.headers["etag"],
            "Idempotency-Key": "knowledge-base-complete",
        },
    ).json()

    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        components.parser_router,
        "knowledge-base-worker",
        chunker=components.chunker,
        indexing_sink=components.indexing_sink,
    )
    assert worker.run_once() is True

    documents = client.get(f"/api/v1/spaces/{space_id}/documents")
    assert documents.status_code == 200
    assert documents.json()[0]["filename"] == "service-policy.md"
    assert documents.json()[0]["processing_state"] == "VALIDATED"
    assert documents.json()[0]["chunk_count"] > 0

    version_id = completed["document_version_id"]
    chunks = client.get(f"/api/v1/document-versions/{version_id}/chunks")
    assert chunks.status_code == 200
    assert any("三年" in item["text"] for item in chunks.json())

    review = client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": "knowledge-base-review"},
        json={
            "decision": "APPROVED",
            "comment": "approved for the selected knowledge base",
            "security_projection": {
                "visibility": "TENANT",
                "classification_level": 0,
                "acl_scope_tokens": [],
            },
        },
    )
    assert review.status_code == 200
    published = client.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "knowledge-base-publish"},
    )
    assert published.status_code == 200

    selected_search = client.post(
        "/api/v1/search",
        json={"query": "ThinkPad 21FA 保修期", "space_id": space_id},
    )
    default_search = client.post(
        "/api/v1/search",
        json={"query": "ThinkPad 21FA 保修期", "space_id": components.space_id},
    )
    answer = client.post(
        "/api/v1/ask",
        json={"question": "ThinkPad 21FA 保修期多久？", "space_id": space_id},
    )

    assert selected_search.status_code == 200
    assert any("三年" in item["text"] for item in selected_search.json()["hits"])
    assert default_search.status_code == 200
    assert default_search.json()["hits"] == []
    assert answer.status_code == 200
    assert answer.json()["status"] == "answered", answer.json()
    assert answer.json()["verified"] is True


def test_unknown_knowledge_base_is_not_searchable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("RAG_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("VECTOR_BACKEND", "local")
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    client = TestClient(create_app(components))

    assert client.get("/api/v1/spaces/not-found/documents").status_code == 404
    assert (
        client.post(
            "/api/v1/search", json={"query": "anything", "space_id": "not-found"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/ask", json={"question": "anything", "space_id": "not-found"}
        ).status_code
        == 404
    )
