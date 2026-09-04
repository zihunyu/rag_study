"""Row-level MySQL persistence for production aggregate entities.

The legacy v2 repositories stored every entity for a tenant in one JSON row.  This
store keeps one independently versioned row per domain entity and writes only the
rows changed by a transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ragkb.domain.uploads import OptimisticConcurrencyError


@dataclass(frozen=True)
class EntityRow:
    logical_key: str
    parent_id: str | None
    ordinal: int
    payload: dict[str, Any]
    revision: int = 0

    def canonical_payload(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


EntityMap = dict[tuple[str, str], EntityRow]


class MySQLNormalizedEntityStore:
    """Optimistically synchronize independently addressable aggregate rows."""

    _ALLOWED_TABLES = frozenset(
        {"upload_entities_v3", "lifecycle_entities_v3", "governance_entities_v3"}
    )

    def __init__(self, table: str, tenant_id: str) -> None:
        if table not in self._ALLOWED_TABLES:
            raise ValueError("MYSQL_ENTITY_TABLE_NOT_ALLOWED")
        self.table = table
        self.tenant_id = tenant_id

    @staticmethod
    def _mapping(cursor: Any, row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        names = [item[0] for item in cursor.description]
        return dict(zip(names, row, strict=True))

    def load(self, cursor: Any) -> EntityMap:
        cursor.execute(
            f"""
            SELECT entity_type, entity_id, logical_key, parent_id, ordinal,
                   payload_json, entity_revision
            FROM {self.table} WHERE tenant_id=%s
            ORDER BY entity_type, parent_id, ordinal, entity_id
            """,  # noqa: S608 - table is selected from a closed internal allowlist
            (self.tenant_id,),
        )
        entities: EntityMap = {}
        for raw in cursor.fetchall():
            row = self._mapping(cursor, raw)
            payload = row["payload_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise ValueError("MYSQL_NORMALIZED_ENTITY_PAYLOAD_INVALID")
            identity = (str(row["entity_type"]), str(row["entity_id"]))
            entities[identity] = EntityRow(
                logical_key=str(row["logical_key"]),
                parent_id=str(row["parent_id"]) if row["parent_id"] is not None else None,
                ordinal=int(row["ordinal"]),
                payload=payload,
                revision=int(row["entity_revision"]),
            )
        return entities

    def sync(self, cursor: Any, before: EntityMap, after: EntityMap) -> None:
        for identity in sorted(before.keys() - after.keys()):
            entity_type, entity_id = identity
            removed = before[identity]
            cursor.execute(
                f"""
                DELETE FROM {self.table}
                WHERE tenant_id=%s AND entity_type=%s AND entity_id=%s
                  AND entity_revision=%s
                """,  # noqa: S608
                (self.tenant_id, entity_type, entity_id, removed.revision),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError(entity_id)

        for identity in sorted(after):
            entity_type, entity_id = identity
            current = after[identity]
            existing = before.get(identity)
            if existing is not None and (
                existing.logical_key == current.logical_key
                and existing.parent_id == current.parent_id
                and existing.ordinal == current.ordinal
                and existing.canonical_payload() == current.canonical_payload()
            ):
                continue
            payload = current.canonical_payload()
            if existing is None:
                cursor.execute(
                    f"""
                    INSERT INTO {self.table}(
                        tenant_id, entity_type, entity_id, logical_key, parent_id,
                        ordinal, payload_json, entity_revision, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, NOW(6), NOW(6))
                    """,  # noqa: S608
                    (
                        self.tenant_id,
                        entity_type,
                        entity_id,
                        current.logical_key,
                        current.parent_id,
                        current.ordinal,
                        payload,
                    ),
                )
                continue
            cursor.execute(
                f"""
                UPDATE {self.table}
                SET logical_key=%s, parent_id=%s, ordinal=%s, payload_json=%s,
                    entity_revision=entity_revision+1, updated_at=NOW(6)
                WHERE tenant_id=%s AND entity_type=%s AND entity_id=%s
                  AND entity_revision=%s
                """,  # noqa: S608
                (
                    current.logical_key,
                    current.parent_id,
                    current.ordinal,
                    payload,
                    self.tenant_id,
                    entity_type,
                    entity_id,
                    existing.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError(entity_id)
