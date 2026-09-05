"""Transactional SQLite lifecycle, tombstone, cleanup outbox, audit and idempotency store."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ragkb.application.lifecycle import InMemoryLifecycleStore
from ragkb.domain.lifecycle import (
    AuditEvent,
    CleanupStatus,
    DeletionTombstone,
    LifecycleRecord,
    LifecycleState,
    SecurityTransition,
    SecurityTransitionStatus,
)
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLiteLifecycleStore(InMemoryLifecycleStore):
    revision = "sqlite-lifecycle-store:g3-v1"

    def __init__(self, database: SQLiteDatabase) -> None:
        super().__init__()
        self.database = database
        self.database.initialize()
        self.idempotency: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        self._load()
        self._committed_state = self.snapshot_state()

    def _load(self) -> None:
        with self.database.connect() as connection:
            for row in connection.execute("SELECT * FROM lifecycle_records").fetchall():
                self.documents[str(row["document_id"])] = LifecycleRecord(
                    document_id=str(row["document_id"]),
                    active_version_id=(
                        str(row["active_version_id"])
                        if row["active_version_id"] is not None
                        else None
                    ),
                    version_history=list(json.loads(str(row["version_history_json"]))),
                    lifecycle_state=LifecycleState(str(row["lifecycle_state"])),
                    acl_revision=int(row["acl_revision"]),
                    visible=bool(row["visible"]),
                    tombstoned=bool(row["tombstoned"]),
                    row_version=int(row["row_version"]),
                )
            for row in connection.execute("SELECT * FROM security_transitions").fetchall():
                self.transitions[str(row["transition_id"])] = SecurityTransition(
                    transition_id=str(row["transition_id"]),
                    document_id=str(row["document_id"]),
                    target_acl_revision=int(row["target_acl_revision"]),
                    required_watermark=int(row["required_watermark"]),
                    status=SecurityTransitionStatus(str(row["status"])),
                    observed_watermark=int(row["observed_watermark"]),
                    error_code=str(row["error_code"]) if row["error_code"] is not None else None,
                )
            for row in connection.execute("SELECT * FROM deletion_tombstones").fetchall():
                cleanup = json.loads(str(row["cleanup_json"]))
                self.tombstones[str(row["document_id"])] = DeletionTombstone(
                    str(row["document_id"]),
                    {key: CleanupStatus(value) for key, value in cleanup.items()},
                )
            for row in connection.execute(
                "SELECT * FROM audit_events ORDER BY sequence"
            ).fetchall():
                self.audit_events.append(
                    AuditEvent(
                        sequence=int(row["sequence"]),
                        action=str(row["action"]),
                        resource_id=str(row["resource_id"]),
                        trace_id=str(row["trace_id"]),
                        governance_revision=str(row["governance_revision"]),
                        previous_hash=str(row["previous_hash"]),
                        event_hash=str(row["event_hash"]),
                    )
                )
            for row in connection.execute("SELECT * FROM lifecycle_idempotency").fetchall():
                key = (
                    str(row["tenant_id"]),
                    str(row["operation"]),
                    str(row["idempotency_key"]),
                )
                self.idempotency[key] = (
                    str(row["request_hash"]),
                    json.loads(str(row["response_json"])),
                )

    def reload(self) -> None:
        with self.lock:
            refreshed = SQLiteLifecycleStore(self.database)
            self.restore_state(refreshed.snapshot_state())
            self._committed_state = self.snapshot_state()

    @staticmethod
    def _sync_local_fact_source(
        connection: sqlite3.Connection,
        operation: str | None,
        documents: dict[str, LifecycleRecord],
        expected_document_row_version: int | None,
        generation_id: str | None,
    ) -> None:
        if operation is None or ":" not in operation:
            return
        command, document_id = operation.split(":", 1)
        if command not in {"publish", "rollback", "revoke", "delete"}:
            return
        fact = connection.execute(
            "SELECT id FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if fact is None:
            return
        record = documents[document_id]
        now = time.time()
        if command in {"publish", "rollback"}:
            if record.active_version_id is None:
                raise ValueError("active version is required for publication")
            target = connection.execute(
                """
                SELECT id FROM document_versions
                WHERE id = ? AND document_id = ?
                """,
                (record.active_version_id, document_id),
            ).fetchone()
            if target is None:
                raise ValueError("publication target is not a document version")
            if expected_document_row_version is not None:
                switching = connection.execute(
                    """
                    UPDATE documents SET state = 'SWITCHING', updated_at = ?
                    WHERE id = ? AND row_version = ?
                    """,
                    (now, document_id, expected_document_row_version),
                )
                if switching.rowcount != 1:
                    raise ValueError("PUBLICATION_DOCUMENT_CAS_FAILED")
            candidate_state = "RETIRED" if command == "rollback" else "STAGED"
            candidate = connection.execute(
                """
                UPDATE publication_candidates SET projection_state = 'ACTIVE', updated_at = ?
                WHERE version_id = ? AND document_id = ? AND generation_id = ?
                    AND projection_state = ?
                """,
                (
                    now,
                    record.active_version_id,
                    document_id,
                    generation_id,
                    candidate_state,
                ),
            )
            if generation_id is not None and candidate.rowcount != 1:
                raise ValueError("PUBLICATION_PROJECTION_SWAP_FAILED")
            connection.execute(
                """
                UPDATE publication_candidates SET projection_state = 'RETIRED', updated_at = ?
                WHERE document_id = ? AND version_id != ? AND projection_state = 'ACTIVE'
                """,
                (now, document_id, record.active_version_id),
            )
            connection.execute(
                """
                UPDATE document_versions
                SET publication_state = CASE
                    WHEN id = ? THEN 'SERVING'
                    WHEN publication_state = 'SERVING' THEN 'SUPERSEDED'
                    ELSE publication_state
                END
                WHERE document_id = ?
                """,
                (record.active_version_id, document_id),
            )
            updated = connection.execute(
                """
                UPDATE documents SET current_version_id = ?, state = 'ACTIVE',
                    row_version = row_version + 1, updated_at = ?
                WHERE id = ? AND (? IS NULL OR row_version = ?)
                """,
                (
                    record.active_version_id,
                    now,
                    document_id,
                    expected_document_row_version,
                    expected_document_row_version,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("PUBLICATION_DOCUMENT_CAS_FAILED")
        elif command == "revoke":
            connection.execute(
                """
                UPDATE documents SET state = 'REVOKED', row_version = row_version + 1,
                    updated_at = ? WHERE id = ?
                """,
                (now, document_id),
            )
        else:
            connection.execute(
                """
                UPDATE documents SET state = 'DELETED', current_version_id = NULL,
                    row_version = row_version + 1, updated_at = ? WHERE id = ?
                """,
                (now, document_id),
            )
            connection.execute(
                """
                UPDATE document_versions SET publication_state = 'RETIRED'
                WHERE document_id = ? AND publication_state != 'RETIRED'
                """,
                (document_id,),
            )

    def get_idempotency(
        self, tenant_id: str, operation: str, key: str
    ) -> tuple[str, dict[str, Any]] | None:
        return self.idempotency.get((tenant_id, operation, key))

    def persist_state(
        self,
        *,
        tenant_id: str = "local",
        operation: str | None = None,
        key: str | None = None,
        request_hash: str | None = None,
        response: dict[str, Any] | None = None,
        expected_document_row_version: int | None = None,
        generation_id: str | None = None,
    ) -> None:
        idempotency_entry: tuple[tuple[str, str, str], tuple[str, dict[str, Any]]] | None = None
        try:
            with self.database.transaction(immediate=True) as connection:
                for record in self.documents.values():
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO lifecycle_records(
                            document_id, tenant_id, active_version_id, version_history_json,
                            lifecycle_state, acl_revision, visible, tombstoned, row_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.document_id,
                            tenant_id,
                            record.active_version_id,
                            json.dumps(record.version_history, sort_keys=True),
                            record.lifecycle_state.value,
                            record.acl_revision,
                            int(record.visible),
                            int(record.tombstoned),
                            record.row_version,
                        ),
                    )
                for transition in self.transitions.values():
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO security_transitions(
                            transition_id, document_id, target_acl_revision,
                            required_watermark, observed_watermark, status, error_code
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transition.transition_id,
                            transition.document_id,
                            transition.target_acl_revision,
                            transition.required_watermark,
                            transition.observed_watermark,
                            transition.status.value,
                            transition.error_code,
                        ),
                    )
                for tombstone in self.tombstones.values():
                    cleanup_json = json.dumps(
                        {key: value.value for key, value in tombstone.cleanup.items()},
                        sort_keys=True,
                    )
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO deletion_tombstones(
                            document_id, cleanup_json, created_at
                        ) VALUES (?, ?, ?)
                        """,
                        (tombstone.document_id, cleanup_json, time.time()),
                    )
                    for target, state in tombstone.cleanup.items():
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO cleanup_outbox(
                                document_id, target_store, state, updated_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (tombstone.document_id, target, state.value, time.time()),
                        )
                for event in self.audit_events:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO audit_events(
                            sequence, action, resource_id, trace_id, governance_revision,
                            previous_hash, event_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.sequence,
                            event.action,
                            event.resource_id,
                            event.trace_id,
                            event.governance_revision,
                            event.previous_hash,
                            event.event_hash,
                        ),
                    )
                self._sync_local_fact_source(
                    connection,
                    operation,
                    self.documents,
                    expected_document_row_version,
                    generation_id,
                )
                if operation and key and request_hash and response is not None:
                    connection.execute(
                        """
                        INSERT INTO lifecycle_idempotency(
                            tenant_id, operation, idempotency_key, request_hash,
                            response_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            operation,
                            key,
                            request_hash,
                            json.dumps(response, sort_keys=True),
                            time.time(),
                        ),
                    )
                    idempotency_entry = (
                        (tenant_id, operation, key),
                        (request_hash, dict(response)),
                    )
        except Exception:
            self.restore_state(self._committed_state)
            raise
        if idempotency_entry is not None:
            self.idempotency[idempotency_entry[0]] = idempotency_entry[1]
        self._committed_state = self.snapshot_state()

    def is_tombstoned(self, document_id: str) -> bool:
        return document_id in self.tombstones or (
            document_id in self.documents and self.documents[document_id].tombstoned
        )

    def is_accessible(self, document_id: str) -> bool:
        record = self.documents.get(document_id)
        return record is not None and (
            record.lifecycle_state is LifecycleState.ACTIVE
            and record.visible
            and not record.tombstoned
            and record.active_version_id is not None
        )
