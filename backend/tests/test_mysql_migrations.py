from __future__ import annotations

import pytest
from ragkb.infrastructure.mysql_migrations import MYSQL_MIGRATIONS, apply_mysql_migrations


class _MigrationCursor:
    def __init__(self, connection: _MigrationConnection) -> None:
        self.connection = connection
        self.row = None

    def execute(self, statement: str, parameters=None):
        normalized = " ".join(statement.split())
        self.connection.statements.append(normalized)
        if normalized.startswith("SELECT migration_id"):
            migration_id = parameters[0]
            self.row = (migration_id,) if migration_id in self.connection.applied else None
        elif normalized.startswith("INSERT INTO schema_migrations"):
            migration_id = parameters[0]
            self.connection.applied.add(migration_id)
        elif self.connection.fail_table and self.connection.fail_table in normalized:
            raise RuntimeError("simulated migration DDL failure")
        else:
            self.row = None

    def fetchone(self):
        return self.row


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

    assert first["applied_count"] == len(MYSQL_MIGRATIONS) == 5
    assert first["skipped_count"] == 0
    assert second["applied_count"] == 0
    assert second["skipped_count"] == 5
    assert connection.applied == {migration_id for migration_id, _ in MYSQL_MIGRATIONS}
    assert not any("DROP " in statement.upper() for statement in connection.statements)


def test_migration_failure_rolls_back_and_does_not_record_failed_step() -> None:
    connection = _MigrationConnection(fail_table="index_generations")

    with pytest.raises(RuntimeError, match="migration DDL failure"):
        apply_mysql_migrations(connection)

    assert connection.rollbacks == 1
    assert "001_index_profiles" in connection.applied
    assert "002_index_generations" not in connection.applied
