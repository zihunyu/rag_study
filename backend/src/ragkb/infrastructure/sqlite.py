"""Shared SQLite connection and G1 schema initialization."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 14

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
    kind TEXT NOT NULL DEFAULT 'paragraph',
    chunking_revision TEXT NOT NULL DEFAULT 'node-per-chunk:g1-v1',
    tokenizer_id TEXT NOT NULL DEFAULT 'whitespace-estimate:g1-v1',
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS local_search_index (
    chunk_id TEXT PRIMARY KEY,
    document_version_id TEXT NOT NULL,
    parent_chunk_id TEXT,
    retrieval_text TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    index_generation_id TEXT NOT NULL,
    security_watermark INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_search_generation
    ON local_search_index(index_generation_id);
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
    target_document_id TEXT,
    target_document_row_version INTEGER,
    job_id TEXT,
    error_code TEXT,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_content_lineage (
    document_id TEXT NOT NULL,
    version_id TEXT,
    partition TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    content_kind TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(document_id, partition, storage_key)
);
CREATE INDEX IF NOT EXISTS idx_local_content_lineage_version
    ON local_content_lineage(document_id, version_id);
CREATE TABLE IF NOT EXISTS publication_candidates (
    version_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    projection_state TEXT NOT NULL,
    required_watermark INTEGER NOT NULL,
    observed_watermark INTEGER NOT NULL,
    expected_checksum TEXT NOT NULL,
    observed_checksum TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publication_candidates_document
    ON publication_candidates(document_id, projection_state);
CREATE TABLE IF NOT EXISTS document_quality_reports (
    version_id TEXT PRIMARY KEY,
    source_format TEXT NOT NULL,
    parser_revision TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    locator_coverage REAL NOT NULL,
    issue_codes_json TEXT NOT NULL,
    disposition TEXT NOT NULL,
    real_acceptance INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS document_reviews (
    review_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL,
    quality_revision TEXT NOT NULL,
    real_acceptance INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_document_reviews_version
    ON document_reviews(version_id, created_at);
CREATE TABLE IF NOT EXISTS runtime_events (
    event_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_events_trace
    ON runtime_events(trace_id, created_at);
CREATE TABLE IF NOT EXISTS governance_register (
    record_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    owner TEXT NOT NULL,
    state TEXT NOT NULL,
    revision TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    UNIQUE(category, revision)
);
CREATE TABLE IF NOT EXISTS evidence_index (
    evidence_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    revision TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(category, revision)
);
CREATE TABLE IF NOT EXISTS pilot_records (
    pilot_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    feature_flag TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
    real_acceptance INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_signoffs (
    signoff_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    role TEXT NOT NULL,
    decision TEXT NOT NULL,
    signer_id TEXT NOT NULL,
    comment TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_idempotency (
    tenant_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(tenant_id, operation, idempotency_key)
);
CREATE TABLE IF NOT EXISTS canary_runs (
    run_id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL,
    seed INTEGER NOT NULL,
    request_count INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    threshold INTEGER NOT NULL,
    result TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_governance_signoffs_scope
    ON governance_signoffs(scope_type, scope_id, role, created_at);
CREATE TABLE IF NOT EXISTS rollout_batches (
    batch_id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    percentage INTEGER NOT NULL,
    state TEXT NOT NULL,
    rollback_trigger TEXT,
    simulated INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    UNIQUE(pilot_id, ordinal)
);
CREATE TABLE IF NOT EXISTS uat_cases (
    case_id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    result TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    step_results_json TEXT NOT NULL DEFAULT '[]',
    row_version INTEGER NOT NULL DEFAULT 1,
    simulated INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_defects (
    defect_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_governance_defects_scope
    ON governance_defects(scope_type, scope_id, severity, state);
CREATE TABLE IF NOT EXISTS observation_windows (
    window_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    starts_at REAL NOT NULL,
    ends_at REAL NOT NULL,
    state TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
    real_acceptance INTEGER NOT NULL DEFAULT 0,
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1,
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
CREATE TABLE IF NOT EXISTS retrieval_projections (
    chunk_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    document_version_id TEXT NOT NULL,
    parent_chunk_id TEXT,
    display_text TEXT NOT NULL,
    retrieval_text TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    content_checksum TEXT NOT NULL,
    visibility TEXT NOT NULL,
    acl_scope_tokens_json TEXT NOT NULL,
    classification_level INTEGER NOT NULL,
    lifecycle_projection TEXT NOT NULL,
    valid_from_epoch INTEGER NOT NULL,
    valid_to_epoch INTEGER NOT NULL,
    permission_revision INTEGER NOT NULL,
    current_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retrieval_projection_scope
    ON retrieval_projections(tenant_id, space_id, lifecycle_projection, classification_level);
CREATE TABLE IF NOT EXISTS rag_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    query TEXT NOT NULL,
    status TEXT NOT NULL,
    package_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rag_evidence (
    run_id TEXT NOT NULL REFERENCES rag_runs(run_id),
    evidence_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(run_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS user_feedback (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES rag_runs(run_id),
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    comment TEXT NOT NULL,
    index_generation_id TEXT NOT NULL,
    retrieval_revision TEXT NOT NULL,
    prompt_revision TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_records (
    document_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    active_version_id TEXT,
    version_history_json TEXT NOT NULL DEFAULT '[]',
    lifecycle_state TEXT NOT NULL,
    acl_revision INTEGER NOT NULL,
    visible INTEGER NOT NULL,
    tombstoned INTEGER NOT NULL,
    row_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS security_transitions (
    transition_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    target_acl_revision INTEGER NOT NULL,
    required_watermark INTEGER NOT NULL,
    observed_watermark INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS deletion_tombstones (
    document_id TEXT PRIMARY KEY,
    cleanup_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    governance_revision TEXT NOT NULL DEFAULT 'lifecycle-orchestration:g3-v1',
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS lifecycle_idempotency (
    tenant_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(tenant_id, operation, idempotency_key)
);
CREATE TABLE IF NOT EXISTS cleanup_outbox (
    document_id TEXT NOT NULL,
    target_store TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(document_id, target_store)
);
CREATE TABLE IF NOT EXISTS reference_tokens (
    opaque_id TEXT PRIMARY KEY,
    token_kind TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    evidence_id TEXT,
    document_id TEXT,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reference_subject
    ON reference_tokens(tenant_id, user_id, run_id, revoked, expires_at);
CREATE INDEX IF NOT EXISTS idx_reference_document
    ON reference_tokens(document_id, revoked);
"""

