"""Plan-only MySQL control-plane migrations for G2 indexing state."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

MYSQL_MIGRATION_REVISION = "mysql-control-plane:g4-v4"
MYSQL_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id VARCHAR(128) PRIMARY KEY,
    revision VARCHAR(128) NOT NULL,
    applied_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

MYSQL_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "001_index_profiles",
        """
        CREATE TABLE IF NOT EXISTS index_profiles (
            id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            revision VARCHAR(128) NOT NULL,
            embedding_model VARCHAR(255) NOT NULL,
            embedding_dimension INT UNSIGNED NOT NULL,
            metric_type VARCHAR(16) NOT NULL,
            analyzer_revision VARCHAR(128) NOT NULL,
            schema_fingerprint CHAR(64) NOT NULL,
            created_at DATETIME(6) NOT NULL,
            UNIQUE KEY uq_index_profile_revision (tenant_id, revision)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "002_index_generations",
        """
        CREATE TABLE IF NOT EXISTS index_generations (
            id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            space_id BINARY(16) NOT NULL,
            index_profile_id BINARY(16) NOT NULL,
            collection_family VARCHAR(255) NOT NULL,
            generation_id VARCHAR(128) NOT NULL,
            source_snapshot_seq BIGINT UNSIGNED NOT NULL,
            last_applied_event_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
            security_watermark BIGINT UNSIGNED NOT NULL DEFAULT 0,
            state VARCHAR(32) NOT NULL,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            UNIQUE KEY uq_generation (tenant_id, space_id, generation_id),
            KEY idx_generation_state (tenant_id, space_id, state)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "003_retrieval_releases",
        """
        CREATE TABLE IF NOT EXISTS retrieval_releases (
            id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            space_id BINARY(16) NOT NULL,
            active_generation_id BINARY(16) NOT NULL,
            active_permission_revision BIGINT UNSIGNED NOT NULL,
            source_snapshot_seq BIGINT UNSIGNED NOT NULL,
            last_applied_event_seq BIGINT UNSIGNED NOT NULL,
            security_watermark BIGINT UNSIGNED NOT NULL,
            row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            UNIQUE KEY uq_retrieval_release_space (tenant_id, space_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "004_index_entries",
        """
        CREATE TABLE IF NOT EXISTS index_entries (
            generation_id BINARY(16) NOT NULL,
            tenant_id BINARY(16) NOT NULL,
            chunk_id BINARY(16) NOT NULL,
            zilliz_pk VARCHAR(255) NOT NULL,
            content_checksum CHAR(64) NOT NULL,
            state VARCHAR(32) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (generation_id, chunk_id),
            UNIQUE KEY uq_index_entry_pk (tenant_id, zilliz_pk)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "005_retrieval_outbox",
        """
        CREATE TABLE IF NOT EXISTS retrieval_outbox (
            event_id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            space_id BINARY(16) NOT NULL,
            aggregate_id BINARY(16) NOT NULL,
            aggregate_version BIGINT UNSIGNED NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            payload_json JSON NOT NULL,
            state VARCHAR(32) NOT NULL,
            created_at DATETIME(6) NOT NULL,
            UNIQUE KEY uq_retrieval_event_order (
                tenant_id, space_id, aggregate_id, aggregate_version, event_type
            ),
            KEY idx_retrieval_outbox_state (state, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "006_retrieval_chunk_projections",
        """
        CREATE TABLE IF NOT EXISTS retrieval_chunk_projections (
            chunk_id VARCHAR(255) PRIMARY KEY,
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
            KEY idx_retrieval_projection_scope (
                tenant_id, space_id, lifecycle_projection,
                current_version, permission_revision
            ),
            KEY idx_retrieval_projection_document (document_id, document_version_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "007_retrieval_release_state",
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
)

MYSQL_G3_MIGRATION_REVISION = "mysql-trusted-qa-lifecycle:g4-v4"
MYSQL_G3_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "101_rag_runs",
        """
        CREATE TABLE IF NOT EXISTS rag_runs (
            id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            user_id BINARY(16) NOT NULL,
            query TEXT NOT NULL,
            result_status VARCHAR(64) NOT NULL,
            index_generation_id VARCHAR(128) NOT NULL,
            retrieval_revision VARCHAR(128) NOT NULL,
            prompt_revision VARCHAR(128) NOT NULL,
            model_revision VARCHAR(255) NOT NULL,
            permission_revision BIGINT UNSIGNED NOT NULL,
            result_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            KEY idx_rag_run_tenant_created (tenant_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "102_rag_evidence",
        """
        CREATE TABLE IF NOT EXISTS rag_evidence (
            run_id BINARY(16) NOT NULL,
            evidence_id VARCHAR(32) NOT NULL,
            chunk_id BINARY(16) NOT NULL,
            document_version_id BINARY(16) NOT NULL,
            locator_json JSON NOT NULL,
            authority_rank INT NOT NULL,
            valid_from DATETIME(6),
            valid_to DATETIME(6),
            PRIMARY KEY(run_id, evidence_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "103_user_feedback",
        """
        CREATE TABLE IF NOT EXISTS user_feedback (
            id BINARY(16) PRIMARY KEY,
            run_id BINARY(16) NOT NULL,
            user_id BINARY(16) NOT NULL,
            rating TINYINT UNSIGNED NOT NULL,
            reason_code VARCHAR(64) NOT NULL,
            comment TEXT NOT NULL,
            revision_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            KEY idx_feedback_run (run_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "104_security_transitions",
        """
        CREATE TABLE IF NOT EXISTS security_transitions (
            id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            document_id BINARY(16) NOT NULL,
            target_acl_revision BIGINT UNSIGNED NOT NULL,
            required_watermark BIGINT UNSIGNED NOT NULL,
            observed_watermark BIGINT UNSIGNED NOT NULL DEFAULT 0,
            state VARCHAR(32) NOT NULL,
            error_code VARCHAR(128),
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            KEY idx_security_transition_state (tenant_id, state, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "105_deletion_tombstones",
        """
        CREATE TABLE IF NOT EXISTS deletion_tombstones (
            document_id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            cleanup_state_json JSON NOT NULL,
            retention_until DATETIME(6),
            created_at DATETIME(6) NOT NULL,
            KEY idx_tombstone_retention (tenant_id, retention_until)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "106_append_only_audit",
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id BINARY(16) PRIMARY KEY,
            tenant_id BINARY(16) NOT NULL,
            sequence_no BIGINT UNSIGNED NOT NULL,
            action VARCHAR(128) NOT NULL,
            resource_type VARCHAR(64) NOT NULL,
            resource_id BINARY(16) NOT NULL,
            trace_id VARCHAR(128) NOT NULL,
            previous_hash CHAR(64) NOT NULL,
            event_hash CHAR(64) NOT NULL,
            governance_revision VARCHAR(128) NOT NULL,
            created_at DATETIME(6) NOT NULL,
            UNIQUE KEY uq_audit_sequence (tenant_id, sequence_no),
            UNIQUE KEY uq_audit_hash (tenant_id, event_hash)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "107_lifecycle_idempotency",
        """
        CREATE TABLE IF NOT EXISTS lifecycle_idempotency (
            tenant_id BINARY(16) NOT NULL,
            operation VARCHAR(128) NOT NULL,
            idempotency_key VARCHAR(255) NOT NULL,
            request_hash CHAR(64) NOT NULL,
            response_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            PRIMARY KEY(tenant_id, operation, idempotency_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "108_cleanup_outbox",
        """
        CREATE TABLE IF NOT EXISTS cleanup_outbox (
            document_id BINARY(16) NOT NULL,
            target_store VARCHAR(64) NOT NULL,
            state VARCHAR(32) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY(document_id, target_store)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "109_reference_tokens",
        """
        CREATE TABLE IF NOT EXISTS reference_tokens (
            opaque_id VARCHAR(128) PRIMARY KEY,
            token_kind VARCHAR(32) NOT NULL,
            tenant_id BINARY(16) NOT NULL,
            user_id BINARY(16) NOT NULL,
            run_id BINARY(16) NOT NULL,
            evidence_id VARCHAR(32),
            document_id BINARY(16),
            expires_at DATETIME(6) NOT NULL,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at DATETIME(6) NOT NULL,
            KEY idx_reference_subject (tenant_id, user_id, run_id, revoked, expires_at),
            KEY idx_reference_document (document_id, revoked)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "110_rag_run_documents_v2",
        """
        CREATE TABLE IF NOT EXISTS rag_run_documents_v2 (
            run_id VARCHAR(255) PRIMARY KEY,
            tenant_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            status VARCHAR(64) NOT NULL,
            package_json JSON NOT NULL,
            result_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            KEY idx_rag_run_v2_subject (tenant_id, user_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "111_rag_feedback_v2",
        """
        CREATE TABLE IF NOT EXISTS rag_feedback_v2 (
            feedback_id VARCHAR(255) PRIMARY KEY,
            run_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            feedback_json JSON NOT NULL,
            created_at DATETIME(6) NOT NULL,
            KEY idx_rag_feedback_v2_run (run_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "112_reference_tokens_v2",
        """
        CREATE TABLE IF NOT EXISTS reference_tokens_v2 (
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
            KEY idx_reference_v2_subject (tenant_id, user_id, run_id, revoked, expires_at),
            KEY idx_reference_v2_document (document_id, revoked)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "113_lifecycle_state_v2",
        """
        CREATE TABLE IF NOT EXISTS lifecycle_state_v2 (
            tenant_id VARCHAR(255) PRIMARY KEY,
            state_json JSON NOT NULL,
            updated_at DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "114_index_jobs_v2",
        """
        CREATE TABLE IF NOT EXISTS index_jobs_v2 (
            index_job_id VARCHAR(255) PRIMARY KEY,
            tenant_id VARCHAR(255) NOT NULL,
            space_id VARCHAR(255) NOT NULL,
            document_id VARCHAR(255) NOT NULL,
            document_version_id VARCHAR(255) NOT NULL,
            generation_id VARCHAR(255) NOT NULL,
            expected_count BIGINT UNSIGNED NOT NULL,
            expected_checksum CHAR(64) NOT NULL,
            state VARCHAR(32) NOT NULL,
            error_code VARCHAR(128),
            created_at DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL,
            UNIQUE KEY uq_index_job_generation_version (generation_id, document_version_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "115_index_batches_v2",
        """
        CREATE TABLE IF NOT EXISTS index_batches_v2 (
            index_job_id VARCHAR(255) NOT NULL,
            batch_number INT UNSIGNED NOT NULL,
            chunk_ids_json JSON NOT NULL,
            batch_checksum CHAR(64) NOT NULL,
            vector_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            control_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (index_job_id, batch_number)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "116_upload_state_v2",
        """
        CREATE TABLE IF NOT EXISTS upload_state_v2 (
            tenant_id VARCHAR(255) PRIMARY KEY,
            state_json JSON NOT NULL,
            updated_at DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "117_governance_state_v2",
        """
        CREATE TABLE IF NOT EXISTS governance_state_v2 (
            tenant_id VARCHAR(255) PRIMARY KEY,
            state_json JSON NOT NULL,
            updated_at DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "118_upload_entities_v3",
        """
        CREATE TABLE IF NOT EXISTS upload_entities_v3 (
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
        "119_lifecycle_entities_v3",
        """
        CREATE TABLE IF NOT EXISTS lifecycle_entities_v3 (
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
        "120_governance_entities_v3",
        """
        CREATE TABLE IF NOT EXISTS governance_entities_v3 (
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
        "121_publication_outbox_v3",
        """
        CREATE TABLE IF NOT EXISTS publication_outbox_v3 (
            tenant_id VARCHAR(255) NOT NULL,
            operation VARCHAR(320) NOT NULL,
            idempotency_key VARCHAR(255) NOT NULL,
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
        "122_index_jobs_v3",
        """
        CREATE TABLE IF NOT EXISTS index_jobs_v3 (
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
            UNIQUE KEY uq_index_job_v3_generation_version (generation_id, document_version_id),
            KEY idx_index_job_v3_state (tenant_id, state, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
    (
        "123_index_batches_v3",
        """
        CREATE TABLE IF NOT EXISTS index_batches_v3 (
            index_job_id VARCHAR(255) NOT NULL,
            batch_number INT UNSIGNED NOT NULL,
            attempt_number INT UNSIGNED NOT NULL,
            chunk_manifest_json JSON NOT NULL,
            batch_checksum CHAR(64) NOT NULL,
            vector_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            control_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at DATETIME(6) NOT NULL,
            PRIMARY KEY (index_job_id, batch_number),
            KEY idx_index_batch_v3_attempt (index_job_id, attempt_number, batch_number),
            CONSTRAINT fk_index_batch_v3_job FOREIGN KEY (index_job_id)
                REFERENCES index_jobs_v3(index_job_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)


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


def g3_migration_plan() -> dict[str, object]:
    return {
        "revision": MYSQL_G3_MIGRATION_REVISION,
        "migration_ids": [migration_id for migration_id, _ in MYSQL_G3_MIGRATIONS],
        "statement_count": len(MYSQL_G3_MIGRATIONS),
        "mutating_execution_performed": False,
        "real_database_execution_approved": False,
    }


def apply_mysql_migrations(connection: ConnectionLike) -> dict[str, object]:
    """Apply recorded idempotent migrations to an explicitly supplied project database."""
    cursor = connection.cursor()
    applied: list[str] = []
    skipped: list[str] = []
    try:
        cursor.execute(MYSQL_MIGRATION_TABLE_SQL)
        connection.commit()
        migrations = (*MYSQL_MIGRATIONS, *MYSQL_G3_MIGRATIONS)
        for migration_id, statement in migrations:
            cursor.execute(
                "SELECT migration_id FROM schema_migrations WHERE migration_id = %s",
                (migration_id,),
            )
            if cursor.fetchone() is not None:
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
