from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from mysql_sql_harness import SQLControl
from ragkb.adapters.mysql_upload import _empty_state
from ragkb.api.app import create_app
from ragkb.domain.pagination import RepositoryPage
from ragkb.domain.state_machines import UploadSessionState
from test_document_authorization import published as _published
from test_document_authorization import reader_for
from test_production_persistence_behaviors import upload
from test_search_backed_qa import _components_with_search_qa

published = _published


def test_new_version_loads_one_version_despite_unrelated_and_old_history(tmp_path, monkeypatch):
    control = SQLControl(tmp_path / "state.sqlite3")
    repository, document_id, version_id, _ = upload(control, tmp_path)
    # Include hundreds of unrelated rows and older versions in the same document.
    state = _empty_state()
    first = repository.get_version(version_id)
    for index in range(400):
        identity = f"history-{index}"
        state["versions"][identity] = {
            **first,
            "id": identity,
            "document_id": document_id if index < 200 else "unrelated",
            "version_no": index + 2,
        }
    connection = control.connect()
    repository._entities.sync(connection.cursor(), {}, repository._to_entities(state))
    connection.commit()
    connection.close()
    reads = []
    load = repository._entities.load

    def track(cursor, **filters):
        rows = load(cursor, **filters)
        reads.append((filters, len(rows)))
        return rows

    monkeypatch.setattr(repository._entities, "load", track)
    session = repository.create_upload_session(
        tenant_id="tenant",
        space_id="kb",
        filename="v2.txt",
        expected_size=1,
        expected_sha256="b" * 64,
        declared_mime="text/plain",
        idempotency_key="v2",
        request_hash="v2",
        target_document_id=document_id,
        target_document_row_version=1,
    )
    for state_value, fields in (
        (UploadSessionState.UPLOADED, {}),
        (UploadSessionState.VALIDATED, {"detected_mime": "text/plain", "detected_format": "txt"}),
        (UploadSessionState.PROMOTED, {"original_key": "original/v2"}),
    ):
        session = repository.update_session(session.id, session.row_version, state_value, **fields)
    _, new_version = repository.ensure_document_version(session)

    assert repository.get_version(new_version)["version_no"] == 202
    assert all(count <= 1 for _, count in reads)
    assert all(filters.get("entity_id") or filters.get("parent_id") for filters, _ in reads)
    assert repository.get_version("history-399")["document_id"] == "unrelated"


def test_mysql_document_page_loads_only_latest_version_and_matching_session(tmp_path, monkeypatch):
    control = SQLControl(tmp_path / "state.sqlite3")
    repository, document_id, version_id, _ = upload(control, tmp_path)
    connection = control.connect()
    connection.cursor().execute(
        "CREATE TABLE retrieval_chunk_projections (document_version_id TEXT, "
        "tenant_id TEXT, index_generation_id TEXT, locator_json TEXT)"
    )
    connection.commit()
    connection.close()
    state = _empty_state()
    first = repository.get_version(version_id)
    for index in range(300):
        identity = f"v-{index}"
        state["versions"][identity] = {**first, "id": identity, "version_no": index + 2}
    connection = control.connect()
    repository._entities.sync(connection.cursor(), {}, repository._to_entities(state))
    connection.commit()
    connection.close()
    reads = []
    load = repository._entities.load

    def track(cursor, **filters):
        rows = load(cursor, **filters)
        reads.append((filters, len(rows)))
        return rows

    monkeypatch.setattr(repository._entities, "load", track)
    page = repository.list_documents_page("kb", limit=1)

    assert page.items[0]["version_id"] == "v-299"
    assert page.next_key is None
    assert all(count <= 1 for _, count in reads)
    assert any(filters.get("document_version_ids") == ["v-299"] for filters, _ in reads)
    assert repository.get_document(document_id)["row_version"] == 1

    state = _empty_state()
    template = repository.get_document(document_id)
    for suffix in ("b", "c"):
        identity = f"document-{suffix}"
        state["documents"][identity] = {**template, "id": identity}
        state["versions"][f"version-{suffix}"] = {
            **first,
            "id": f"version-{suffix}",
            "document_id": identity,
        }
    connection = control.connect()
    repository._entities.sync(connection.cursor(), {}, repository._to_entities(state))
    connection.commit()
    first_page = repository.list_documents_page("kb", limit=1)
    assert first_page.next_key == (0, document_id)
    connection.cursor().execute(
        "DELETE FROM upload_entities WHERE tenant_id=%s "
        "AND entity_type='documents' AND entity_id=%s",
        ("tenant", document_id),
    )
    connection.commit()
    connection.close()
    next_page = repository.list_documents_page("kb", limit=1, after=first_page.next_key)
    assert [item["document_id"] for item in next_page.items] == ["document-b"]


