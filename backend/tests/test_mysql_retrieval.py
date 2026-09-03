from __future__ import annotations

from pathlib import Path

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane
from ragkb.config import load_env
from ragkb.domain.retrieval import AuthorizedChunk, RetrievalRelease, SearchContext


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rows = []
        self.description = []

    def execute(self, statement, parameters=()):
        self.connection.statements.append((statement, parameters))
        if "FROM retrieval_chunk_projections" in statement and statement.lstrip().startswith(
            "SELECT *"
        ):
            self.rows = list(self.connection.projection_rows)
        elif "FROM retrieval_release_state" in statement and statement.lstrip().startswith(
            "SELECT tenant_id"
        ):
            self.rows = [self.connection.release_row]
        elif statement.lstrip().startswith("SELECT chunk_id"):
            self.rows = list(self.connection.projection_rows[:1])
        else:
            self.rows = []

    def executemany(self, statement, parameters):
        self.connection.executemany_calls.append((statement, list(parameters)))

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, projection_rows, release_row) -> None:
        self.projection_rows = projection_rows
        self.release_row = release_row
        self.statements = []
        self.executemany_calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def _chunk() -> AuthorizedChunk:
    return AuthorizedChunk(
        "chunk-1",
        "tenant-1",
        "space-1",
        "document-1",
        "version-1",
        None,
        "display",
        "retrieval",
        {"page": 1},
        "a" * 64,
        "RESTRICTED",
        ("group:reader",),
        1,
        "SERVING",
        0,
        0,
        2,
        True,
    )


def test_mysql_projection_authorization_write_lifecycle_and_release(tmp_path: Path) -> None:
    chunk = _chunk()
    row = {
        "chunk_id": chunk.chunk_id,
        "tenant_id": chunk.tenant_id,
        "space_id": chunk.space_id,
        "document_id": chunk.document_id,
        "document_version_id": chunk.document_version_id,
        "parent_chunk_id": None,
        "display_text": chunk.display_text,
        "retrieval_text": chunk.retrieval_text,
        "locator_json": '{"page":1}',
        "content_checksum": chunk.content_checksum,
        "visibility": chunk.visibility,
        "acl_scope_tokens_json": '["group:reader"]',
        "classification_level": 1,
        "lifecycle_projection": "SERVING",
        "valid_from_epoch": 0,
        "valid_to_epoch": 0,
        "permission_revision": 2,
        "current_version": 1,
    }
    release = {
        "tenant_id": "tenant-1",
        "space_id": "space-1",
        "active_generation_id": "generation-1",
        "active_permission_revision": 2,
        "security_watermark": 2,
    }
    connections = []

    def factory(**kwargs):
        del kwargs
        connection = _Connection([row], release)
        connections.append(connection)
        return connection

    settings = load_env(
        Path(__file__).resolve().parents[2], env_path=tmp_path / "missing", environ={}
    ).settings
    assert settings is not None
    adapter = MySQLRetrievalControlPlane(
        MySQLControlPlaneAdapter(settings, connection_factory=factory)
    )
    context = SearchContext("tenant-1", ("space-1",), ("group:reader",), 1, 1, "generation-1", 2, 2)

    assert adapter.authorize_chunks(("chunk-1",), context)["chunk-1"] == chunk
    assert adapter.current_release("tenant-1", "space-1") == RetrievalRelease(
        "tenant-1", "space-1", "generation-1", 2, 2
    )
    adapter.upsert_chunks((chunk,))
    adapter.set_document_projection(
        "document-1",
        active_version_id="version-1",
        lifecycle_projection="SERVING",
        permission_revision=2,
    )
    adapter.set_release(RetrievalRelease("tenant-1", "space-1", "generation-2", 3, 3))
    adapter.delete_document_projection("document-1")

    assert any(connection.executemany_calls for connection in connections)
    assert sum(connection.commits for connection in connections) == 4
    assert all(connection.closed == 1 for connection in connections)
