from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ragkb.api.app import create_app
from ragkb.application.worker import LocalIngestionWorker
from ragkb.document_processing.parsers import ParserRouter
from ragkb.domain.auth import RequestPrincipal
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
from ragkb.runtime_components import build_runtime_components


class _PrincipalAuthenticator:
    revision = "publication-test-principal"

    def __init__(self, roles: tuple[str, ...], tenant_id: str) -> None:
        self.principal = RequestPrincipal(
            tenant_id=tenant_id,
            user_id="publication-user",
            roles=roles,
            scope_tokens=tuple(f"role:{role}" for role in roles),
            auth_mode="test",
        )

    def authenticate(self, authorization_header: str | None) -> RequestPrincipal:
        del authorization_header
        return self.principal


def _components(tmp_path: Path):
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
    )


def _create_and_complete(
    client: TestClient,
    create_path: str,
    content: bytes,
    *,
    key: str,
    create_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    created = client.post(
        create_path,
        headers={"Idempotency-Key": f"create-{key}", **(create_headers or {})},
        json={
            "filename": f"{key}.txt",
            "expected_size": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "declared_mime": "text/plain",
        },
    )
    assert created.status_code in {200, 201}
    uploaded = client.put(
        created.json()["upload_path"],
        headers={"If-Match": created.headers["etag"]},
        content=content,
    )
    completed = client.post(
        f"/api/v1/upload-sessions/{created.json()['upload_session_id']}:complete",
        headers={"If-Match": uploaded.headers["etag"], "Idempotency-Key": f"complete-{key}"},
    )
    assert completed.status_code == 202
    return completed.json()


def _run_worker(components) -> None:
    worker = LocalIngestionWorker(
        components.queue,
        components.repository,
        components.storage,
        ParserRouter(),
        "publication-worker",
    )
    assert worker.run_once() is True


def _approve(client: TestClient, version_id: str, key: str) -> None:
    response = client.post(
        f"/api/v1/document-versions/{version_id}/review",
        headers={"Idempotency-Key": f"approve-{key}"},
        json={
            "decision": "APPROVED",
            "comment": "synthetic local review",
            "security_projection": {
                "visibility": "TENANT",
                "classification_level": 0,
                "acl_scope_tokens": [],
            },
        },
    )
    assert response.status_code == 200


def test_processing_publish_is_rejected_without_side_effects_then_staged_publish_succeeds(
    tmp_path: Path,
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    uploaded = _create_and_complete(
        client,
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        b"version one",
        key="v1",
    )
    document_id = uploaded["document_id"]
    version_id = uploaded["document_version_id"]
    audit_before = len(components.lifecycle_store.audit_events)

    blocked = client.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "publish-processing"},
    )

    assert blocked.status_code == 409
    assert blocked.json()["message"] == "PUBLICATION_VERSION_NOT_VALIDATED"
    assert components.repository.get_document(document_id)["current_version_id"] is None
    assert components.repository.get_version(version_id)["publication_state"] == "DRAFT"
    assert len(components.lifecycle_store.audit_events) == audit_before
    assert (
        components.lifecycle_store.get_idempotency(
            components.tenant_id, f"publish:{document_id}", "publish-processing"
        )
        is None
    )

    _run_worker(components)
    _approve(client, version_id, "v1")
    published = client.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "publish-processing"},
    )
    before_noop = components.repository.get_document(document_id)["row_version"]
    audit_before_noop = len(components.lifecycle_store.audit_events)
    noop = client.post(
        f"/api/v1/document-versions/{version_id}:publish",
        headers={"Idempotency-Key": "publish-ready-new-key"},
    )

    assert published.status_code == noop.status_code == 200
    assert components.repository.get_document(document_id)["row_version"] == before_noop
    assert len(components.lifecycle_store.audit_events) == audit_before_noop


