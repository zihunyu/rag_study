from __future__ import annotations

import pytest
from ragkb.infrastructure.mysql_migrations import (
    MYSQL_MIGRATIONS,
    apply_mysql_migrations,
)


class _MigrationCursor:
    def __init__(self, connection: _MigrationConnection) -> None:
        self.connection = connection
        self.row = None
        self.rows = []

    def execute(self, statement: str, parameters=None):
        normalized = " ".join(statement.split())
        self.connection.statements.append(normalized)
        if normalized.startswith("SELECT migration_id"):
            self.rows = [(migration_id,) for migration_id in self.connection.applied]
        elif normalized.startswith("INSERT INTO schema_migrations"):
            migration_id = parameters[0]
            self.connection.applied.add(migration_id)
        elif self.connection.fail_table and self.connection.fail_table in normalized:
            raise RuntimeError("simulated migration DDL failure")
        else:
            self.row = None

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _MigrationConnection:
    def __init__(self, *, fail_table: str | None = None) -> None:
        self.applied: set[str] = set()
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_table = fail_table

    def cursor(self):
        return _MigrationCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_recorded_migrations_apply_once_and_second_run_is_idempotent() -> None:
    connection = _MigrationConnection()

    first = apply_mysql_migrations(connection)
    second = apply_mysql_migrations(connection)

    migrations = MYSQL_MIGRATIONS
    assert first["applied_count"] == len(migrations)
    assert first["skipped_count"] == 0
    assert second["applied_count"] == 0
    assert second["skipped_count"] == len(migrations)
    assert connection.applied == {migration_id for migration_id, _ in migrations}
    assert not any(
        "DROP TABLE" in statement.upper() or "DROP DATABASE" in statement.upper()
        for statement in connection.statements
    )


def test_publication_outbox_primary_key_fits_mysql_utf8mb4_index_limit() -> None:
    statement = dict(MYSQL_MIGRATIONS)["create_publication_outbox"]

    assert "tenant_id VARCHAR(191)" in statement
    assert "operation VARCHAR(191)" in statement
    assert "idempotency_key VARCHAR(191)" in statement


def test_existing_incompatible_database_is_rejected_before_business_ddl() -> None:
    connection = _MigrationConnection()
    connection.applied.add("historical-schema")
    with pytest.raises(RuntimeError, match="MYSQL_SCHEMA_UNSUPPORTED_USE_NEW_DATABASE"):
        apply_mysql_migrations(connection)
    assert connection.applied == {"historical-schema"}
    assert not any("CREATE TABLE IF NOT EXISTS retrieval" in sql for sql in connection.statements)


def test_migration_failure_rolls_back_and_does_not_record_failed_step() -> None:
    connection = _MigrationConnection(fail_table="retrieval_release_state")

    with pytest.raises(RuntimeError, match="migration DDL failure"):
        apply_mysql_migrations(connection)

    assert connection.rollbacks == 1
    assert "create_retrieval_chunk_projections" in connection.applied
    assert "create_retrieval_release_state" not in connection.applied
