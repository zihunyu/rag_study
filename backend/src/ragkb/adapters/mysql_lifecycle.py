"""MySQL-authoritative lifecycle state for the single-instance production profile."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
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


class MySQLLifecycleStore(InMemoryLifecycleStore):
    revision = "mysql-lifecycle-state:g4-v2"

    def __init__(self, control: MySQLControlPlaneAdapter, tenant_id: str) -> None:
        super().__init__()
        self.control = control
        self.tenant_id = tenant_id
        self._committed_state = self.snapshot_state()

    def _serialized(self) -> dict[str, object]:
        return {
            "documents": [
                {
                    **asdict(record),
                    "lifecycle_state": record.lifecycle_state.value,
                }
                for record in self.documents.values()
            ],
            "transitions": [
                {**asdict(item), "status": item.status.value} for item in self.transitions.values()
            ],
            "tombstones": [
                {
                    "document_id": item.document_id,
                    "cleanup": {key: value.value for key, value in item.cleanup.items()},
                }
                for item in self.tombstones.values()
            ],
            "audit_events": [asdict(item) for item in self.audit_events],
            "processed_events": sorted(self.processed_events),
            "idempotency": [
                {
                    "tenant_id": key[0],
                    "operation": key[1],
                    "key": key[2],
                    "request_hash": value[0],
                    "response": value[1],
                }
                for key, value in self.idempotency.items()
            ],
        }

    def reload(self) -> None:
        self.documents.clear()
        self.transitions.clear()
        self.tombstones.clear()
        self.audit_events.clear()
        self.processed_events.clear()
        self.idempotency.clear()
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT state_json FROM lifecycle_state_v2 WHERE tenant_id=%s",
                (self.tenant_id,),
            )
            row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return
        value = row["state_json"] if isinstance(row, dict) else row[0]
        loaded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(loaded, dict):
            raise ValueError("MYSQL_LIFECYCLE_STATE_INVALID")
        self.documents = {
            str(item["document_id"]): LifecycleRecord(
                document_id=str(item["document_id"]),
                active_version_id=(
                    str(item["active_version_id"]) if item.get("active_version_id") else None
                ),
                version_history=list(map(str, item.get("version_history", []))),
                lifecycle_state=LifecycleState(str(item["lifecycle_state"])),
                acl_revision=int(item["acl_revision"]),
                visible=bool(item["visible"]),
                tombstoned=bool(item["tombstoned"]),
                row_version=int(item["row_version"]),
            )
            for item in loaded.get("documents", [])
        }
        self.transitions = {
            str(item["transition_id"]): SecurityTransition(
                transition_id=str(item["transition_id"]),
                document_id=str(item["document_id"]),
                target_acl_revision=int(item["target_acl_revision"]),
                required_watermark=int(item["required_watermark"]),
                status=SecurityTransitionStatus(str(item["status"])),
                observed_watermark=int(item["observed_watermark"]),
                error_code=str(item["error_code"]) if item.get("error_code") else None,
            )
            for item in loaded.get("transitions", [])
        }
        self.tombstones = {
            str(item["document_id"]): DeletionTombstone(
                str(item["document_id"]),
                {
                    str(key): CleanupStatus(str(state))
                    for key, state in dict(item.get("cleanup", {})).items()
                },
            )
            for item in loaded.get("tombstones", [])
        }
        self.audit_events = [AuditEvent(**item) for item in loaded.get("audit_events", [])]
        self.processed_events = set(map(str, loaded.get("processed_events", [])))
        self.idempotency = {
            (str(item["tenant_id"]), str(item["operation"]), str(item["key"])): (
                str(item["request_hash"]),
                dict(item["response"]),
            )
            for item in loaded.get("idempotency", [])
        }

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
        del expected_document_row_version, generation_id
        if tenant_id != self.tenant_id:
            raise ValueError("MYSQL_LIFECYCLE_TENANT_MISMATCH")
        if operation and key and request_hash and response is not None:
            self.idempotency[(tenant_id, operation, key)] = (request_hash, dict(response))
        payload = json.dumps(self._serialized(), ensure_ascii=False, sort_keys=True)
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO lifecycle_state_v2(tenant_id, state_json, updated_at)
                VALUES (%s, %s, NOW(6)) AS incoming
                ON DUPLICATE KEY UPDATE state_json=incoming.state_json, updated_at=NOW(6)
                """,
                (tenant_id, payload),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            self.restore_state(self._committed_state)
            raise
        finally:
            connection.close()
        self._committed_state = self.snapshot_state()
