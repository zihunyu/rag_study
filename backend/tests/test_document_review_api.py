from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.auth import RequestPrincipal
from ragkb.runtime_components import build_runtime_components


class _ReaderAuth:
    revision = "review-reader"

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    def authenticate(self, authorization_header: str | None) -> RequestPrincipal:
        del authorization_header
        return RequestPrincipal(
            self.tenant_id,
            "reader",
            ("reader",),
            ("role:reader",),
            "test",
        )


def test_quality_report_and_single_document_review_are_persisted_and_idempotent(
    tmp_path: Path,
) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    client = TestClient(create_app(components))
    content = b"synthetic review document"
    created = client.post(
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        headers={"Idempotency-Key": "review-create"},
        json={
            "filename": "review.txt",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/plain",
        },
    )
    uploaded = client.put(
        created.json()["upload_path"],
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    completed = client.post(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": "complete"},
    ).json()
    assert LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        ParserRouter(),
        "review-worker",
    ).run_once()
    version_id = completed["document_version_id"]

    quality = client.get(f"/api/v1/document-versions/{version_id}/quality-report")
    body = {"decision": "APPROVED", "comment": "synthetic review only"}
    first = client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": "review-key"},
        json=body,
    )
    replay = client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": "review-key"},
        json=body,
    )
    conflict = client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": "review-key"},
        json={**body, "decision": "REJECTED"},
    )
    reader = TestClient(
        create_app(replace(components, authenticator=_ReaderAuth(components.tenant_id)))
    )
    forbidden = reader.get(f"/api/v1/document-versions/{version_id}/quality-report")

    assert quality.status_code == 200
    assert quality.json()["locator_coverage"] == 1.0
    assert quality.json()["real_acceptance"] is False
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert forbidden.status_code == 403
