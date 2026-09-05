from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.lifecycle import CleanupStatus, LifecycleState
from ragkb.runtime_components import build_runtime_components


class _PrincipalAuthenticator:
    revision = "test-principal"

    def __init__(self, principal: RequestPrincipal) -> None:
        self.principal = principal

    def authenticate(self, authorization_header: str | None) -> RequestPrincipal:
        del authorization_header
        return self.principal


def _components(tmp_path: Path):
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )


def _upload(
    client: TestClient,
    space_id: str,
    *,
    key: str = "fact",
    document_id: str | None = None,
    document_etag: str | None = None,
) -> tuple[str, str, str]:
    content = f"fact source {key}".encode()
    create_path = (
        f"/api/v1/documents/{document_id}/versions/upload-sessions"
        if document_id
        else f"/api/v1/spaces/{space_id}/upload-sessions"
    )
    created = client.post(
        create_path,
        headers={
            "Idempotency-Key": f"create-{key}",
            **({"If-Match": document_etag} if document_etag else {}),
        },
        json={
            "filename": f"{key}.txt",
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
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": f"complete-{key}"},
    ).json()
    return completed["document_id"], completed["document_version_id"], completed["job_id"]


def _process_next(components, client: TestClient, version_id: str) -> None:
    assert LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        ParserRouter(),
        "fact-source-worker",
        chunker=components.chunker,
        indexing_sink=components.indexing_sink,
    ).run_once()
    assert (
        client.post(
            f"/api/v1/document-versions/{version_id}/review",
            headers={"Idempotency-Key": f"approve-{version_id}"},
            json={
                "decision": "APPROVED",
                "comment": "synthetic",
                "security_projection": {
                    "visibility": "TENANT",
                    "classification_level": 0,
                    "acl_scope_tokens": [],
                },
            },
        ).status_code
        == 200
    )


def _client_for_roles(components, *roles: str) -> TestClient:
    principal = RequestPrincipal(
        tenant_id=components.tenant_id,
        user_id="test-user",
        roles=tuple(roles),
        scope_tokens=tuple(f"role:{role}" for role in roles) + (
            (f"space:{components.space_id}:manage",) if "knowledge_maintainer" in roles else ()
        ),
        auth_mode="test",
    )
    return TestClient(
        create_app(replace(components, authenticator=_PrincipalAuthenticator(principal)))
    )


def test_draft_is_hidden_from_reader_but_available_to_management_until_publish(
    tmp_path: Path,
) -> None:
    components = _components(tmp_path)
    admin = TestClient(create_app(components))
    document_id, version_id, job_id = _upload(admin, components.space_id)
    reader = _client_for_roles(components, "reader")
    maintainer = _client_for_roles(components, "knowledge_maintainer")

    record = components.lifecycle_store.documents[document_id]
    assert record.lifecycle_state is LifecycleState.DRAFT
    assert record.visible is False
    assert reader.get(f"/api/v1/documents/{document_id}").status_code == 404
    assert reader.get(f"/api/v1/documents/{document_id}/versions").status_code == 404
    assert maintainer.get(f"/api/v1/documents/{document_id}").status_code == 200
    assert maintainer.get(f"/api/v1/ingestion-jobs/{job_id}").status_code == 200
    _process_next(components, admin, version_id)

    published = admin.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "publish-draft"},
    )

    assert published.status_code == 200
    assert reader.get(f"/api/v1/documents/{document_id}").status_code == 200


