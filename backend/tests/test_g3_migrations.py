from __future__ import annotations

from ragkb.infrastructure.mysql_migrations import MYSQL_G3_MIGRATIONS, g3_migration_plan


def test_g3_mysql_plan_covers_qa_lifecycle_tombstone_and_append_only_audit_without_execution() -> (
    None
):
    plan = g3_migration_plan()
    sql = "\n".join(statement for _, statement in MYSQL_G3_MIGRATIONS)

    assert plan["statement_count"] == len(MYSQL_G3_MIGRATIONS) == 21
    assert plan["mutating_execution_performed"] is False
    assert plan["real_database_execution_approved"] is False
    for table in (
        "rag_runs",
        "rag_evidence",
        "user_feedback",
        "security_transitions",
        "deletion_tombstones",
        "audit_events",
        "lifecycle_idempotency",
        "cleanup_outbox",
        "reference_tokens",
        "upload_entities_v3",
        "lifecycle_entities_v3",
        "governance_entities_v3",
        "publication_outbox_v3",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "previous_hash" in sql and "event_hash" in sql
    assert "ENGINE=InnoDB" in sql and "CHARSET=utf8mb4" in sql
    assert "DROP " not in sql.upper()
