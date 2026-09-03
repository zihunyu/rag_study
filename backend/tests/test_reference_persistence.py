from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    LifecycleAwareFinalPermission,
    SyntheticEvidenceProvider,
)
from ragkb.api.app import create_app
from ragkb.application.qa import TrustedQAService
from ragkb.domain.lifecycle import LifecycleState
from ragkb.domain.rag import Evidence
from ragkb.engineering_security.references import HMACReferenceSigner, ReferenceTokenError
from ragkb.infrastructure.reference_repository import SQLiteReferenceStore
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.runtime_components import build_runtime_components


def _components(tmp_path: Path):
    return build_runtime_components(
        storage_root=tmp_path / "storage",
        database_path=tmp_path / "control.sqlite3",
        app_secret_override=SecretStr("injected-local-reference-secret-32bytes"),
    )


def _answered_components(tmp_path: Path):
    components = _components(tmp_path)
    if "document" not in components.lifecycle_store.documents:
        components.lifecycle_service.register_document("document", "version", trace_id="setup")
        record = components.lifecycle_store.documents["document"]
        record.lifecycle_state = LifecycleState.ACTIVE
        record.visible = True
        components.lifecycle_store.persist_state(tenant_id=components.tenant_id)
    evidence = Evidence(
        evidence_id="E1",
        chunk_id="chunk",
        document_id="document",
        document_version_id="version",
        text="persistent evidence",
        locator={"page": 1},
        valid_from_epoch=0,
        valid_to_epoch=0,
        authority_rank=1,
        permission_revision=1,
        authorized=True,
        current_version=True,
    )
    service = TrustedQAService(
        SyntheticEvidenceProvider((evidence,)),
        DeterministicBufferedGenerator(answer="answer"),
        LifecycleAwareFinalPermission(components.lifecycle_store, components.tenant_id),
        components.reference_signer,
        components.rag_repository,
    )
    return replace(components, qa_service=service)


def test_reference_mapping_and_local_random_secret_survive_restart(tmp_path: Path) -> None:
    first = _answered_components(tmp_path)
    answer = TestClient(create_app(first)).post("/api/v1/ask", json={"question": "q"}).json()
    source_url = answer["citations"][0]["source_url"]

    restarted = _components(tmp_path)
    response = TestClient(create_app(restarted)).get(source_url)

    assert response.status_code == 200
    assert response.json()["text"] == "persistent evidence"


def test_reference_is_bound_to_tenant_user_and_revocation(tmp_path: Path) -> None:
    components = _answered_components(tmp_path)
    answer = TestClient(create_app(components)).post("/api/v1/ask", json={"question": "q"}).json()
    parts = answer["citations"][0]["source_url"].split("/")

    with pytest.raises(ReferenceTokenError):
        components.reference_signer.resolve(parts[4], parts[6], "other-tenant", "local-admin")
    with pytest.raises(ReferenceTokenError):
        components.reference_signer.resolve(parts[4], parts[6], components.tenant_id, "other-user")

    components.reference_signer.revoke_document("document")
    with pytest.raises(ReferenceTokenError):
        components.reference_signer.resolve(parts[4], parts[6], components.tenant_id, "local-admin")


def test_reference_expiry_uses_persisted_record(tmp_path: Path) -> None:
    now = [100.0]
    database = SQLiteDatabase(tmp_path / "reference.sqlite3")
    store = SQLiteReferenceStore(database)
    signer = HMACReferenceSigner(
        SecretStr("test-reference-signing-key-32bytes"),
        store,
        ttl_seconds=1,
        clock=lambda: now[0],
    )
    url = signer.source_url("run", "E1", "tenant", "user", "document")
    parts = url.split("/")
    now[0] = 102.0

    with pytest.raises(ReferenceTokenError, match="EXPIRED"):
        signer.resolve(parts[4], parts[6], "tenant", "user")


def test_reference_keyring_rotates_without_invalidating_retiring_tokens(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "references.sqlite3")
    store = SQLiteReferenceStore(database)
    old = HMACReferenceSigner(
        {"v1": SecretStr("old-reference-secret-long")}, store, active_kid="v1"
    )
    old_url = old.source_url("run-old", "E1", "tenant", "user", "document")
    old_parts = old_url.split("/")
    rotated = HMACReferenceSigner(
        {
            "v1": SecretStr("old-reference-secret-long"),
            "v2": SecretStr("new-reference-secret-long"),
        },
        store,
        active_kid="v2",
    )

    assert rotated.resolve(old_parts[4], old_parts[6], "tenant", "user") == (
        "run-old",
        "E1",
    )
    new_url = rotated.source_url("run-new", "E1", "tenant", "user", "document")
    assert new_url.split("/")[4].startswith("v2.")


def test_tombstone_revokes_preview_and_full_ask_context(tmp_path: Path) -> None:
    components = _answered_components(tmp_path)
    client = TestClient(create_app(components))
    answer = client.post("/api/v1/ask", json={"question": "q"}).json()
    source_url = answer["citations"][0]["source_url"]
    components.lifecycle_service.delete("document", event_id="delete", trace_id="trace")
    components.reference_signer.revoke_document("document")

    blocked_answer = client.post("/api/v1/ask", json={"question": "q"})
    blocked_source = client.get(source_url)

    assert blocked_answer.json()["status"] == "system_error"
    assert blocked_answer.json()["answer"] is None
    assert blocked_source.status_code == 404
