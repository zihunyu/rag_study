from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.lifecycle import LifecycleService
from ragkb.application.worker import LocalIngestionWorker
from ragkb.contracts.lifecycle import CleanupExecutionResult
from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.lifecycle import CleanupStatus
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.runtime_components import build_runtime_components


def _upload(client: TestClient, space_id: str) -> tuple[str, str]:
    content = b"cleanup postcondition"
    created = client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": "cleanup-create"},
        json={
            "filename": "cleanup.txt",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/plain",
        },
    )
    uploaded = client.put(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    completed = client.post(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "complete"},
    ).json()
    return completed["document_id"], completed["document_version_id"]


def test_local_cleanup_deletes_file_before_completion_and_external_stays_pending(
    tmp_path: Path,
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    client = TestClient(create_app(components))
    document_id, version_id = _upload(client, components.space_id)
    original_key = str(components.repository.get_version(version_id)["original_key"])
    client.delete(f"/api/v1/documents/{document_id}", headers={"Idempotency-Key": "delete"})
    assert components.storage.exists("original", original_key)

    cleaned = client.post(
        f"/api/v1/documents/{document_id}/cleanup/local_file:run",
        headers={"Idempotency-Key": "cleanup-local"},
    )
    blocked = client.post(
        f"/api/v1/documents/{document_id}/cleanup/redis:run",
        headers={"Idempotency-Key": "cleanup-redis"},
    )

    assert cleaned.status_code == 200
    assert cleaned.json()["cleanup"]["local_file"] == "COMPLETED"
    assert not components.storage.exists("original", original_key)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CLEANUP_PENDING_APPROVAL"
    assert (
        components.lifecycle_store.tombstones[document_id].cleanup["redis"]
        is CleanupStatus.PENDING_APPROVAL
    )


def test_cleanup_failure_does_not_consume_key_and_retry_survives_restart(
    tmp_path: Path,
) -> None:
    class _PostconditionExecutor:
        revision = "postcondition-test"

        def __init__(self, postconditions: list[bool]) -> None:
            self.postconditions = postconditions

        def execute(self, document_id: str) -> CleanupExecutionResult:
            return CleanupExecutionResult(True, self.postconditions.pop(0), "POSTCONDITION")

    database = SQLiteDatabase(tmp_path / "control.sqlite3")
    first_store = SQLiteLifecycleStore(database)
    first = LifecycleService(
        first_store,
        "tenant",
        cleanup_executors={"local_file": _PostconditionExecutor([False])},
    )
    first.register_document("doc", "version", trace_id="register")
    first.delete("doc", event_id="delete", trace_id="delete")

    assert (
        first.run_cleanup("doc", "local_file", event_id="retry-key", trace_id="first")
        is CleanupStatus.FAILED
    )
    assert first_store.get_idempotency("tenant", "cleanup:doc:local_file", "retry-key") is None

    restarted_store = SQLiteLifecycleStore(database)
    restarted = LifecycleService(
        restarted_store,
        "tenant",
        cleanup_executors={"local_file": _PostconditionExecutor([True])},
    )
    assert (
        restarted.run_cleanup("doc", "local_file", event_id="retry-key", trace_id="retry")
        is CleanupStatus.COMPLETED
    )
    assert (
        SQLiteLifecycleStore(database).tombstones["doc"].cleanup["local_file"]
        is CleanupStatus.COMPLETED
    )


def test_local_cleanup_removes_worker_artifacts_tracked_residue_and_multiple_versions(
    tmp_path: Path,
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    client = TestClient(create_app(components))
    document_id, version_id = _upload(client, components.space_id)
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        ParserRouter(),
        "cleanup-worker",
    )
    assert worker.run_once() is True
    original_key = str(components.repository.get_version(version_id)["original_key"])
    prefix, _, _ = original_key.rpartition("/original/")
    canonical_key = f"{prefix}/artifacts/canonical-document-v1.json"
    media_key = f"{prefix}/artifacts/images/page-1.png"
    temp_key = f"{prefix}/temp/parser.partial"
    quarantine_key = components.repository.get_session(document_id).quarantine_key
    components.storage.write_bytes("artifacts", media_key, b"image")
    components.storage.write_bytes("temp", temp_key, b"partial")
    components.storage.write_bytes("quarantine", quarantine_key, b"residue")
    components.repository.record_local_content(
        document_id, version_id, "artifacts", media_key, "parsed_media"
    )
    components.repository.record_local_content(
        document_id, version_id, "temp", temp_key, "parser_temp"
    )
    second_original = original_key.replace("/version/1/", "/version/2/")
    second_artifact = canonical_key.replace("/version/1/", "/version/2/")
    components.storage.write_bytes("original", second_original, b"version two")
    components.storage.write_bytes("artifacts", second_artifact, b"{}")
    components.repository.record_local_content(
        document_id, "version-2", "original", second_original, "source_original"
    )
    components.repository.record_local_content(
        document_id, "version-2", "artifacts", second_artifact, "canonical_document"
    )
    legal_hold_key = f"legal-hold/{document_id}.json"
    components.storage.write_bytes("audit", legal_hold_key, b"preserve")
    tracked = components.repository.list_local_content_lineage(document_id)
    assert components.storage.exists("artifacts", canonical_key)

    client.delete(f"/api/v1/documents/{document_id}", headers={"Idempotency-Key": "delete-lineage"})
    response = client.post(
        f"/api/v1/documents/{document_id}/cleanup/local_file:run",
        headers={"Idempotency-Key": "cleanup-lineage"},
    )

    assert response.json()["cleanup"]["local_file"] == "COMPLETED"
    assert all(not components.storage.exists(partition, key) for partition, key in tracked)
    assert components.storage.exists("audit", legal_hold_key)
