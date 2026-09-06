"""SQL harness assertions measure query/connection counts, not production latency."""

import json
from dataclasses import asdict, replace

import pytest
from mysql_sql_harness import SQLControl
from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane
from ragkb.adapters.mysql_upload import _empty_state
from ragkb.adapters.retrieval_memory import InMemoryRetrievalControlPlane
from ragkb.adapters.sqlite_retrieval import SQLiteRetrievalControlPlane
from ragkb.api.support import ensure_document_readable
from ragkb.domain.auth import RequestPrincipal
from ragkb.domain.retrieval import SearchContext
from ragkb.domain.uploads import ResourceNotFoundError
from ragkb.infrastructure.sqlite import SQLiteDatabase
from test_document_authorization import projections
from test_document_authorization import published as _published
from test_mysql_retrieval import _chunk
from test_production_persistence_behaviors import upload

published = _published


@pytest.mark.parametrize("current_only", [False, True])
def test_page_relations_use_constant_queries_and_one_connection(
    tmp_path, monkeypatch, current_only
):
    control = SQLControl(tmp_path / "state.sqlite3")
    repository, doc_id, version_id, session = upload(control, tmp_path)
    doc = repository.get_document(doc_id)
    version = repository.get_version(version_id)
    state = _empty_state()
    # Each document has history, a published current version, and a newer draft.
    for index in range(25):
        identity = f"a-{index:02}"
        state["documents"][identity] = {
            **doc,
            "id": identity,
            "current_version_id": f"{identity}-v1",
        }
        for no in range(1, 6):
            vid = f"{identity}-v{no}"
            state["versions"][vid] = {
                **version,
                "id": vid,
                "document_id": identity,
                "version_no": no,
            }
            for suffix in ("a", "z"):
                sid = f"{vid}-{suffix}"
                state["sessions"][sid] = {
                    **asdict(session),
                    "id": sid,
                    "document_id": identity,
                    "document_version_id": vid,
                    "filename": f"{vid}-{suffix}.txt",
                    "job_id": f"job-{vid}-{suffix}",
                }
    connection = control.connect()
    cursor = connection.cursor()
    repository._entities.sync(cursor, {}, repository._to_entities(state))
    cursor.execute(
        "CREATE TABLE retrieval_chunk_projections (document_version_id TEXT, "
        "tenant_id TEXT, index_generation_id TEXT, locator_json TEXT)"
    )
    for vid in state["versions"]:
        for tenant, generation, locator in (
            ("tenant", "g1", "{}"),
            ("tenant", "g1", '{"is_parent":true}'),
            ("other", "g1", "{}"),
            ("tenant", "old", "{}"),
        ):
            cursor.execute(
                "INSERT INTO retrieval_chunk_projections VALUES (%s,%s,%s,%s)",
                (vid, tenant, generation, locator),
            )
    connection.commit()
    connection.close()
    connections = []
    connect = control.connect

    def track_connect():
        connections.append(True)
        return connect()

    monkeypatch.setattr(control, "connect", track_connect)
    reads = []
    load = repository._entities.load

    def track_load(cursor, **filters):
        rows = load(cursor, **filters)
        reads.append((filters, len(rows)))
        return rows

    monkeypatch.setattr(repository._entities, "load", track_load)
    counts = []
    for page_size in (1, 20):
        control.statements.clear()
        connections.clear()
        reads.clear()
        page = repository.list_documents_page(
            "kb", limit=page_size, current_only=current_only, after=(0, "a-")
        )
        assert len(page.items) == page_size
        assert len(connections) == 1
        counts.append(len(control.statements))
        assert all(count <= page_size for _, count in reads)
        for item in page.items:
            expected = f"{item['document_id']}-v{1 if current_only else 5}"
            assert item["version_id"] == expected
            assert item["filename"] == f"{expected}-z.txt"
            assert item["job_id"] == f"job-{expected}-z"
            assert item["chunk_count"] == 1
        assert page.next_key == (0, page.items[-1]["document_id"])
    assert counts == [6, 6]


