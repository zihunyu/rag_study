from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from ragkb.infrastructure.sqlite import SCHEMA_REVISION, SQLiteDatabase


def test_current_schema_initializes_once_and_retains_data(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "fresh.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute("INSERT INTO tenants VALUES ('tenant', 'example', 'ACTIVE', 0)")
    database.initialize()
    with database.connect() as connection:
        assert connection.execute("SELECT code FROM tenants").fetchone()[0] == "example"
        assert (
            connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_revision'"
            ).fetchone()[0]
            == SCHEMA_REVISION
        )
        required_columns = {
            "job_queue": {"fence_token", "cancel_requested", "heartbeat_at"},
            "chunks": {"kind", "chunking_revision", "tokenizer_id"},
            "document_reviews": {"security_revision", "security_projection_json"},
            "upload_sessions": {"target_document_id", "target_document_row_version"},
        }
        for table, required in required_columns.items():
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert required <= columns, table


@pytest.mark.parametrize("with_metadata", [True, False])
def test_incompatible_database_is_rejected_without_changing_data(
    tmp_path: Path, with_metadata: bool
) -> None:
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE old_data (value TEXT); INSERT INTO old_data VALUES ('keep');"
        )
        if with_metadata:
            connection.executescript(
                "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "INSERT INTO schema_metadata VALUES ('schema_version', '1');"
            )
    with pytest.raises(RuntimeError, match="SQLITE_SCHEMA_UNSUPPORTED_USE_NEW_DATABASE"):
        SQLiteDatabase(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM old_data").fetchone()[0] == "keep"
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name = 'job_queue'").fetchone()
            is None
        )


def test_backend_and_worker_can_initialize_one_fresh_database_together(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "shared.sqlite3"
    barrier = Barrier(2)

    def initialize():
        barrier.wait(timeout=5)
        SQLiteDatabase(path).initialize()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(initialize) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    with SQLiteDatabase(path).connect() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT fence_token FROM job_queue").fetchall() == []
