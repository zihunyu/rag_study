from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.uploads import UploadService
from ragkb.engineering_security.file_validation import FileValidationError, UploadFileValidator
from ragkb.engineering_security.malware import SignatureMalwareScanner
from ragkb.runtime_components import RuntimeComponents, build_runtime_components


async def _chunks(*values: bytes):
    for value in values:
        yield value


def _service(
    tmp_path, *, max_bytes: int, quota_bytes: int
) -> tuple[UploadService, RuntimeComponents]:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    service = UploadService(
        components.repository,
        components.queue,
        components.storage,
        UploadFileValidator(max_size_bytes=max_bytes),
        SignatureMalwareScanner(),
        components.tenant_id,
        quarantine_max_bytes=quota_bytes,
        max_concurrent_streams=1,
    )
    return service, components


def _session(
    service,
    components,
    content: bytes,
    *,
    digest: str | None = None,
    filename: str = "stream.bin",
    key: str = "stream-create",
):
    return service.create_session(
        space_id=components.space_id,
        filename=filename,
        expected_size=len(content),
        expected_sha256=digest or hashlib.sha256(content).hexdigest(),
        declared_mime="application/octet-stream",
        idempotency_key=key,
    )


def test_stream_hard_limit_stops_mid_body_and_removes_temporary_file(tmp_path) -> None:
    service, components = _service(tmp_path, max_bytes=5, quota_bytes=20)
    session = _session(service, components, b"12345")

    with pytest.raises(FileValidationError, match="configured size limit") as error:
        asyncio.run(
            service.upload_content_stream(
                session.id,
                _chunks(b"123", b"456"),
                expected_row_version=session.row_version,
                content_length=None,
            )
        )

    assert error.value.code == "DOC_SIZE_LIMIT"
    assert not components.storage.exists("quarantine", session.quarantine_key)
    assert list((components.storage.root / "quarantine").rglob("*.uploading")) == []


def test_stream_hash_mismatch_deletes_quarantine_object(tmp_path) -> None:
    service, components = _service(tmp_path, max_bytes=10, quota_bytes=20)
    session = _session(service, components, b"abc", digest="0" * 64)

    with pytest.raises(FileValidationError, match="hash differs") as error:
        asyncio.run(
            service.upload_content_stream(
                session.id,
                _chunks(b"a", b"bc"),
                expected_row_version=session.row_version,
                content_length=3,
            )
        )

    assert error.value.code == "DOC_HASH_MISMATCH"
    assert not components.storage.exists("quarantine", session.quarantine_key)


def test_quarantine_quota_rejects_before_consuming_stream(tmp_path) -> None:
    service, components = _service(tmp_path, max_bytes=10, quota_bytes=10)
    components.storage.write_bytes("quarantine", "existing.bin", b"12345678")
    session = _session(service, components, b"abc")
    consumed = False

    async def body():
        nonlocal consumed
        consumed = True
        yield b"abc"

    with pytest.raises(FileValidationError, match="capacity exhausted") as error:
        asyncio.run(
            service.upload_content_stream(
                session.id,
                body(),
                expected_row_version=session.row_version,
                content_length=3,
            )
        )

    assert error.value.code == "UPLOAD_QUARANTINE_QUOTA_EXCEEDED"
    assert consumed is False


def test_upload_service_applies_concurrency_backpressure(tmp_path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )

    class _TrackingStorage:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0

        def __getattr__(self, name: str):
            return getattr(components.storage, name)

        async def write_stream(self, *args, **kwargs):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.01)
            try:
                return await components.storage.write_stream(*args, **kwargs)
            finally:
                self.active -= 1

    storage = _TrackingStorage()
    service = UploadService(
        components.repository,
        components.queue,
        storage,
        UploadFileValidator(max_size_bytes=10),
        SignatureMalwareScanner(),
        components.tenant_id,
        quarantine_max_bytes=100,
        max_concurrent_streams=1,
    )
    first = _session(service, components, b"one", filename="one.txt", key="one")
    second = _session(service, components, b"two", filename="two.txt", key="two")

    async def upload_both():
        return await asyncio.gather(
            service.upload_content_stream(
                first.id,
                _chunks(b"one"),
                expected_row_version=first.row_version,
                content_length=3,
            ),
            service.upload_content_stream(
                second.id,
                _chunks(b"two"),
                expected_row_version=second.row_version,
                content_length=3,
            ),
        )

    asyncio.run(upload_both())

    assert storage.maximum == 1


def test_api_streams_octet_body_and_content_length_fast_rejects(tmp_path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    components.uploads.validator = UploadFileValidator(max_size_bytes=5)
    client = TestClient(create_app(components))
    content = b"abc"
    created = client.post(
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        headers={"Idempotency-Key": "api-stream-create"},
        json={
            "filename": "stream.txt",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/plain",
        },
    )
    session_id = created.json()["upload_session_id"]
    rejected = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"], "Content-Length": "6"},
        content=content,
    )
    assert rejected.status_code == 413
    assert rejected.json()["code"] == "DOC_SIZE_LIMIT"

    uploaded = client.put(
        f"/api/v1/upload-sessions/{session_id}/content",
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    assert uploaded.status_code == 200
    assert components.storage.size(
        "quarantine", components.repository.get_session(session_id).quarantine_key
    ) == len(content)
