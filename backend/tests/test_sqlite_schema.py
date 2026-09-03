from __future__ import annotations

import sqlite3
from pathlib import Path

from ragkb.infrastructure.sqlite import SCHEMA_VERSION, SQLiteDatabase


def test_schema_v1_database_is_migrated_to_revisioned_chunk_contract(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_metadata(key, value) VALUES ('schema_version', '1');
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                section_id TEXT NOT NULL,
                parent_chunk_id TEXT,
                ordinal INTEGER NOT NULL,
                original_text TEXT NOT NULL,
                display_text TEXT NOT NULL,
                retrieval_text TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            """
        )

    database = SQLiteDatabase(path)
    database.initialize()

    with database.connect() as connection:
        columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(chunks)").fetchall()
        }
        revision = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
    assert {"kind", "chunking_revision", "tokenizer_id"}.issubset(columns)
    assert int(revision["value"]) == SCHEMA_VERSION == 15
