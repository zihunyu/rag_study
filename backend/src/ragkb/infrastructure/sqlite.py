"""Shared SQLite connection and G1 schema initialization."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_queue (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    available_at REAL NOT NULL,
    lease_owner TEXT,
    lease_expires_at REAL,
    heartbeat_at REAL,
    next_retry_at REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    result_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(operation, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_job_queue_lease
    ON job_queue(state, available_at, next_retry_at, created_at);

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_spaces (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(tenant_id, name)
);
CREATE TABLE IF NOT EXISTS corpora (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(space_id, name)
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    corpus_id TEXT NOT NULL REFERENCES corpora(id),
    kind TEXT NOT NULL,
    external_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(corpus_id, external_key)
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    external_key TEXT NOT NULL,
    state TEXT NOT NULL,
    current_version_id TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(source_id, external_key)
);
CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    version_no INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    original_key TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    processing_state TEXT NOT NULL,
    publication_state TEXT NOT NULL,
    parser_revision TEXT,
    created_at REAL NOT NULL,
    UNIQUE(document_id, version_no)
);
CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES document_versions(id),
    parent_id TEXT,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    locator_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES document_versions(id),
    section_id TEXT NOT NULL REFERENCES sections(id),
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
CREATE TABLE IF NOT EXISTS upload_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    filename TEXT NOT NULL,
    expected_size INTEGER NOT NULL,
    expected_sha256 TEXT NOT NULL,
    declared_mime TEXT NOT NULL,
    state TEXT NOT NULL,
    quarantine_key TEXT NOT NULL,
    original_key TEXT,
    detected_format TEXT,
    detected_mime TEXT,
    document_id TEXT,
    document_version_id TEXT,
    job_id TEXT,
    error_code TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_records (
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    response_json TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY(operation, idempotency_key)
);
"""


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT OR REPLACE INTO schema_metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
