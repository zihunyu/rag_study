"""Explicitly approved MySQL G2 database creation, migration and validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.config import EnvSettings
from ragkb.infrastructure.mysql_migrations import (
    MYSQL_G3_MIGRATIONS,
    MYSQL_MIGRATIONS,
    apply_mysql_migrations,
)

MYSQL_APPROVAL = "MYSQL_DATABASE_CREATE_AND_MIGRATE_APPROVED"
PROJECT_TABLES = frozenset(
    {
        "schema_migrations",
        "index_profiles",
        "index_generations",
        "retrieval_releases",
        "index_entries",
        "retrieval_outbox",
        "retrieval_chunk_projections",
        "retrieval_release_state",
    }
)


class MySQLProvisionError(RuntimeError):
    def __init__(self, stage: str, error: Exception) -> None:
        super().__init__("MYSQL_G2_PROVISION_FAILED")
        self.stage = stage
        self.error_type = type(error).__name__
        self.mysql_error_code = (
            error.args[0] if error.args and isinstance(error.args[0], int) else None
        )


def _quoted_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError("MYSQL_DATABASE_IDENTIFIER_INVALID")
    return f"`{value}`"


def create_database_if_missing(connection: Any, settings: EnvSettings) -> dict[str, object]:
    cursor = connection.cursor()
    cursor.execute("SHOW DATABASES LIKE %s", (settings.mysql_database,))
    existed_before = cursor.fetchone() is not None
    if not existed_before:
        identifier = _quoted_identifier(settings.mysql_database)
        cursor.execute(
            f"CREATE DATABASE {identifier} CHARACTER SET utf8mb4 "  # noqa: S608
            "COLLATE utf8mb4_0900_ai_ci"
        )
        connection.commit()
    return {
        "database_existed_before": existed_before,
        "database_created": not existed_before,
        "create_statement_count": 0 if existed_before else 1,
    }


def validate_mysql_database(connection: Any, settings: EnvSettings) -> dict[str, object]:
    cursor = connection.cursor()
    placeholders = ",".join("%s" for _ in PROJECT_TABLES)
    cursor.execute(
        f"""
        SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({placeholders})
        """,  # noqa: S608
        (settings.mysql_database, *sorted(PROJECT_TABLES)),
    )
    table_rows = cursor.fetchall()
    cursor.execute(
        f"""
        SELECT TABLE_NAME, INDEX_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({placeholders})
        """,  # noqa: S608
        (settings.mysql_database, *sorted(PROJECT_TABLES)),
    )
    index_rows = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM schema_migrations")
    migration_record_count = int(cursor.fetchone()[0])
    rollback_marker = "__ragkb_g2_rollback_probe__"
    connection.begin()
    cursor.execute(
        """
        INSERT INTO schema_migrations(migration_id, revision, applied_at)
        VALUES (%s, %s, NOW(6))
        """,
        (rollback_marker, "rollback-probe"),
    )
    connection.rollback()
    cursor.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = %s",
        (rollback_marker,),
    )
    rollback_remaining = int(cursor.fetchone()[0])
    if rollback_remaining:
        cursor.execute("DELETE FROM schema_migrations WHERE migration_id = %s", (rollback_marker,))
        connection.commit()
    engines = {str(row[1]) for row in table_rows}
    collations = {str(row[2]) for row in table_rows}
    return {
        "project_table_count": len(table_rows),
        "expected_table_count": len(PROJECT_TABLES),
        "index_count": len(index_rows),
        "migration_record_count": migration_record_count,
        "all_tables_present": {str(row[0]) for row in table_rows} == PROJECT_TABLES,
        "all_innodb": engines == {"InnoDB"},
        "all_utf8mb4": bool(collations)
        and all(collation.casefold().startswith("utf8mb4") for collation in collations),
        "transaction_rollback_passed": rollback_remaining == 0,
        "rollback_probe_cleanup_needed": rollback_remaining != 0,
    }


def provision_mysql_control_plane(
    settings: EnvSettings,
    *,
    approval: str,
    connection_factory: Callable[..., Any] | None = None,
) -> dict[str, object]:
    if approval != MYSQL_APPROVAL:
        raise PermissionError("MYSQL_DATABASE_CREATE_AND_MIGRATE_APPROVAL_REQUIRED")
    adapter = (
        MySQLControlPlaneAdapter(settings, connection_factory=connection_factory)
        if connection_factory is not None
        else MySQLControlPlaneAdapter(settings)
    )
    try:
        server_connection = adapter.connect_server()
    except Exception as error:
        raise MySQLProvisionError("server_connect", error) from error
    try:
        try:
            creation = create_database_if_missing(server_connection, settings)
        except Exception as error:
            raise MySQLProvisionError("create_database", error) from error
    finally:
        server_connection.close()
    try:
        database_connection = adapter.connect()
    except Exception as error:
        raise MySQLProvisionError("database_connect", error) from error
    try:
        try:
            first = apply_mysql_migrations(database_connection)
        except Exception as error:
            raise MySQLProvisionError("first_migration", error) from error
        try:
            second = apply_mysql_migrations(database_connection)
        except Exception as error:
            raise MySQLProvisionError("idempotency_migration", error) from error
        try:
            validation = validate_mysql_database(database_connection, settings)
        except Exception as error:
            raise MySQLProvisionError("validate_database", error) from error
    finally:
        database_connection.close()
    planned_migration_count = len((*MYSQL_MIGRATIONS, *MYSQL_G3_MIGRATIONS))
    if not (
        second["applied_count"] == 0
        and second["skipped_count"] == planned_migration_count
        and validation["all_tables_present"]
        and validation["all_innodb"]
        and validation["all_utf8mb4"]
        and validation["transaction_rollback_passed"]
    ):
        raise MySQLProvisionError("validate_database", RuntimeError("MYSQL_G2_VALIDATION_FAILED"))
    return {
        "status": "MYSQL_G2_CREATE_MIGRATE_VALIDATE_PASSED",
        "creation": creation,
        "first_migration": first,
        "second_migration": second,
        "validation": validation,
        "planned_migration_count": planned_migration_count,
        "drop_statement_count": 0,
        "other_database_modified": False,
        "database_name_in_output": False,
        "host_in_output": False,
        "username_in_output": False,
        "password_in_output": False,
    }
