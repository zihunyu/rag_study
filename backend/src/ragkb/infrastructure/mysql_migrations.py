"""Current MySQL schema for fresh project databases."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ragkb.infrastructure.workspace_schema import WORKSPACE_TABLES, mysql_workspace_migrations

MYSQL_MIGRATION_REVISION = "mysql-current-schema"
MYSQL_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id VARCHAR(128) PRIMARY KEY,
    revision VARCHAR(128) NOT NULL,
    applied_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

MYSQL_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "create_retrieval_chunk_projections",
        """
        CREATE TABLE IF NOT EXISTS retrieval_chunk_projections (
            chunk_id VARCHAR(255) NOT NULL,
            index_generation_id VARCHAR(128) CHARACTER SET ascii NOT NULL,
            tenant_id VARCHAR(255) NOT NULL,
            space_id VARCHAR(255) NOT NULL,
            document_id VARCHAR(255) NOT NULL,
            document_version_id VARCHAR(255) NOT NULL,
            parent_chunk_id VARCHAR(255),
            display_text MEDIUMTEXT NOT NULL,
            retrieval_text MEDIUMTEXT NOT NULL,
            locator_json JSON NOT NULL,
            content_checksum CHAR(64) NOT NULL,
            visibility VARCHAR(32) NOT NULL,
            acl_scope_tokens_json JSON NOT NULL,
            classification_level INT UNSIGNED NOT NULL,
            lifecycle_projection VARCHAR(32) NOT NULL,
            valid_from_epoch BIGINT UNSIGNED NOT NULL,
            valid_to_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0,
            permission_revision BIGINT UNSIGNED NOT NULL,
            current_version BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (tenant_id, index_generation_id, chunk_id),
            KEY idx_retrieval_projection_scope (
                tenant_id, space_id, lifecycle_projection,
                current_version, permission_revision
            ),
            KEY idx_retrieval_projection_document (document_id, document_version_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_retrieval_release_state",
        """
        CREATE TABLE IF NOT EXISTS retrieval_release_state (
            tenant_id VARCHAR(255) NOT NULL,
            space_id VARCHAR(255) NOT NULL,
            active_generation_id VARCHAR(255) NOT NULL,
            active_permission_revision BIGINT UNSIGNED NOT NULL,
            security_watermark BIGINT UNSIGNED NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (tenant_id, space_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_rag_run_documents",
        """
        CREATE TABLE IF NOT EXISTS rag_run_documents (
            run_id VARCHAR(255) PRIMARY KEY,
            tenant_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            status VARCHAR(64) NOT NULL,
            package_json JSON NOT NULL,
            result_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            KEY idx_rag_run_subject (tenant_id, user_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_rag_feedback",
        """
        CREATE TABLE IF NOT EXISTS rag_feedback (
            feedback_id VARCHAR(255) PRIMARY KEY,
            run_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            feedback_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            KEY idx_rag_feedback_run (run_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_reference_tokens",
        """
        CREATE TABLE IF NOT EXISTS reference_tokens (
            opaque_id VARCHAR(128) PRIMARY KEY,
            token_kind VARCHAR(32) NOT NULL,
            tenant_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            run_id VARCHAR(255) NOT NULL,
            evidence_id VARCHAR(64),
            document_id VARCHAR(255),
            expires_at BIGINT UNSIGNED NOT NULL,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at DATETIME(6) NOT NULL,
            KEY idx_reference_subject (tenant_id, user_id, run_id, revoked, expires_at),
            KEY idx_reference_document (document_id, revoked)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_upload_entities",
        """
        CREATE TABLE IF NOT EXISTS upload_entities (
            tenant_id VARCHAR(255) NOT NULL,
            entity_type VARCHAR(64) NOT NULL,
            entity_id VARCHAR(255) NOT NULL,
            logical_key TEXT NOT NULL,
            parent_id VARCHAR(255),
            ordinal INT UNSIGNED NOT NULL DEFAULT 0,
            payload_json JSON NOT NULL,
            entity_revision BIGINT UNSIGNED NOT NULL DEFAULT 1,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (tenant_id, entity_type, entity_id),
            KEY idx_upload_entity_parent (tenant_id, entity_type, parent_id, ordinal),
            KEY idx_upload_entity_updated (tenant_id, entity_type, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_lifecycle_entities",
        """
        CREATE TABLE IF NOT EXISTS lifecycle_entities (
            tenant_id VARCHAR(255) NOT NULL,
            entity_type VARCHAR(64) NOT NULL,
            entity_id VARCHAR(255) NOT NULL,
            logical_key TEXT NOT NULL,
            parent_id VARCHAR(255),
            ordinal INT UNSIGNED NOT NULL DEFAULT 0,
            payload_json JSON NOT NULL,
            entity_revision BIGINT UNSIGNED NOT NULL DEFAULT 1,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (tenant_id, entity_type, entity_id),
            KEY idx_lifecycle_entity_parent (tenant_id, entity_type, parent_id, ordinal),
            KEY idx_lifecycle_entity_updated (tenant_id, entity_type, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_governance_entities",
        """
        CREATE TABLE IF NOT EXISTS governance_entities (
            tenant_id VARCHAR(255) NOT NULL,
            entity_type VARCHAR(64) NOT NULL,
            entity_id VARCHAR(255) NOT NULL,
            logical_key TEXT NOT NULL,
            parent_id VARCHAR(255),
            ordinal INT UNSIGNED NOT NULL DEFAULT 0,
            payload_json JSON NOT NULL,
            entity_revision BIGINT UNSIGNED NOT NULL DEFAULT 1,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (tenant_id, entity_type, entity_id),
            KEY idx_governance_entity_parent (tenant_id, entity_type, parent_id, ordinal),
            KEY idx_governance_entity_updated (tenant_id, entity_type, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_publication_outbox",
        """
        CREATE TABLE IF NOT EXISTS publication_outbox (
            tenant_id VARCHAR(191) NOT NULL,
            operation VARCHAR(191) NOT NULL,
            idempotency_key VARCHAR(191) NOT NULL,
            document_id VARCHAR(255) NOT NULL,
            target_version_id VARCHAR(255) NOT NULL,
            generation_id VARCHAR(255) NOT NULL,
            state VARCHAR(32) NOT NULL,
            attempt_count INT UNSIGNED NOT NULL DEFAULT 1,
            error_code VARCHAR(128),
            payload_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (tenant_id, operation, idempotency_key),
            KEY idx_publication_outbox_state (state, updated_at),
            KEY idx_publication_outbox_document (tenant_id, document_id, state)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_index_jobs",
        """
        CREATE TABLE IF NOT EXISTS index_jobs (
            index_job_id VARCHAR(255) PRIMARY KEY,
            tenant_id VARCHAR(255) NOT NULL,
            space_id VARCHAR(255) NOT NULL,
            document_id VARCHAR(255) NOT NULL,
            document_version_id VARCHAR(255) NOT NULL,
            generation_id VARCHAR(255) NOT NULL,
            expected_count BIGINT UNSIGNED NOT NULL,
            expected_checksum CHAR(64) NOT NULL,
            expected_manifest_json JSON NOT NULL,
            state VARCHAR(32) NOT NULL,
            attempt_number INT UNSIGNED NOT NULL DEFAULT 1,
            error_code VARCHAR(128),
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            UNIQUE KEY uq_index_job_generation_version (generation_id, document_version_id),
            KEY idx_index_job_state (tenant_id, state, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_index_batches",
        """
        CREATE TABLE IF NOT EXISTS index_batches (
            index_job_id VARCHAR(255) NOT NULL,
            batch_number INT UNSIGNED NOT NULL,
            attempt_number INT UNSIGNED NOT NULL,
            chunk_manifest_json JSON NOT NULL,
            batch_checksum CHAR(64) NOT NULL,
            vector_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            control_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (index_job_id, batch_number),
            KEY idx_index_batch_attempt (index_job_id, attempt_number, batch_number),
            CONSTRAINT fk_index_batch_job FOREIGN KEY (index_job_id)
                REFERENCES index_jobs(index_job_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "create_ingestion_fences",
        """
        CREATE TABLE IF NOT EXISTS ingestion_fences (
            tenant_id VARCHAR(191) NOT NULL,
            version_id VARCHAR(191) NOT NULL,
            job_id VARCHAR(191) NOT NULL,
            fence_token BIGINT UNSIGNED NOT NULL,
            PRIMARY KEY (tenant_id, version_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)

MYSQL_MIGRATIONS += mysql_workspace_migrations()

# Event ordering uses epoch microseconds, which exceed INT UNSIGNED even today.
# Append a widening migration so deployed databases retain their rows and migration IDs.
MYSQL_MIGRATIONS += (
    (
        "widen_governance_event_ordinal",
        "ALTER TABLE governance_entities MODIFY COLUMN ordinal BIGINT UNSIGNED NOT NULL DEFAULT 0",
    ),
)

PROJECT_TABLES = frozenset(
    {
        "schema_migrations",
        "retrieval_chunk_projections",
        "retrieval_release_state",
        "rag_run_documents",
        "rag_feedback",
        "reference_tokens",
        "upload_entities",
        "lifecycle_entities",
        "governance_entities",
        "publication_outbox",
        "index_jobs",
        "index_batches",
        "ingestion_fences",
    }
) | frozenset(WORKSPACE_TABLES)


class CursorLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[Any] | None = None) -> Any: ...

    def fetchone(self) -> Any: ...


class ConnectionLike(Protocol):
    def cursor(self) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def migration_plan() -> dict[str, object]:
    return {
        "revision": MYSQL_MIGRATION_REVISION,
        "migration_ids": [migration_id for migration_id, _ in MYSQL_MIGRATIONS],
        "statement_count": len(MYSQL_MIGRATIONS),
        "mutating_execution_performed": False,
    }


def apply_mysql_migrations(connection: ConnectionLike) -> dict[str, object]:
    """Apply recorded idempotent migrations to an explicitly supplied project database."""
    cursor = connection.cursor()
    applied: list[str] = []
    skipped: list[str] = []
    try:
        cursor.execute(MYSQL_MIGRATION_TABLE_SQL)
        connection.commit()
        migrations = MYSQL_MIGRATIONS
        cursor.execute("SELECT migration_id FROM schema_migrations")
        recorded = {row[0] for row in cursor.fetchall()}
        if recorded - {migration_id for migration_id, _ in migrations}:
            raise RuntimeError("MYSQL_SCHEMA_UNSUPPORTED_USE_NEW_DATABASE")
        for migration_id, statement in migrations:
            if migration_id in recorded:
                skipped.append(migration_id)
                continue
            cursor.execute(statement)
            cursor.execute(
                """
                INSERT INTO schema_migrations(migration_id, revision, applied_at)
                VALUES (%s, %s, NOW(6))
                """,
                (migration_id, MYSQL_MIGRATION_REVISION),
            )
            connection.commit()
            applied.append(migration_id)
    except Exception:
        connection.rollback()
        raise
    return {
        "revision": MYSQL_MIGRATION_REVISION,
        "planned_count": len(migrations),
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "applied_ids": applied,
        "skipped_ids": skipped,
        "failed_count": 0,
    }
