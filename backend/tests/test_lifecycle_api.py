from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.runtime_components import build_runtime_components


def _components(tmp_path: Path):
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )


def _uploaded_version(client: TestClient, space_id: str) -> tuple[str, str]:
    content = b"lifecycle contract"
    created = client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": "create-lifecycle"},
        json={
            "filename": "lifecycle.txt",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/plain",
        },
    )
    session_id = created.json()["upload_session_id"]
    uploaded = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    completed = client.post(
        f"/api/v1/upload-sessions/{session_id}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "complete"},
    ).json()
    return completed["document_id"], completed["document_version_id"]


def _process_next(components, client: TestClient, version_id: str) -> None:
    assert LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        ParserRouter(),
        "lifecycle-api-worker",
    ).run_once()
    assert (
        client.post(
            f"/api/v1/document-versions/{version_id}/review",
            headers={"Idempotency-Key": f"approve-{version_id}"},
            json={"decision": "APPROVED", "comment": "synthetic"},
        ).status_code
        == 200
    )


def test_publish_permissions_delete_and_audit_api_are_fail_closed(tmp_path: Path) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    document_id, version_id = _uploaded_version(client, components.space_id)
    _process_next(components, client, version_id)

    published = client.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "publish"},
    )
    transition = client.put(
        f"/api/v1/resources/document/{document_id}/permissions",
        headers={"Idempotency-Key": "acl-2", "X-Request-ID": "trace-acl"},
        json={
            "target_acl_revision": 2,
            "required_watermark": 10,
            "observed_watermark": 9,
            "projection_ok": True,
        },
    )
    deleted = client.delete(
        f"/api/v1/documents/{document_id}",
        headers={"Idempotency-Key": "delete", "X-Request-ID": "trace-delete"},
    )
    audit = client.get("/api/v1/admin/audit-events")

    assert published.status_code == 200
    assert transition.json()["lifecycle_state"] == "SECURITY_TRANSITION"
    assert transition.json()["visible"] is False
    assert deleted.status_code == 200
    assert deleted.json()["lifecycle_state"] == "DELETED"
    assert set(deleted.json()["cleanup"]) == {
        "local_file",
        "mysql",
        "redis",
        "zilliz_projection",
    }
    assert deleted.json()["cleanup"]["local_file"] == "PENDING"
    assert {
        deleted.json()["cleanup"][target] for target in ("mysql", "redis", "zilliz_projection")
    } == {"PENDING_APPROVAL"}
    assert [item["sequence"] for item in audit.json()] == list(range(1, len(audit.json()) + 1))
    assert any(item["trace_id"] == "trace-delete" for item in audit.json())


def test_rollback_and_openapi_management_contract(tmp_path: Path) -> None:
    components = _components(tmp_path)
    components.lifecycle_service.publication_readiness = None
    components.lifecycle_service.register_document("doc", "v1", trace_id="trace")
    components.lifecycle_service.publish("doc", "v2", event_id="publish", trace_id="trace")
    client = TestClient(create_app(components))

    rolled_back = client.post(
        "/api/v1/documents/doc:rollback",
        headers={"Idempotency-Key": "rollback"},
        json={"version_id": "v1"},
    )
    schema = client.app.openapi()

    assert rolled_back.status_code == 200
    assert rolled_back.json()["active_version_id"] == "v1"
    for path in (
        "/api/v1/document-versions/{version_id}:publish",
        "/api/v1/documents/{document_id}:rollback",
        "/api/v1/resources/document/{document_id}/permissions",
        "/api/v1/documents/{document_id}",
        "/api/v1/documents/{document_id}/versions/upload-sessions",
        "/api/v1/admin/audit-events",
    ):
        assert path in schema["paths"]