@pytest.fixture(params=["mysql", "sqlite", "memory"])
def control_plane(request, tmp_path):
    if request.param == "sqlite":
        adapter = SQLiteRetrievalControlPlane(SQLiteDatabase(tmp_path / "local.sqlite3"))
    elif request.param == "memory":
        adapter = InMemoryRetrievalControlPlane()
    else:
        control = SQLControl(tmp_path / "mysql.sqlite3")
        connection = control.connect()
        connection.cursor().execute("""CREATE TABLE retrieval_chunk_projections (
            chunk_id TEXT, tenant_id TEXT, space_id TEXT, document_id TEXT,
            document_version_id TEXT,
            parent_chunk_id TEXT, display_text TEXT, retrieval_text TEXT, locator_json TEXT,
            content_checksum TEXT, visibility TEXT, acl_scope_tokens_json TEXT,
            classification_level INTEGER,
            lifecycle_projection TEXT, valid_from_epoch INTEGER, valid_to_epoch INTEGER,
            permission_revision INTEGER, current_version INTEGER, index_generation_id TEXT,
            updated_at TEXT,
            PRIMARY KEY(chunk_id, index_generation_id))""")
        connection.close()
        connect = control.connect

        def with_json_overlap():
            connection = connect()
            connection.connection.create_function(
                "JSON_OVERLAPS", 2, lambda a, b: bool(set(json.loads(a)) & set(json.loads(b)))
            )
            return connection

        control.connect = with_json_overlap
        adapter = MySQLRetrievalControlPlane(control, "generation-1")
    return adapter


def seed(adapter, chunks):
    if isinstance(adapter, InMemoryRetrievalControlPlane):
        adapter._chunks = {chunk.chunk_id: chunk for chunk in chunks}
    else:
        adapter.upsert_chunks(chunks)


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": "other"},
        {"space_id": "other"},
        {"document_id": "other"},
        {"document_version_id": "old"},
        {"classification_level": 3},
        {"acl_scope_tokens": ("group:secret",)},
        {"permission_revision": 1},
        {"permission_revision": 3},
        {"valid_from_epoch": 101},
        {"valid_to_epoch": 100},
        {"lifecycle_projection": "STAGED"},
        {"current_version": False},
    ],
)
def test_readable_document_query_retains_each_chunk_policy(control_plane, change):
    context = SearchContext(
        "tenant-1", ("space-1",), ("group:reader",), 2, 100, "generation-1", 2, 2
    )
    chunk = _chunk()
    seed(control_plane, [replace(chunk, **change)])

    def readable():
        return control_plane.has_readable_chunks(
            "document-1", "version-1", context, permission_revision=2
        )

    assert not readable()
    # A denied first chunk must not hide a later authorized chunk in the same version.
    seed(control_plane, [replace(chunk, **change), replace(chunk, chunk_id="later-readable")])
    assert readable()


def test_readability_keeps_local_single_generation_and_mysql_isolation(control_plane):
    seed(control_plane, [replace(_chunk(), index_generation_id="old")])
    context = SearchContext(
        "tenant-1", ("space-1",), ("group:reader",), 2, 100, "generation-1", 2, 2
    )
    readable = control_plane.has_readable_chunks(
        "document-1", "version-1", context, permission_revision=2
    )
    assert readable is (not isinstance(control_plane, MySQLRetrievalControlPlane))


def test_denied_large_document_never_scans_chunk_ids_and_rechecks_lifecycle(published, monkeypatch):
    runtime, _, document_id, version_id = published
    adapter = runtime.search_service.control_plane.control_plane
    first = projections(runtime, version_id)[0]
    seed(
        adapter,
        [
            replace(
                first, chunk_id=f"denied-{i}", visibility="RESTRICTED", acl_scope_tokens=("secret",)
            )
            for i in range(1200)
        ],
    )
    # Replace all original projections too.
    seed(
        adapter,
        [
            replace(chunk, visibility="RESTRICTED", acl_scope_tokens=("secret",))
            for chunk in projections(runtime, version_id)
        ],
    )
    principal = RequestPrincipal(runtime.tenant_id, "reader", ("reader",), (), "test", 3)

    def unexpected(*args, **kwargs):
        pytest.fail("document authorization must not read chunk IDs or bodies")

    monkeypatch.setattr(runtime.repository, "list_chunk_ids", unexpected)
    monkeypatch.setattr(adapter, "authorize_chunks", unexpected)
    reloads = []
    reload = runtime.lifecycle_store.reload

    def track_reload():
        reloads.append(True)
        reload()

    monkeypatch.setattr(runtime.lifecycle_store, "reload", track_reload)
    with pytest.raises(ResourceNotFoundError):
        ensure_document_readable(runtime, document_id, principal, version_id)
    assert (
        len(reloads) == 2
    )  # Initial snapshot, then authoritative recheck after SQL; no duplicate preflight.
    seed(adapter, [replace(first, chunk_id="allowed-last", visibility="TENANT")])
    ensure_document_readable(runtime, document_id, principal, version_id)
    real_exists = adapter.has_readable_chunks

    def revoked_during_query(*args, **kwargs):
        allowed = real_exists(*args, **kwargs)
        runtime.lifecycle_store.documents[document_id].visible = False
        runtime.lifecycle_store.persist_state()
        return allowed

    monkeypatch.setattr(adapter, "has_readable_chunks", revoked_during_query)
    with pytest.raises(ResourceNotFoundError):
        ensure_document_readable(runtime, document_id, principal, version_id)
