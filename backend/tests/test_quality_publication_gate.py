from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.validation import DocumentQualityReport, QualityDisposition
from ragkb.runtime_components import build_runtime_components


def _upload(
    client: TestClient,
    space_id: str,
    *,
    filename: str,
    content: bytes,
    mime: str,
    key: str,
) -> dict[str, str]:
    created = client.post(
        f"/api/v1/spaces/{space_id}/upload-sessions",
        headers={"Idempotency-Key": f"quality-create-{key}"},
        json={
            "filename": filename,
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": mime,
        },
    )
    uploaded = client.put(
        created.json()["upload_path"],
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    return client.post(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={
            "If-Match": uploaded.headers["etag"],
            "Idempotency-Key": f"quality-complete-{key}",
        },
    ).json()


def _worker(components) -> None:
    assert LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        ParserRouter(),
        "quality-worker",
    ).run_once()


def test_missing_or_non_approved_or_stale_review_blocks_publication(tmp_path: Path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    client = TestClient(create_app(components))
    item = _upload(
        client,
        components.space_id,
        filename="quality.txt",
        content=b"synthetic quality",
        mime="text/plain",
        key="text",
    )
    _worker(components)
    version_id = item["document_version_id"]
    publish_path = f"/api/v1/document-versions/{version_id}:publish"

    missing = client.post(publish_path, headers={"Idempotency-Key": "quality-publish"})
    client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": "needs-rework"},
        json={"decision": "NEEDS_REWORK", "comment": "synthetic issue"},
    )
    needs_rework = client.post(publish_path, headers={"Idempotency-Key": "quality-publish"})
    client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": "approved"},
        json={"decision": "APPROVED", "comment": "synthetic approval"},
    )
    with components.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE document_quality_reports SET parser_revision = 'new-quality-revision'
            WHERE version_id = ?
            """,
            (version_id,),
        )
    stale = client.post(publish_path, headers={"Idempotency-Key": "quality-publish"})

    assert missing.status_code == needs_rework.status_code == stale.status_code == 409
    assert missing.json()["message"] == "PUBLICATION_REVIEW_REQUIRED"
    assert needs_rework.json()["message"] == "PUBLICATION_REVIEW_NOT_APPROVED"
    assert stale.json()["message"] == "PUBLICATION_REVIEW_REVISION_MISMATCH"
    assert components.repository.get_document(item["document_id"])["current_version_id"] is None


def test_blocked_real_validation_stub_cannot_be_approved_into_serving(tmp_path: Path) -> None:
    components = build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )
    client = TestClient(create_app(components))
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"synthetic legacy office"
    item = _upload(
        client,
        components.space_id,
        filename="legacy.doc",
        content=ole,
        mime="application/msword",
        key="legacy",
    )
    _worker(components)
    version_id = item["document_version_id"]
    quality = client.get(f"/api/v1/document-versions/{version_id}/quality-report")
    approved = client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": "stub-approval"},
        json={"decision": "APPROVED", "comment": "cannot override stub block"},
    )
    published = client.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "stub-publish"},
    )

    assert quality.json()["disposition"] == "BLOCKED_REAL_VALIDATION"
    assert approved.status_code == 200
    assert published.status_code == 409
    assert published.json()["message"] == "PUBLICATION_QUALITY_BLOCKED_REAL_VALIDATION"
    assert components.repository.get_document(item["document_id"])["current_version_id"] is None


def test_empty_quality_contract_has_zero_coverage_without_division_error() -> None:
    empty = SimpleNamespace(
        document_version_id="empty-version",
        source_format="synthetic-empty",
        parser_revision="empty-parser",
        nodes=(),
        quality_issues=(),
    )
    report = DocumentQualityReport.from_document(empty)  # type: ignore[arg-type]

    assert report.node_count == 0
    assert report.locator_coverage == 0.0
    assert "EMPTY_DOCUMENT" in report.issue_codes
    assert report.disposition is QualityDisposition.DEGRADED
