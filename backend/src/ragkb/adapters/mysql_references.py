"""MySQL store for subject-bound opaque citation references."""

from __future__ import annotations

from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter


class MySQLReferenceStore:
    revision = "mysql-reference-store:g4-v2"

    def __init__(self, control: MySQLControlPlaneAdapter) -> None:
        self.control = control

    def save(self, opaque_id: str, record: dict[str, Any]) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO reference_tokens_v2(
                    opaque_id, token_kind, tenant_id, user_id, run_id,
                    evidence_id, document_id, expires_at, revoked, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, NOW(6))
                """,
                (
                    opaque_id,
                    record["kind"],
                    record["tenant_id"],
                    record["user_id"],
                    record["run_id"],
                    record.get("evidence_id"),
                    record.get("document_id"),
                    record["expires_at"],
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, opaque_id: str) -> dict[str, Any] | None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM reference_tokens_v2 WHERE opaque_id=%s", (opaque_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            if isinstance(row, dict):
                return dict(row)
            columns = tuple(item[0] for item in cursor.description)
            return dict(zip(columns, row, strict=True))
        finally:
            connection.close()

    def revoke_document(self, document_id: str) -> int:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE reference_tokens_v2 SET revoked=TRUE WHERE document_id=%s",
                (document_id,),
            )
            connection.commit()
            return int(cursor.rowcount)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