def test_mysql_chunk_cursor_handles_equal_ordinals_without_offset_skips(tmp_path):
    control = SQLControl(tmp_path / "state.sqlite3")
    repository, _, version, _ = upload(control, tmp_path)
    connection = control.connect()
    cursor = connection.cursor()
    cursor.execute(
        "CREATE TABLE retrieval_chunk_projections (chunk_id TEXT, parent_chunk_id TEXT, "
        "display_text TEXT, locator_json TEXT, lifecycle_projection TEXT, current_version INTEGER, "
        "document_version_id TEXT, tenant_id TEXT, index_generation_id TEXT)"
    )
    for chunk_id in ("a", "b", "c"):
        cursor.execute(
            "INSERT INTO retrieval_chunk_projections "
            "VALUES (%s, NULL, %s, %s, 'STAGED', 0, %s, %s, %s)",
            (chunk_id, chunk_id, '{"ordinal":0,"page":1}', version, "tenant", "g1"),
        )
    connection.commit()
    first = repository.list_chunks_page(version, limit=1, preview=True)
    assert first.next_key == (0, "a")
    cursor.execute("DELETE FROM retrieval_chunk_projections WHERE chunk_id='a'")
    connection.commit()
    connection.close()
    second = repository.list_chunks_page(version, limit=1, preview=True, after=first.next_key)
    assert [item["chunk_id"] for item in second.items] == ["b"]
    assert second.next_key == (0, "b")


def test_chunk_cursor_survives_deletion_before_current_position(published):
    runtime, admin, _, version = published
    path = f"/api/document-versions/{version}/chunks/preview"
    first = admin.get(path, params={"limit": 1})
    cursor = first.headers["X-Next-Cursor"]
    assert cursor
    first_id = first.json()[0]["chunk_id"]
    with runtime.database.transaction(immediate=True) as connection:
        # Remove a row before the cursor without renumbering the immutable chunk positions.
        connection.execute("DELETE FROM chunks WHERE id=?", (first_id,))
    second = admin.get(path, params={"limit": 1, "cursor": cursor})

    assert second.status_code == 200
    assert len(second.json()) == 1
    assert second.json()[0]["chunk_id"] != first_id
    assert second.headers["X-Next-Cursor"] == ""
    assert admin.get(path, params={"cursor": cursor, "offset": 1}).status_code == 400
    assert admin.get(path, params={"cursor": "bad"}).status_code == 400
    other_space = admin.post("/api/spaces", json={"name": "other"}).json()["id"]
    assert (
        admin.get(f"/api/spaces/{other_space}/documents", params={"cursor": cursor}).status_code
        == 400
    )


def test_empty_authorized_page_still_advances_cursor(published, monkeypatch):
    runtime, _, _, version = published
    repository = runtime.repository
    actual = repository.list_chunks_page

    def filtered(*args, **kwargs):
        page = actual(*args, **kwargs)
        return RepositoryPage([] if kwargs.get("after") is None else page.items, page.next_key)

    monkeypatch.setattr(repository, "list_chunks_page", filtered)
    reader = reader_for(runtime)
    path = f"/api/document-versions/{version}/chunks"
    first = reader.get(path, params={"limit": 1})
    assert first.json() == []
    assert first.headers["X-Next-Cursor"]
    second = reader.get(path, params={"limit": 1, "cursor": first.headers["X-Next-Cursor"]})
    assert second.json()
    wrong_scope = reader.get(path + "/preview", params={"cursor": first.headers["X-Next-Cursor"]})
    assert wrong_scope.status_code in (400, 403)


@pytest.mark.parametrize("path", ["documents", "search", "ask", "ask:stream"])
def test_synchronous_preflight_leaves_event_loop_responsive(tmp_path, monkeypatch, path):
    runtime = _components_with_search_qa(tmp_path)
    app = create_app(runtime)
    release = threading.Event()
    original = runtime.repository.get_space

    @app.get("/event-loop-probe")
    async def probe():
        return {"ok": True}

    async def exercise():
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def blocked(space_id):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(2):
                raise RuntimeError("sync I/O ran on event loop or probe never completed")
            return original(space_id)

        monkeypatch.setattr(runtime.repository, "get_space", blocked)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            if path == "documents":
                request = client.get(f"/api/spaces/{runtime.space_id}/documents")
            else:
                request = client.post(
                    f"/api/{path}", json={"query" if path == "search" else "question": "保修期"}
                )
            pending = asyncio.create_task(request)
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                assert not pending.done()
                response = await asyncio.wait_for(client.get("/event-loop-probe"), timeout=1)
                assert response.json() == {"ok": True}
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200

    asyncio.run(exercise())
