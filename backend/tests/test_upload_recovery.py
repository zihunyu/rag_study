from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ragkb.application.uploads import UploadService
from ragkb.engineering_security.file_validation import UploadFileValidator
from ragkb.engineering_security.malware import SignatureMalwareScanner
from ragkb.runtime_components import build_runtime_components


class _FailingQueue:
    def enqueue(self, *args, **kwargs):
        raise RuntimeError("simulated process interruption before queue commit")


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

    result = components.uploads.complete(
        session.id,
        expected_row_version=recoverable.row_version,
        idempotency_key="complete",
    )

    assert result["status"] == "QUEUED"
    assert len(components.repository.get_versions(result["document_id"])) == 1
