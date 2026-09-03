"""Persistent local opaque citation reference and development signing-secret store."""

from __future__ import annotations

import time
from typing import Any

from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLiteReferenceStore:
    revision = "sqlite-reference-store:g3-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()

    def save(self, opaque_id: str, record: dict[str, Any]) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO reference_tokens(
                    opaque_id, token_kind, tenant_id, user_id, run_id,
                    evidence_id, document_id, expires_at, revoked, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
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
                    time.time(),
                ),
            )

    def get(self, opaque_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reference_tokens WHERE opaque_id = ?", (opaque_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def revoke_document(self, document_id: str) -> int:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE reference_tokens SET revoked = 1 WHERE document_id = ?",
                (document_id,),
            )
            return int(cursor.rowcount)