def test_publish_second_version_and_rollback_keep_fact_source_atomic_and_restart_safe(
    tmp_path: Path,
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    document_id, first_version, _ = _upload(client, components.space_id)
    _process_next(components, client, first_version)
    client.post(
        f"/api/v1/document-versions/{first_version}:publish",
        headers={"Idempotency-Key": "publish-v1"},
    )
    document_etag = client.get(f"/api/v1/documents/{document_id}").headers["etag"]
    _, second_version, _ = _upload(
        client,
        components.space_id,
        key="second",
        document_id=document_id,
        document_etag=document_etag,
    )
    _process_next(components, client, second_version)

    second = client.post(
        f"/api/v1/document-versions/{second_version}:publish",
        headers={"Idempotency-Key": "publish-v2"},
    )
    rollback = client.post(
        f"/api/v1/documents/{document_id}:rollback",
        headers={"Idempotency-Key": "rollback-v1"},
        json={"version_id": first_version},
    )

    assert second.status_code == 200
    assert rollback.status_code == 200
    fact = components.repository.get_document(document_id)
    states = {
        str(item["id"]): str(item["publication_state"])
        for item in components.repository.get_versions(document_id)
    }
    assert fact["current_version_id"] == first_version
    assert states == {first_version: "SERVING", second_version: "SUPERSEDED"}
    assert components.lifecycle_store.documents[document_id].active_version_id == first_version

    restarted = _components(tmp_path)
    assert restarted.repository.get_document(document_id)["current_version_id"] == first_version
    assert restarted.lifecycle_store.documents[document_id].active_version_id == first_version
    assert restarted.lifecycle_store.is_accessible(document_id)


def test_publication_sqlite_failure_rolls_back_lifecycle_and_fact_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    document_id, version_id, _ = _upload(client, components.space_id)
    _process_next(components, client, version_id)
    components.lifecycle_store.reload()

    @contextmanager
    def failed_transaction(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        del immediate
        raise sqlite3.OperationalError("publication transaction failed")
        yield  # pragma: no cover

    monkeypatch.setattr(components.database, "transaction", failed_transaction)
    with pytest.raises(sqlite3.OperationalError, match="publication"):
        components.lifecycle_service.publish(
            document_id, version_id, event_id="publish-fail", trace_id="failure"
        )

    assert (
        components.lifecycle_store.documents[document_id].lifecycle_state is LifecycleState.STAGED
    )
    assert components.lifecycle_store.is_accessible(document_id) is False
    assert components.repository.get_document(document_id)["current_version_id"] is None
    assert components.repository.get_version(version_id)["publication_state"] == "DRAFT"


def test_draft_delete_is_irreversible_and_repeated_delete_preserves_cleanup(
    tmp_path: Path,
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    document_id, version_id, _ = _upload(client, components.space_id)
    deleted = client.delete(
        f"/api/v1/documents/{document_id}", headers={"Idempotency-Key": "delete-one"}
    )
    cleaned = client.post(
        f"/api/v1/documents/{document_id}/cleanup/local_file:run",
        headers={"Idempotency-Key": "cleanup"},
    )
    repeated = client.delete(
        f"/api/v1/documents/{document_id}", headers={"Idempotency-Key": "delete-two"}
    )

    assert deleted.status_code == cleaned.status_code == repeated.status_code == 200
    assert repeated.json()["cleanup"]["local_file"] == "COMPLETED"
    assert (
        components.lifecycle_store.tombstones[document_id].cleanup["local_file"]
        is CleanupStatus.COMPLETED
    )

    commands = (
        client.post(
            f"/api/v1/document-versions/{version_id}:publish",
            headers={"Idempotency-Key": "publish-after-delete"},
        ),
        client.post(
            f"/api/v1/documents/{document_id}:rollback",
            headers={"Idempotency-Key": "rollback-after-delete"},
            json={"version_id": version_id},
        ),
        client.put(
            f"/api/v1/resources/document/{document_id}/permissions",
            headers={"Idempotency-Key": "acl-after-delete", "If-Match": '"1"'},
            json={"security_projection": {"visibility": "TENANT", "classification_level": 0,
                                          "acl_scope_tokens": []}},
        ),
        client.post(
            f"/api/v1/documents/{document_id}:revoke",
            headers={"Idempotency-Key": "revoke-after-delete"},
        ),
    )
    assert {response.status_code for response in commands} == {409}
    assert (
        components.lifecycle_store.documents[document_id].lifecycle_state is LifecycleState.DELETED
    )
    assert components.lifecycle_store.is_tombstoned(document_id)
    assert (
        _components(tmp_path).lifecycle_store.documents[document_id].lifecycle_state
        is LifecycleState.DELETED
    )
