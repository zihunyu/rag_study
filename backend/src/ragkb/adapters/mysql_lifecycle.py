"""MySQL-authoritative lifecycle state for the single-instance production profile."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.mysql_entity_store import EntityMap, EntityRow, MySQLNormalizedEntityStore
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
    revision = "mysql-lifecycle-normalized-outbox:g4-v3"
    durable_publication_intents = True

    def __init__(self, control: MySQLControlPlaneAdapter, tenant_id: str) -> None:
        super().__init__()
        self.control = control
        self.tenant_id = tenant_id
        self._entities = MySQLNormalizedEntityStore("lifecycle_entities_v3", tenant_id)
        self._committed_state = self.snapshot_state()

    @staticmethod
    def _hashed_id(kind: str, value: str) -> str:
        return hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()

    def _serialized(self) -> dict[str, Any]:
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

    @classmethod
    def _to_entities(cls, state: dict[str, Any]) -> EntityMap:
        entities: EntityMap = {}
        for collection, id_field in (
            ("documents", "document_id"),
            ("transitions", "transition_id"),
            ("tombstones", "document_id"),
        ):
            for value in state[collection]:
                entity_id = str(value[id_field])
                entities[(collection, entity_id)] = EntityRow(
                    entity_id,
                    str(value.get("document_id")) if collection == "transitions" else None,
                    0,
                    deepcopy(value),
                )
        for ordinal, value in enumerate(state["audit_events"]):
            entity_id = str(value["sequence"])
            entities[("audit_events", entity_id)] = EntityRow(
                entity_id, str(value["resource_id"]), ordinal, deepcopy(value)
            )
        for event_id in state["processed_events"]:
            entities[("processed_events", str(event_id))] = EntityRow(
                str(event_id), None, 0, {"event_id": str(event_id)}
            )
        for value in state["idempotency"]:
            logical_key = json.dumps(
                [value["tenant_id"], value["operation"], value["key"]], separators=(",", ":")
            )
            entity_id = cls._hashed_id("idempotency", logical_key)
            entities[("idempotency", entity_id)] = EntityRow(logical_key, None, 0, deepcopy(value))
        return entities

    @staticmethod
    def _from_entities(entities: EntityMap) -> dict[str, Any]:
        state: dict[str, Any] = {
            "documents": [],
            "transitions": [],
            "tombstones": [],
            "audit_events": [],
            "processed_events": [],
            "idempotency": [],
        }
        for (collection, _), row in entities.items():
            if collection == "processed_events":
                state[collection].append(row.logical_key)
            else:
                state[collection].append((row.ordinal, deepcopy(row.payload)))
        for collection in ("documents", "transitions", "tombstones", "idempotency"):
            state[collection] = [item for _, item in state[collection]]
        state["audit_events"] = [
            item for _, item in sorted(state["audit_events"], key=lambda pair: pair[0])
        ]
        state["processed_events"] = sorted(state["processed_events"])
        return state

    def _apply_loaded(self, loaded: dict[str, Any]) -> None:
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
                    str(key): CleanupStatus(str(status))
                    for key, status in dict(item.get("cleanup", {})).items()
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
            entities = self._entities.load(cursor)
            if entities:
                loaded = self._from_entities(entities)
            else:
                cursor.execute(
                    "SELECT state_json FROM lifecycle_state_v2 WHERE tenant_id=%s",
                    (self.tenant_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    self._committed_state = self.snapshot_state()
                    return
                value = row["state_json"] if isinstance(row, dict) else row[0]
                loaded = json.loads(value) if isinstance(value, str) else value
        finally:
            connection.close()
        if not isinstance(loaded, dict):
            raise ValueError("MYSQL_LIFECYCLE_STATE_INVALID")
        self._apply_loaded(loaded)
        self._committed_state = self.snapshot_state()

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        loaded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(loaded, dict):
            raise ValueError("MYSQL_UPLOAD_ENTITY_PAYLOAD_INVALID")
        return loaded

    def _commit_upload_current(
        self,
        cursor: Any,
        document_id: str,
        version_id: str,
        expected_document_row_version: int | None,
    ) -> None:
        cursor.execute(
            """
            SELECT entity_id, payload_json, entity_revision
            FROM upload_entities_v3
            WHERE tenant_id=%s AND entity_type='documents' AND entity_id=%s FOR UPDATE
            """,
            (self.tenant_id, document_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError("MYSQL_UPLOAD_DOCUMENT_NOT_NORMALIZED")
        data = (
            row
            if isinstance(row, dict)
            else {"entity_id": row[0], "payload_json": row[1], "entity_revision": row[2]}
        )
        document = self._json_object(data["payload_json"])
        if expected_document_row_version is not None and int(document["row_version"]) != int(
            expected_document_row_version
        ):
            raise ValueError("PUBLICATION_DOCUMENT_ROW_VERSION_CHANGED")
        previous = document.get("current_version_id")
        document["current_version_id"] = version_id
        if previous != version_id:
            document["row_version"] = int(document["row_version"]) + 1
        cursor.execute(
            """
            UPDATE upload_entities_v3
            SET payload_json=%s, entity_revision=entity_revision+1, updated_at=NOW(6)
            WHERE tenant_id=%s AND entity_type='documents' AND entity_id=%s
              AND entity_revision=%s
            """,
            (
                json.dumps(document, ensure_ascii=False, sort_keys=True),
                self.tenant_id,
                document_id,
                int(data["entity_revision"]),
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("PUBLICATION_DOCUMENT_CONCURRENT_UPDATE")
        cursor.execute(
            """
            SELECT entity_id, payload_json, entity_revision
            FROM upload_entities_v3
            WHERE tenant_id=%s AND entity_type='versions' AND parent_id=%s FOR UPDATE
            """,
            (self.tenant_id, document_id),
        )
        for raw in cursor.fetchall():
            item = (
                raw
                if isinstance(raw, dict)
                else {"entity_id": raw[0], "payload_json": raw[1], "entity_revision": raw[2]}
            )
            version = self._json_object(item["payload_json"])
            target_state = "SERVING" if str(item["entity_id"]) == version_id else "SUPERSEDED"
            if version.get("publication_state") == target_state:
                continue
            version["publication_state"] = target_state
            cursor.execute(
                """
                UPDATE upload_entities_v3
                SET payload_json=%s, entity_revision=entity_revision+1, updated_at=NOW(6)
                WHERE tenant_id=%s AND entity_type='versions' AND entity_id=%s
                  AND entity_revision=%s
                """,
                (
                    json.dumps(version, ensure_ascii=False, sort_keys=True),
                    self.tenant_id,
                    str(item["entity_id"]),
                    int(item["entity_revision"]),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("PUBLICATION_VERSION_CONCURRENT_UPDATE")

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
        if tenant_id != self.tenant_id:
            raise ValueError("MYSQL_LIFECYCLE_TENANT_MISMATCH")
        if operation and key and request_hash and response is not None:
            self.idempotency[(tenant_id, operation, key)] = (request_hash, dict(response))
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            before = self._entities.load(cursor)
            self._entities.sync(cursor, before, self._to_entities(self._serialized()))
            cursor.execute("DELETE FROM lifecycle_state_v2 WHERE tenant_id=%s", (tenant_id,))
            base_operation = operation.removesuffix(":intent") if operation else None
            if operation and operation.endswith(":intent") and key and response is not None:
                cursor.execute(
                    """
                    INSERT INTO publication_outbox_v3(
                        tenant_id, operation, idempotency_key, document_id,
                        target_version_id, generation_id, state, attempt_count,
                        payload_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', 1, %s, NOW(6), NOW(6))
                    AS incoming ON DUPLICATE KEY UPDATE
                        state='PENDING', attempt_count=attempt_count+1,
                        payload_json=incoming.payload_json, updated_at=NOW(6)
                    """,
                    (
                        tenant_id,
                        base_operation,
                        key,
                        str(response["document_id"]),
                        str(response["active_version_id"]),
                        generation_id or "",
                        json.dumps(response, ensure_ascii=False, sort_keys=True),
                    ),
                )
            elif (
                operation
                and operation.split(":", 1)[0] in {"publish", "rollback"}
                and key
                and response is not None
                and base_operation == operation
            ):
                self._commit_upload_current(
                    cursor,
                    str(response["document_id"]),
                    str(response["active_version_id"]),
                    expected_document_row_version,
                )
                cursor.execute(
                    """
                    UPDATE publication_outbox_v3
                    SET state='APPLIED', error_code=NULL, updated_at=NOW(6)
                    WHERE tenant_id=%s AND operation=%s AND idempotency_key=%s
                      AND state IN ('PENDING', 'FAILED_RETRYABLE')
                    """,
                    (tenant_id, operation, key),
                )
                if cursor.rowcount != 1:
                    raise ValueError("PUBLICATION_OUTBOX_NOT_PENDING")
            connection.commit()
        except Exception:
            connection.rollback()
            self.restore_state(self._committed_state)
            raise
        finally:
            connection.close()
        self._committed_state = self.snapshot_state()

    def mark_publication_projection_failed(self, operation: str, key: str, error_code: str) -> None:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE publication_outbox_v3
                SET state='FAILED_RETRYABLE', error_code=%s, updated_at=NOW(6)
                WHERE tenant_id=%s AND operation=%s AND idempotency_key=%s
                  AND state='PENDING'
                """,
                (error_code, self.tenant_id, operation, key),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