CHUNK_V2_COLUMNS = {
    "kind": "kind TEXT NOT NULL DEFAULT 'paragraph'",
    "chunking_revision": ("chunking_revision TEXT NOT NULL DEFAULT 'node-per-chunk:g1-v1'"),
    "tokenizer_id": "tokenizer_id TEXT NOT NULL DEFAULT 'whitespace-estimate:g1-v1'",
}
AUDIT_V6_COLUMNS = {
    "governance_revision": (
        "governance_revision TEXT NOT NULL DEFAULT 'lifecycle-orchestration:g3-v1'"
    )
}
LIFECYCLE_V7_COLUMNS = {
    "tenant_id": "tenant_id TEXT NOT NULL DEFAULT 'local'",
    "version_history_json": "version_history_json TEXT NOT NULL DEFAULT '[]'",
}
UPLOAD_SESSION_V10_COLUMNS = {
    "target_document_id": "target_document_id TEXT",
    "target_document_row_version": "target_document_row_version INTEGER",
}
UAT_V14_COLUMNS = {
    "pilot_id": "pilot_id TEXT NOT NULL DEFAULT ''",
    "step_results_json": "step_results_json TEXT NOT NULL DEFAULT '[]'",
    "row_version": "row_version INTEGER NOT NULL DEFAULT 1",
}
OBSERVATION_V14_COLUMNS = {"row_version": "row_version INTEGER NOT NULL DEFAULT 1"}
DEFECT_V14_COLUMNS = {"row_version": "row_version INTEGER NOT NULL DEFAULT 1"}
INCIDENT_V14_COLUMNS = {"row_version": "row_version INTEGER NOT NULL DEFAULT 1"}


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            existing_chunk_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(chunks)").fetchall()
            }
            for name, definition in CHUNK_V2_COLUMNS.items():
                if name not in existing_chunk_columns:
                    connection.execute(f"ALTER TABLE chunks ADD COLUMN {definition}")  # noqa: S608
            existing_audit_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(audit_events)").fetchall()
            }
            for name, definition in AUDIT_V6_COLUMNS.items():
                if name not in existing_audit_columns:
                    connection.execute(  # noqa: S608
                        f"ALTER TABLE audit_events ADD COLUMN {definition}"
                    )
            existing_lifecycle_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(lifecycle_records)").fetchall()
            }
            for name, definition in LIFECYCLE_V7_COLUMNS.items():
                if name not in existing_lifecycle_columns:
                    connection.execute(  # noqa: S608
                        f"ALTER TABLE lifecycle_records ADD COLUMN {definition}"
                    )
            existing_upload_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(upload_sessions)").fetchall()
            }
            for name, definition in UPLOAD_SESSION_V10_COLUMNS.items():
                if name not in existing_upload_columns:
                    connection.execute(  # noqa: S608
                        f"ALTER TABLE upload_sessions ADD COLUMN {definition}"
                    )
            for table, columns in (
                ("uat_cases", UAT_V14_COLUMNS),
                ("observation_windows", OBSERVATION_V14_COLUMNS),
                ("governance_defects", DEFECT_V14_COLUMNS),
                ("incidents", INCIDENT_V14_COLUMNS),
            ):
                existing = {
                    str(row["name"])
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
                }
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(  # noqa: S608
                            f"ALTER TABLE {table} ADD COLUMN {definition}"
                        )
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
