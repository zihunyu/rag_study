from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.documents import CanonicalDocument
from ragkb.runtime import run_worker_iteration
from ragkb.runtime_components import RuntimeComponents, build_runtime_components


def _components(tmp_path: Path) -> RuntimeComponents:
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )


def _enqueue_text(
    components: RuntimeComponents, *, filename: str, content: bytes, key: str
) -> dict[str, object]:
    session = components.uploads.create_session(
        space_id=components.space_id,
        filename=filename,
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        declared_mime="text/plain",
        idempotency_key=f"create-{key}",
    )
    uploaded = components.uploads.upload_content(
        session.id, content, expected_row_version=session.row_version
    )
    return components.uploads.complete(
        session.id,
        expected_row_version=uploaded.row_version,
        idempotency_key=f"complete-{key}",
    )


class _SelectiveParserRouter:
    revision = "selective-parser:g1-test"

    def __init__(self) -> None:
        self.delegate = ParserRouter()

    def parse(
        self, source_format: str, source: Path, document_version_id: str
    ) -> CanonicalDocument:
        if source.name == "bad.txt":
            raise RuntimeError("sensitive bad document content must not be logged")
        return self.delegate.parse(source_format, source, document_version_id)


class _BlockingParserRouter:
    revision = "blocking-parser:g1-test"

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release
        self.delegate = ParserRouter()

    def parse(
        self, source_format: str, source: Path, document_version_id: str
    ) -> CanonicalDocument:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test parser release timed out")
        return self.delegate.parse(source_format, source, document_version_id)


def test_bad_task_is_safely_recorded_and_next_good_task_still_runs(tmp_path: Path) -> None:
    components = _components(tmp_path)
    bad = _enqueue_text(components, filename="bad.txt", content=b"bad body", key="bad")
    good = _enqueue_text(components, filename="good.txt", content=b"good body", key="good")
    with components.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE job_queue SET max_attempts = 1 WHERE id = ?", (str(bad["job_id"]),)
        )
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        _SelectiveParserRouter(),
        "resilient-worker",
    )
    error_stream = io.StringIO()

    bad_iteration = run_worker_iteration(worker, error_stream=error_stream)
    good_iteration = run_worker_iteration(worker, error_stream=error_stream)

    assert bad_iteration.failed is True
    assert good_iteration.failed is False
    assert components.queue.get(str(bad["job_id"])).state.value == "FAILED_FINAL"  # type: ignore[union-attr]
    assert components.queue.get(str(good["job_id"])).state.value == "SUCCEEDED"  # type: ignore[union-attr]
    assert (
        components.repository.get_version(str(good["document_version_id"]))["processing_state"]
        == "VALIDATED"
    )
    safe_error = error_stream.getvalue()
    assert "WORKER_TASK_FAILED" in safe_error
    assert "sensitive bad document" not in safe_error
    assert "bad body" not in safe_error


def test_running_cancel_is_acknowledged_before_artifact_or_chunk_write(tmp_path: Path) -> None:
    components = _components(tmp_path)
    completed = _enqueue_text(
        components,
        filename="running.txt",
        content=b"cancel while parsing",
        key="running",
    )
    job_id = str(completed["job_id"])
    version_id = str(completed["document_version_id"])
    started = threading.Event()
    release = threading.Event()
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        _BlockingParserRouter(started, release),
        "blocking-worker",
    )
    errors: list[BaseException] = []

    def run() -> None:
        try:
            worker.run_once()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(timeout=5)
    client = TestClient(create_app(components))
    running = client.get(f"/api/v1/ingestion-jobs/{job_id}")
    cancelled = client.post(
        f"/api/v1/ingestion-jobs/{job_id}:cancel",
        headers={
            "If-Match": running.headers["etag"],
            "Idempotency-Key": "cancel-running",
        },
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["state"] == "CANCEL_REQUESTED"
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    assert components.queue.get(job_id).state.value == "CANCELLED"  # type: ignore[union-attr]
    assert components.repository.get_version(version_id)["processing_state"] == "CANCELLED"
    with components.database.connect() as connection:
        chunk_count = connection.execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE version_id = ?", (version_id,)
        ).fetchone()
    assert int(chunk_count["count"]) == 0
    version = components.repository.get_version(version_id)
    original_key = str(version["original_key"])
    filename = Path(original_key).name
    artifact_key = original_key.replace(
        f"original/{filename}", "artifacts/canonical-document-v1.json"
    )
    assert not components.storage.exists("artifacts", artifact_key)


def test_worker_once_returns_nonzero_and_emits_only_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ragkb import runtime

    components = _components(tmp_path)

    class _FailingWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run_once(self) -> bool:
            raise RuntimeError("secret document body")

    monkeypatch.setattr(runtime, "build_runtime_components", lambda: components)
    monkeypatch.setattr(runtime, "LocalIngestionWorker", _FailingWorker)

    assert runtime.run_worker(["--once"]) == 1
    captured = capsys.readouterr()
    assert "WORKER_TASK_FAILED" in captured.err
    assert "secret document body" not in captured.err
    assert '"failed": true' in captured.out
