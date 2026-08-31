from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.runtime_components import RuntimeComponents, build_runtime_components


def _components(tmp_path: Path) -> RuntimeComponents:
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )


def _create_session(
    client: TestClient,
    space_id: str,
    content: bytes,
    *,
    filename: str = "knowledge.md",
    mime: str = "text/markdown",
    key: str = "create-1",
):
    return client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": key},
        json={
            "filename": filename,
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": mime,
        },
    )


def test_upload_complete_document_job_and_worker_flow(tmp_path: Path) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    content = b"# Policy\nWarranty is three years.\n"

    created = _create_session(client, components.space_id, content)
    assert created.status_code == 201
    session_id = created.json()["upload_session_id"]
    uploaded = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    assert uploaded.status_code == 200
    completed = client.post(
        f"/api/v1/upload-sessions/{session_id}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "complete-1"},
    )
    assert completed.status_code == 202
    result = completed.json()
    assert result["real_acceptance"] is False
    assert components.storage.exists(
        "original", components.repository.get_versions(result["document_id"])[0]["original_key"]
    )

    job_before = client.get(f"/api/v1/ingestion-jobs/{result['job_id']}")
    assert job_before.json()["state"] == "QUEUED"
    assert (
        LocalIngestionWorker(
            components.queue,
            components.repository,
            components.storage,
            components.parser_router,
            "test-worker",
        ).run_once()
        is True
    )
    job_after = client.get(f"/api/v1/ingestion-jobs/{result['job_id']}")
    assert job_after.json()["state"] == "SUCCEEDED"
    versions = client.get(f"/api/v1/documents/{result['document_id']}/versions")
    assert versions.json()[0]["processing_state"] == "VALIDATED"

    replay = client.post(
        f"/api/v1/upload-sessions/{session_id}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "complete-1"},
    )
    assert replay.status_code == 202
    assert replay.json() == result


def test_create_session_idempotency_and_conflict(tmp_path: Path) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    content = b"same"

    first = _create_session(client, components.space_id, content, key="stable-key")
    replay = _create_session(client, components.space_id, content, key="stable-key")
    conflict = _create_session(client, components.space_id, b"different", key="stable-key")

    assert replay.status_code == 201
    assert replay.json()["upload_session_id"] == first.json()["upload_session_id"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "CONFLICT_IDEMPOTENCY_KEY"


def test_stale_if_match_is_rejected(tmp_path: Path) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    content = b"text"
    created = _create_session(client, components.space_id, content)
    session_id = created.json()["upload_session_id"]

    first = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    stale = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )

    assert first.status_code == 200
    assert stale.status_code == 412


def test_malware_fixture_stays_in_quarantine(tmp_path: Path) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    content = b"X5O!P%@AP EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
    created = _create_session(
        client,
        components.space_id,
        content,
        filename="eicar.txt",
        mime="text/plain",
    )
    session_id = created.json()["upload_session_id"]
    uploaded = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    rejected = client.post(
        f"/api/v1/upload-sessions/{session_id}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "complete"},
    )

    assert rejected.status_code == 422
    session = components.repository.get_session(session_id)
    assert session.state.value == "FAILED"
    assert components.storage.exists("quarantine", session.quarantine_key)
    assert session.original_key is None


def test_abort_removes_quarantine_content(tmp_path: Path) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    content = b"temporary"
    created = _create_session(client, components.space_id, content)
    session_id = created.json()["upload_session_id"]
    uploaded = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    aborted = client.post(
        f"/api/v1/upload-sessions/{session_id}:abort",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "abort"},
    )

    assert aborted.status_code == 200
    assert aborted.json()["state"] == "ABORTED"
    session = components.repository.get_session(session_id)
    assert not components.storage.exists("quarantine", session.quarantine_key)


def test_path_traversal_filename_is_rejected(tmp_path: Path) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))

    response = _create_session(client, components.space_id, b"x", filename="../escape.txt")

    assert response.status_code == 422
    assert response.json()["code"] == "DOC_PATH_TRAVERSAL"


def test_openapi_contains_frozen_g1_paths_and_headers(tmp_path: Path) -> None:
    app = create_app(_components(tmp_path))
    schema = app.openapi()

    assert app.version == "1.0.0"
    assert "/api/v1/spaces/{space_id}/upload-sessions" in schema["paths"]
    assert "/api/v1/upload-sessions/{session_id}:complete" in schema["paths"]
    create_parameters = schema["paths"]["/api/v1/spaces/{space_id}/upload-sessions"]["post"][
        "parameters"
    ]
    assert any(item["name"] == "Idempotency-Key" and item["required"] for item in create_parameters)
    put_parameters = schema["paths"]["/api/v1/upload-sessions/{session_id}/content"]["put"][
        "parameters"
    ]
    assert any(item["name"] == "If-Match" and item["required"] for item in put_parameters)


def test_request_id_is_echoed_and_errors_use_standard_shape(tmp_path: Path) -> None:
    client = TestClient(create_app(_components(tmp_path)))
    response = client.get(
        "/api/v1/documents/does-not-exist", headers={"X-Request-ID": "request-123"}
    )

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "request-123"
    assert response.json() == {
        "code": "NOT_FOUND",
        "message": "resource was not found",
        "request_id": "request-123",
        "retryable": False,
        "details": {},
    }


def test_exported_openapi_snapshot_matches_app(tmp_path: Path) -> None:
    generated = create_app(_components(tmp_path)).openapi()
    snapshot_path = Path(__file__).resolve().parents[2] / "docs/openapi/openapi-v1.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert snapshot == generated
