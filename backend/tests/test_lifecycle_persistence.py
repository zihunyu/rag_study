from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.lifecycle import LifecycleIdempotencyConflict, LifecycleService
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.runtime_components import build_runtime_components


def _components(tmp_path: Path):
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )


def _upload(client: TestClient, space_id: str) -> tuple[str, str]:
    content = b"persistent lifecycle"
    created = client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": "create-persistent"},
        json={
            "filename": "persistent.txt",
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


def test_delete_tombstone_blocks_document_and_versions_before_and_after_restart(
    tmp_path: Path,
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    document_id, version_id = _upload(client, components.space_id)
    assert version_id

    deleted = client.delete(
        f"/api/v1/documents/{document_id}",
        headers={"Idempotency-Key": "delete"},
    )

    assert deleted.status_code == 200
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
    assert client.get(f"/api/v1/documents/{document_id}/versions").status_code == 404

    restarted = _components(tmp_path)
    restarted_client = TestClient(create_app(restarted))
    assert restarted.lifecycle_store.is_tombstoned(document_id)
    assert restarted_client.get(f"/api/v1/documents/{document_id}").status_code == 404
    with restarted.database.connect() as connection:
        outbox = connection.execute(
            "SELECT target_store, state FROM cleanup_outbox WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    assert len(outbox) == 4
    assert {str(row["state"]) for row in outbox} == {"PENDING", "PENDING_APPROVAL"}


def test_acl_idempotency_is_stable_conflicting_and_restart_safe(tmp_path: Path) -> None:
    from test_lifecycle_fact_source import _process_next, _upload
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    document_id, version_id, _ = _upload(client, components.space_id)
    _process_next(components, client, version_id)
    published = client.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "publish"},
    )
    assert published.status_code == 200
    request = {"security_projection": {"visibility": "RESTRICTED", "classification_level": 1,
                                       "acl_scope_tokens": ["group:legal"]}}
    headers = {"Idempotency-Key": "acl-key", "If-Match": f'\"{published.json()["row_version"]}\"'}
    path = f"/api/v1/resources/document/{document_id}/permissions"
    first = client.put(path, headers=headers, json=request)
    replay = client.put(path, headers=headers, json=request)
    conflict = client.put(path, headers=headers, json={"security_projection": {
        **request["security_projection"], "classification_level": 2}})
    assert first.status_code == 200, first.text
    assert first.json()["visible"] is True
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "CONFLICT_IDEMPOTENCY_KEY"
    restarted = _components(tmp_path)
    after_restart = TestClient(create_app(restarted)).put(path, headers=headers, json=request)
    assert after_restart.json() == first.json()
    assert restarted.lifecycle_store.is_accessible(document_id)


def test_all_lifecycle_operations_are_idempotent_and_validation_does_not_consume_key(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "control.sqlite3")
    store = SQLiteLifecycleStore(database)
    service = LifecycleService(store, "tenant-1")
    service.register_document("doc", "v1", trace_id="trace")

    published = service.publish("doc", "v2", event_id="publish", trace_id="trace")
    assert service.publish("doc", "v2", event_id="publish", trace_id="other").row_version == (
        published.row_version
    )
    with pytest.raises(LifecycleIdempotencyConflict):
        service.publish("doc", "v3", event_id="publish", trace_id="trace")

    with pytest.raises(ValueError):
        service.rollback("doc", "missing", event_id="rollback", trace_id="trace")
    rolled_back = service.rollback("doc", "v1", event_id="rollback", trace_id="trace")
    assert rolled_back.active_version_id == "v1"
    assert service.rollback("doc", "v1", event_id="rollback", trace_id="again").row_version == (
        rolled_back.row_version
    )

    revoked = service.revoke("doc", event_id="revoke", trace_id="trace")
    assert service.revoke("doc", event_id="revoke", trace_id="again").row_version == (
        revoked.row_version
    )
    tombstone = service.delete("doc", event_id="delete", trace_id="trace")
    assert service.delete("doc", event_id="delete", trace_id="again").cleanup == (tombstone.cleanup)
    restarted = SQLiteLifecycleStore(database)
    assert restarted.is_tombstoned("doc")
    assert restarted.tombstones["doc"].cleanup["redis"].value == "PENDING_APPROVAL"