@pytest.mark.parametrize(
    ("column", "value", "error_code"),
    [
        ("projection_state", "BUILDING", "PUBLICATION_PROJECTION_NOT_STAGED"),
        ("generation_id", "wrong-generation", "PUBLICATION_GENERATION_MISMATCH"),
        ("observed_watermark", -1, "PUBLICATION_WATERMARK_NOT_READY"),
        ("observed_checksum", "mismatch", "PUBLICATION_CHECKSUM_MISMATCH"),
    ],
)
def test_publication_readiness_failures_are_409_and_side_effect_free(
    tmp_path: Path,
    column: str,
    value: object,
    error_code: str,
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    uploaded = _create_and_complete(
        client,
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        b"readiness",
        key=column,
    )
    _run_worker(components)
    _approve(client, uploaded["document_version_id"], column)
    with components.database.transaction(immediate=True) as connection:
        connection.execute(
            f"UPDATE publication_candidates SET {column} = ? WHERE version_id = ?",  # noqa: S608
            (value, uploaded["document_version_id"]),
        )
    fact_before = components.repository.get_document(uploaded["document_id"])

    response = client.post(
        f"/api/v1/document-versions/{uploaded['document_version_id']}:publish",
        headers={"Idempotency-Key": "not-ready"},
    )

    assert response.status_code == 409
    assert response.json()["message"] == error_code
    assert components.repository.get_document(uploaded["document_id"]) == fact_before


@pytest.mark.parametrize(
    "processing_state", ["PROCESSING", "FAILED", "QUARANTINED", "CANCELLED", "DRAFT"]
)
def test_non_validated_processing_states_can_never_publish(
    tmp_path: Path, processing_state: str
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    uploaded = _create_and_complete(
        client,
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        b"processing gate",
        key=processing_state,
    )
    _run_worker(components)
    _approve(client, uploaded["document_version_id"], processing_state)
    with components.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE document_versions SET processing_state = ? WHERE id = ?",
            (processing_state, uploaded["document_version_id"]),
        )
    audit_before = len(components.lifecycle_store.audit_events)

    response = client.post(
        f"/api/v1/document-versions/{uploaded['document_version_id']}:publish",
        headers={"Idempotency-Key": "processing-state-blocked"},
    )

    assert response.status_code == 409
    assert response.json()["message"] == "PUBLICATION_VERSION_NOT_VALIDATED"
    assert components.repository.get_document(uploaded["document_id"])["current_version_id"] is None
    assert len(components.lifecycle_store.audit_events) == audit_before


def test_existing_document_new_version_api_keeps_old_serving_then_publishes_and_rolls_back(
    tmp_path: Path,
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    first = _create_and_complete(
        client,
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        b"first version",
        key="first",
    )
    _run_worker(components)
    _approve(client, first["document_version_id"], "first")
    client.post(
        f"/api/v1/document-versions/{first['document_version_id']}:publish",
        headers={"Idempotency-Key": "publish-first"},
    )
    document = client.get(f"/api/v1/documents/{first['document_id']}")
    new_version_path = f"/api/v1/documents/{first['document_id']}/versions/upload-sessions"
    second = _create_and_complete(
        client,
        new_version_path,
        b"second version",
        key="second",
        create_headers={"If-Match": document.headers["etag"]},
    )
    versions = components.repository.get_versions(first["document_id"])
    assert [int(item["version_no"]) for item in versions] == [1, 2]

    blocked = client.post(
        f"/api/v1/document-versions/{second['document_version_id']}:publish",
        headers={"Idempotency-Key": "publish-second-early"},
    )
    assert blocked.status_code == 409
    assert (
        components.repository.get_document(first["document_id"])["current_version_id"]
        == first["document_version_id"]
    )

    _run_worker(components)
    _approve(client, second["document_version_id"], "second")
    published = client.post(
        f"/api/v1/document-versions/{second['document_version_id']}:publish",
        headers={"Idempotency-Key": "publish-second"},
    )
    rolled_back = client.post(
        f"/api/v1/documents/{first['document_id']}:rollback",
        headers={"Idempotency-Key": "rollback-first"},
        json={"version_id": first["document_version_id"]},
    )
    states = {
        str(item["id"]): str(item["publication_state"])
        for item in components.repository.get_versions(first["document_id"])
    }

    assert published.status_code == rolled_back.status_code == 200
    assert states == {
        first["document_version_id"]: "SERVING",
        second["document_version_id"]: "SUPERSEDED",
    }
    assert (
        components.repository.get_document(first["document_id"])["current_version_id"]
        == first["document_version_id"]
    )


def test_new_version_session_enforces_rbac_if_match_and_idempotency(tmp_path: Path) -> None:
    components = _components(tmp_path)
    admin = TestClient(create_app(components))
    first = _create_and_complete(
        admin,
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        b"base",
        key="base",
    )
    document = admin.get(f"/api/v1/documents/{first['document_id']}")
    path = f"/api/v1/documents/{first['document_id']}/versions/upload-sessions"
    body = {
        "filename": "next.txt",
        "expected_size": 4,
        "expected_sha256": hashlib.sha256(b"next").hexdigest(),
        "declared_mime": "text/plain",
    }
    headers = {"If-Match": document.headers["etag"], "Idempotency-Key": "new-version"}
    first_response = admin.post(path, headers=headers, json=body)
    replay = admin.post(path, headers=headers, json=body)
    conflict = admin.post(path, headers=headers, json={**body, "filename": "other.txt"})
    stale = admin.post(
        path,
        headers={"If-Match": '"999"', "Idempotency-Key": "stale"},
        json=body,
    )
    reader = TestClient(
        create_app(
            replace(
                components,
                authenticator=_PrincipalAuthenticator(("reader",), components.tenant_id),
            )
        )
    )
    forbidden = reader.post(path, headers=headers, json=body)

    assert first_response.status_code == replay.status_code == 200
    assert replay.json() == first_response.json()
    assert conflict.status_code == 409
    assert stale.status_code == 412
    assert forbidden.status_code == 403


def test_projection_swap_failure_rolls_back_and_keeps_old_version_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    components = _components(tmp_path)
    client = TestClient(create_app(components))
    first = _create_and_complete(
        client,
        f"/api/v1/spaces/{components.space_id}/upload-sessions",
        b"stable old version",
        key="stable-first",
    )
    _run_worker(components)
    _approve(client, first["document_version_id"], "stable-first")
    client.post(
        f"/api/v1/document-versions/{first['document_version_id']}:publish",
        headers={"Idempotency-Key": "stable-publish-first"},
    )
    document = client.get(f"/api/v1/documents/{first['document_id']}")
    second = _create_and_complete(
        client,
        f"/api/v1/documents/{first['document_id']}/versions/upload-sessions",
        b"candidate version",
        key="candidate-second",
        create_headers={"If-Match": document.headers["etag"]},
    )
    _run_worker(components)
    _approve(client, second["document_version_id"], "candidate-second")
    components.lifecycle_store.reload()
    original_sync = SQLiteLifecycleStore._sync_local_fact_source

    def failed_swap(*args, **kwargs):
        original_sync(*args, **kwargs)
        raise RuntimeError("injected projection swap failure")

    monkeypatch.setattr(
        SQLiteLifecycleStore,
        "_sync_local_fact_source",
        staticmethod(failed_swap),
    )
    with pytest.raises(RuntimeError, match="projection swap"):
        components.lifecycle_service.publish(
            first["document_id"],
            second["document_version_id"],
            event_id="failed-swap",
            trace_id="failure",
        )

    assert (
        components.repository.get_document(first["document_id"])["current_version_id"]
        == first["document_version_id"]
    )
    with components.database.connect() as connection:
        states = {
            str(row["version_id"]): str(row["projection_state"])
            for row in connection.execute(
                "SELECT version_id, projection_state FROM publication_candidates"
            ).fetchall()
        }
    assert states[first["document_version_id"]] == "ACTIVE"
    assert states[second["document_version_id"]] == "STAGED"
    assert (
        components.lifecycle_store.documents[first["document_id"]].active_version_id
        == first["document_version_id"]
    )
