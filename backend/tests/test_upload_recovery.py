from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from ragkb.application.uploads import UploadService
from ragkb.application.worker import LocalIngestionWorker
from ragkb.domain.state_machines import JobState, UploadSessionState
from ragkb.engineering_security.file_validation import FileValidationError, UploadFileValidator
from ragkb.engineering_security.malware import SignatureMalwareScanner
from ragkb.runtime_components import build_runtime_components


class _FailingQueue:
    def enqueue(self, *args, **kwargs):
        raise RuntimeError("simulated process interruption before queue commit")


class _ExplodingParserRouter:
    revision = "exploding-parser:g1-test"

    def parse(self, source_format: str, source: Path, document_version_id: str) -> Any:
        raise RuntimeError("simulated parser crash")


def test_complete_resumes_after_promotion_and_document_creation(tmp_path: Path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    interrupted_service = UploadService(
        components.repository,
        _FailingQueue(),  # type: ignore[arg-type]
        components.storage,
        UploadFileValidator(max_size_bytes=1024),
        SignatureMalwareScanner(),
        components.tenant_id,
    )
    content = b"recoverable"
    session = interrupted_service.create_session(
        space_id=components.space_id,
        filename="recover.txt",
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        declared_mime="text/plain",
        idempotency_key="create",
    )
    uploaded = interrupted_service.upload_content(
        session.id, content, expected_row_version=session.row_version
    )

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        interrupted_service.complete(
            session.id,
            expected_row_version=uploaded.row_version,
            idempotency_key="complete",
        )
    recoverable = components.repository.get_session(session.id)
    assert recoverable.state.value == "PROMOTED"
    assert recoverable.document_id is not None
    assert recoverable.document_version_id is not None

    result = components.uploads.reconcile_promoted_sessions()[0]
    from ragkb.runtime import _reconcile_upload_intents

    _reconcile_upload_intents(components)
    assert components.uploads.reconcile_promoted_sessions() == []
    components.lifecycle_store.reload()
    assert result["document_id"] in components.lifecycle_store.documents

    assert result["status"] == "QUEUED"
    assert len(components.repository.get_versions(result["document_id"])) == 1


def test_complete_recovers_when_database_update_fails_after_atomic_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    content = b"recoverable promotion"
    session = components.uploads.create_session(
        space_id=components.space_id,
        filename="promote.txt",
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        declared_mime="text/plain",
        idempotency_key="create-promotion",
    )
    uploaded = components.uploads.upload_content(
        session.id, content, expected_row_version=session.row_version
    )
    original_update = components.repository.update_session
    interrupted = False

    def fail_once_after_promotion(
        session_id: str,
        expected_row_version: int,
        state: UploadSessionState,
        **fields: str | None,
    ):
        nonlocal interrupted
        if state is UploadSessionState.PROMOTED and not interrupted:
            interrupted = True
            raise RuntimeError("simulated database interruption after promotion")
        return original_update(session_id, expected_row_version, state, **fields)

    monkeypatch.setattr(components.repository, "update_session", fail_once_after_promotion)
    with pytest.raises(RuntimeError, match="database interruption"):
        components.uploads.complete(
            session.id,
            expected_row_version=uploaded.row_version,
            idempotency_key="complete-promotion",
        )
    recoverable = components.repository.get_session(session.id)
    assert recoverable.state is UploadSessionState.VALIDATED
    assert not components.storage.exists("quarantine", recoverable.quarantine_key)
    target_key = (
        f"tenant/{recoverable.tenant_id}/space/{recoverable.space_id}/document/"
        f"{recoverable.id}/version/1/original/{recoverable.filename}"
    )
    assert (
        UploadFileValidator.sha256(components.storage.path_for("original", target_key))
        == hashlib.sha256(content).hexdigest()
    )

    monkeypatch.setattr(components.repository, "update_session", original_update)
    result = components.uploads.complete(
        session.id,
        expected_row_version=recoverable.row_version,
        idempotency_key="complete-promotion",
    )

    assert result["status"] == "QUEUED"
    assert components.repository.get_session(session.id).state is UploadSessionState.COMPLETED


def test_complete_rejects_corrupted_existing_original_without_creating_version_or_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    content = b"validated original"
    session = components.uploads.create_session(
        space_id=components.space_id,
        filename="integrity.txt",
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        declared_mime="text/plain",
        idempotency_key="create-integrity",
    )
    uploaded = components.uploads.upload_content(
        session.id, content, expected_row_version=session.row_version
    )
    original_update = components.repository.update_session
    interrupted = False

    def fail_once_after_promotion(
        session_id: str,
        expected_row_version: int,
        state: UploadSessionState,
        **fields: str | None,
    ):
        nonlocal interrupted
        if state is UploadSessionState.PROMOTED and not interrupted:
            interrupted = True
            raise RuntimeError("simulated database interruption after promotion")
        return original_update(session_id, expected_row_version, state, **fields)

    monkeypatch.setattr(components.repository, "update_session", fail_once_after_promotion)
    with pytest.raises(RuntimeError, match="database interruption"):
        components.uploads.complete(
            session.id,
            expected_row_version=uploaded.row_version,
            idempotency_key="complete-integrity",
        )
    recoverable = components.repository.get_session(session.id)
    target_key = (
        f"tenant/{recoverable.tenant_id}/space/{recoverable.space_id}/document/"
        f"{recoverable.id}/version/1/original/{recoverable.filename}"
    )
    components.storage.path_for("original", target_key).write_bytes(b"corrupted replacement")
    monkeypatch.setattr(components.repository, "update_session", original_update)

    with pytest.raises(FileValidationError) as rejected:
        components.uploads.complete(
            session.id,
            expected_row_version=recoverable.row_version,
            idempotency_key="complete-integrity",
        )

    assert rejected.value.code == "DOC_ORIGINAL_HASH_MISMATCH"
    failed = components.repository.get_session(session.id)
    assert failed.state is UploadSessionState.FAILED
    assert failed.error_code == "DOC_ORIGINAL_HASH_MISMATCH"
    with components.database.connect() as connection:
        version_count = connection.execute(
            "SELECT COUNT(*) AS count FROM document_versions WHERE document_id = ?",
            (session.id,),
        ).fetchone()
        job_count = connection.execute(
            "SELECT COUNT(*) AS count FROM job_queue WHERE operation = 'process_document'"
        ).fetchone()
    assert int(version_count["count"]) == 0
    assert int(job_count["count"]) == 0


def test_worker_marks_version_failed_after_final_unexpected_error(tmp_path: Path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    service = UploadService(
        components.repository,
        components.queue,
        components.storage,
        UploadFileValidator(max_size_bytes=1024),
        SignatureMalwareScanner(),
        components.tenant_id,
        queue_max_attempts=1,
    )
    content = b"parser failure"
    session = service.create_session(
        space_id=components.space_id,
        filename="failure.txt",
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        declared_mime="text/plain",
        idempotency_key="create-failure",
    )
    uploaded = service.upload_content(session.id, content, expected_row_version=session.row_version)
    result = service.complete(
        session.id,
        expected_row_version=uploaded.row_version,
        idempotency_key="complete-failure",
    )
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        _ExplodingParserRouter(),  # type: ignore[arg-type]
        "failure-worker",
    )

    assert worker.run_once() is True
    assert worker.last_failure is not None
    assert worker.last_failure.error_code == "INGEST_UNEXPECTED"
    assert worker.last_failure.exception_type == "RuntimeError"

    assert components.queue.get(result["job_id"]).state is JobState.FAILED_FINAL  # type: ignore[union-attr]
    version = components.repository.get_version(result["document_version_id"])
    assert version["processing_state"] == "FAILED"
    assert version["parser_revision"] == _ExplodingParserRouter.revision
