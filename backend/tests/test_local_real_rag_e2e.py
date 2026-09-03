from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.domain.lifecycle import LifecycleState
from ragkb.runtime_components import build_runtime_components


def test_upload_parse_chunk_embed_index_search_and_ask(tmp_path: Path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage", database_path=tmp_path / "control.sqlite3"
    )
    client = TestClient(create_app(components))
    content = "# 产品政策\nThinkPad P16 Gen 3 21FA 的保修期为三年。\n".encode()
    created = client.post(
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        headers={"Idempotency-Key": "e2e-create"},
        json={
            "filename": "policy.md",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/markdown",
        },
    )
    uploaded = client.put(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    completed = client.post(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "e2e-complete"},
    ).json()
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        components.parser_router,
        "e2e-worker",
        chunker=components.chunker,
        indexing_sink=components.indexing_sink,
    )

    assert worker.run_once() is True
    record = components.lifecycle_store.documents[completed["document_id"]]
    record.active_version_id = completed["document_version_id"]
    record.lifecycle_state = LifecycleState.ACTIVE
    record.visible = True
    components.lifecycle_store.persist_state(tenant_id=components.tenant_id)

    search = client.post("/api/v1/search", json={"query": "ThinkPad 21FA 保修期"})
    answer = client.post("/api/v1/ask", json={"question": "ThinkPad 21FA 保修期多久？"})

    assert search.status_code == 200
    assert search.json()["hits"]
    assert "三年" in search.json()["hits"][0]["text"]
    assert answer.json()["status"] == "answered"
    assert answer.json()["verified"] is True
