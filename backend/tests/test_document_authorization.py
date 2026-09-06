from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from ragkb.adapters.sqlite_retrieval import SQLiteRetrievalControlPlane
from ragkb.api.app import create_app
from ragkb.api.support import document_search_context
from ragkb.domain.auth import RequestPrincipal
from test_lifecycle_fact_source import _components, _PrincipalAuthenticator, _process_next, _upload


@pytest.fixture
def published(tmp_path, monkeypatch):
    for key, value in {
        "APP_ENV": "testing",
        "RAG_RUNTIME_PROFILE": "local",
        "VECTOR_BACKEND": "local",
        "AUTH_MODE": "local_single_user",
        "REAL_PROVIDER_CALLS_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    runtime = _components(tmp_path)
    admin = TestClient(create_app(runtime))
    document, version, _ = _upload(admin, runtime.space_id)
    _process_next(runtime, admin, version)
    assert (
        admin.post(
            f"/api/document-versions/{version}:publish",
            headers={"Idempotency-Key": "publish"},
        ).status_code
        == 200
    )
    return runtime, admin, document, version


def reader_for(runtime, *, scopes=("group:legal",), clearance=2, roles=("reader",), tenant=None):
    principal = RequestPrincipal(
        tenant or runtime.tenant_id, "read-user", roles, scopes, "test", clearance
    )
    return TestClient(
        create_app(replace(runtime, authenticator=_PrincipalAuthenticator(principal)))
    )


def paths(runtime, document, version):
    return (
        f"/api/documents/{document}",
        f"/api/documents/{document}/versions",
        f"/api/document-versions/{version}/chunks",
    )


def projections(runtime, version):
    with runtime.database.connect() as connection:
        return [
            SQLiteRetrievalControlPlane._chunk(dict(row))
            for row in connection.execute(
                "SELECT * FROM retrieval_projections WHERE document_version_id=?", (version,)
            ).fetchall()
        ]


@pytest.mark.parametrize(
    "change,scopes,clearance",
    [
        ({"visibility": "RESTRICTED", "acl_scope_tokens": ("group:finance",)}, ("group:legal",), 3),
        ({"classification_level": 3}, ("group:legal",), 2),
        ({"valid_from_epoch": int(time.time()) + 3600}, ("group:legal",), 3),
        ({"valid_to_epoch": int(time.time()) - 1}, ("group:legal",), 3),
        ({"lifecycle_projection": "STAGED"}, ("group:legal",), 3),
        ({"current_version": False}, ("group:legal",), 3),
        ({"permission_revision": 0}, ("group:legal",), 3),
    ],
)
def test_reader_and_search_share_resource_policy(published, change, scopes, clearance):
    runtime, _, document, version = published
    SQLiteRetrievalControlPlane(runtime.database).upsert_chunks(
        [replace(chunk, **change) for chunk in projections(runtime, version)]
    )
    reader = reader_for(runtime, scopes=scopes, clearance=clearance)
    for path in paths(runtime, document, version):
        assert reader.get(path).status_code == 404, path
    assert reader.get(f"/api/spaces/{runtime.space_id}/documents").json() == []
    response = reader.post("/api/search", json={"query": "fact source"})
    assert response.status_code == 200
    assert response.json()["hits"] == []


def test_matching_acl_and_clearance_allow_current_document(published):
    runtime, _, document, version = published
    SQLiteRetrievalControlPlane(runtime.database).upsert_chunks(
        [
            replace(
                chunk,
                visibility="RESTRICTED",
                acl_scope_tokens=("group:legal",),
                classification_level=2,
            )
            for chunk in projections(runtime, version)
        ]
    )
    reader = reader_for(runtime)
    for path in paths(runtime, document, version):
        assert reader.get(path).status_code == 200, path
    chunks = reader.get(paths(runtime, document, version)[2]).json()
    assert chunks and all(row["status"] == "SERVING" for row in chunks)
    assert reader.post("/api/search", json={"query": "fact source"}).json()["hits"]


@pytest.mark.parametrize(
    "roles,scopes,expected",
    [
        (("reader",), (), 403),
        (("reader",), ("space:{space}:manage",), 403),
        (("knowledge_maintainer",), (), 403),
        (("knowledge_maintainer",), ("space:other:manage",), 403),
        (("knowledge_maintainer",), ("space:{space}:manage",), 200),
        (("admin",), (), 200),
    ],
)
def test_preview_requires_explicit_management_scope(published, roles, scopes, expected):
    runtime, _, document, version = published
    # Hide all content from regular reading, even for privileged roles.
    SQLiteRetrievalControlPlane(runtime.database).upsert_chunks(
        [replace(chunk, classification_level=3) for chunk in projections(runtime, version)]
    )
    caller = reader_for(
        runtime,
        scopes=tuple(s.format(space=runtime.space_id) for s in scopes),
        clearance=0,
        roles=roles,
    )
    for path in paths(runtime, document, version):
        assert caller.get(path).status_code == 404
        assert caller.get(path + "?preview=true").status_code == 404
        assert caller.get(path + "/preview").status_code == expected
    assert caller.get(f"/api/spaces/{runtime.space_id}/documents/preview").status_code == expected


def test_cross_tenant_ids_are_hidden_in_reads_and_preview(published):
    runtime, _, document, version = published
    caller = reader_for(runtime, roles=("admin",), tenant="another-tenant", clearance=3)
    for path in paths(runtime, document, version):
        assert caller.get(path).status_code == 404
        assert caller.get(path + "/preview").status_code == 404


def test_deleted_document_is_hidden_even_from_management_preview(published):
    runtime, admin, document, version = published
    assert (
        admin.delete(
            f"/api/documents/{document}", headers={"Idempotency-Key": "delete"}
        ).status_code
        == 200
    )
    for path in paths(runtime, document, version):
        assert admin.get(path).status_code == 404
        assert admin.get(path + "/preview").status_code == 404


def test_draft_and_retired_versions_only_available_in_preview(published):
    runtime, admin, document, first = published
    etag = admin.get(f"/api/documents/{document}/preview").headers["etag"]
    _, second, _ = _upload(
        admin, runtime.space_id, key="next", document_id=document, document_etag=etag
    )
    _process_next(runtime, admin, second)
    reader = reader_for(runtime)
    for caller in (reader, admin):
        assert [v["id"] for v in caller.get(paths(runtime, document, first)[1]).json()] == [first]
        assert caller.get(paths(runtime, document, second)[2]).status_code == 404
    assert admin.get(paths(runtime, document, second)[2] + "/preview").json()
    assert (
        admin.post(
            f"/api/document-versions/{second}:publish",
            headers={"Idempotency-Key": "publish-next"},
        ).status_code
        == 200
    )
    # Simulate retained stale projections that still claim to be current and serving.
    SQLiteRetrievalControlPlane(runtime.database).upsert_chunks(
        [
            replace(chunk, current_version=True, lifecycle_projection="SERVING")
            for chunk in projections(runtime, first)
        ]
    )
    for caller in (reader, admin):
        assert [v["id"] for v in caller.get(paths(runtime, document, second)[1]).json()] == [second]
        assert caller.get(paths(runtime, document, first)[2]).status_code == 404
    assert len(admin.get(paths(runtime, document, second)[1] + "/preview").json()) == 2
    assert admin.get(paths(runtime, document, first)[2] + "/preview").json()


def test_every_chunk_rechecks_acl_revision_not_just_one_chunk(published):
    runtime, _, document, version = published
    control = SQLiteRetrievalControlPlane(runtime.database)
    original = projections(runtime, version)[0]
    stale = replace(
        original,
        chunk_id="stale-chunk",
        permission_revision=0,
        display_text="old confidential payload",
    )
    control.upsert_chunks((stale,))
    with runtime.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO chunks SELECT 'stale-chunk', tenant_id, version_id, section_id, "
            "parent_chunk_id, ordinal + 1000, original_text, display_text, retrieval_text, "
            "locator_json, content_sha256, token_count, kind, chunking_revision, tokenizer_id, "
            "status FROM chunks WHERE id=?",
            (original.chunk_id,),
        )
    response = reader_for(runtime).get(paths(runtime, document, version)[2])
    assert response.status_code == 200
    assert response.json()
    assert "stale-chunk" not in {row["chunk_id"] for row in response.json()}


def test_document_authorization_checks_beyond_first_page(published, monkeypatch):
    runtime, _, document, version = published
    real_ids = runtime.repository.list_chunk_ids(version)
    monkeypatch.setattr(
        runtime.repository,
        "list_chunk_ids",
        lambda version_id, limit=100, offset=0: (
            [f"missing-{n}" for n in range(100)]
            if offset == 0
            else real_ids
            if offset == 100
            else []
        ),
    )
    assert reader_for(runtime).get(paths(runtime, document, version)[0]).status_code == 200


def test_revocation_during_chunk_loading_fails_closed(published, monkeypatch):
    runtime, admin, document, version = published
    original = runtime.repository.list_chunks_page

    def revoke_after_read(*args, **kwargs):
        rows = original(*args, **kwargs)
        assert (
            admin.post(
                f"/api/documents/{document}:revoke", headers={"Idempotency-Key": "revoke"}
            ).status_code
            == 200
        )
        return rows

    monkeypatch.setattr(runtime.repository, "list_chunks_page", revoke_after_read)
    response = reader_for(runtime).get(paths(runtime, document, version)[2])
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response.json() == []


def test_repository_requires_context_or_explicit_internal_preview(published):
    runtime, _, _, version = published
    with pytest.raises(ValueError, match="CHUNK_READ_CONTEXT_REQUIRED"):
        runtime.repository.list_chunks(version)
    assert runtime.repository.list_chunks(version, preview=True)
    principal = RequestPrincipal(runtime.tenant_id, "reader", ("reader",), (), "test", 0)
    context = document_search_context(runtime, principal, runtime.space_id)
    assert runtime.repository.list_chunks(version, context=context)
    assert (
        runtime.repository.list_chunks(version, context=replace(context, tenant_id="other")) == []
    )
