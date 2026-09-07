"""Evidence-only recovery of historical publication state; never publishes new content."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ragkb.infrastructure.workspace_db import WorkspaceDB


def recovery_decision(evidence: dict[str, Any]) -> tuple[str, list[str], dict[str, Any] | None]:
    """Pure decision boundary, including the non-resurrection rule."""
    record, intent = evidence.get("record"), evidence.get("intent")
    if not intent:
        return "not_candidate", ["没有已应用的发布快照，需按正常流程处理。"], None
    if evidence.get("later_lifecycle_operation") or evidence.get("tombstone"):
        return "blocked", ["存在后续生命周期操作或删除标记，禁止恢复。"], None
    if record and (
        record.get("tombstoned")
        or record.get("lifecycle_state") in ("REVOKED", "DELETED", "SECURITY_TRANSITION")
    ):
        return "blocked", ["当前文档已撤回、删除或正在更新权限，禁止覆盖。"], None
    snapshot = dict(intent["snapshot"])
    if snapshot.get("lifecycle_state") not in ("SWITCHING", "ACTIVE") or snapshot.get("tombstoned"):
        return "blocked", ["发布快照不是可恢复的发布阶段。"], None
    checks = evidence.get("checks", {})
    if not checks:
        return "blocked", ["缺少复核、版本与索引核验结果，禁止恢复。"], None
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return "blocked", failed, None
    recovered = {**snapshot, "lifecycle_state": "ACTIVE", "visible": True, "tombstoned": False}
    if record and all(record.get(key) == recovered.get(key) for key in recovered):
        return "already_consistent", [], None
    if record and not (
        record.get("lifecycle_state") == "DRAFT"
        and record.get("row_version") == 1
        and not record.get("visible")
        and not record.get("tombstoned")
        and evidence.get("startup_recovery_only")
        and not record.get("version_history")
        and record.get("active_version_id") == snapshot.get("active_version_id")
    ):
        return "blocked", ["当前状态不是可证明由启动恢复创建的初始草稿。"], None
    return "repairable", [], recovered


class PublicationRepair:
    def __init__(
        self,
        db: WorkspaceDB,
        tenant: str,
        vector_probe: Callable[[str, str, str], list[dict[str, Any]]],
    ) -> None:
        if not db.mysql:
            raise ValueError("PUBLICATION_REPAIR_REQUIRES_MYSQL")
        self.db, self.tenant, self.vector_probe = db, tenant, vector_probe

    def _read(self, connection: Any, table: str, *, lock: bool = False) -> list[dict[str, Any]]:
        if table not in {
            "upload_entities",
            "lifecycle_entities",
            "publication_outbox",
            "index_jobs",
            "retrieval_chunk_projections",
            "retrieval_release_state",
        }:
            raise ValueError("UNKNOWN_REPAIR_TABLE")
        return self.db.rows(
            connection,
            f"SELECT * FROM {table} WHERE tenant_id=?"  # noqa: S608 - checked table allowlist
            + (" FOR UPDATE" if lock else ""),
            (self.tenant,),
        )

    def collect(self, connection: Any, *, lock: bool = False) -> dict[str, list[dict[str, Any]]]:
        return {
            table: self._read(connection, table, lock=lock)
            for table in (
                "lifecycle_entities",
                "upload_entities",
                "publication_outbox",
                "index_jobs",
                "retrieval_chunk_projections",
                "retrieval_release_state",
            )
        }

    @staticmethod
    def decoded(row: dict[str, Any]) -> dict[str, Any]:
        value = row["payload_json"]
        return json.loads(value) if isinstance(value, str) else dict(value)

    def diagnose(self, data: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        uploads, lifecycle = data["upload_entities"], data["lifecycle_entities"]
        docs = [row for row in uploads if row["entity_type"] == "documents"]
        report = []
        for doc_row in docs:
            doc = self.decoded(doc_row)
            document_id = doc_row["entity_id"]
            versions = [
                row
                for row in uploads
                if row["entity_type"] == "versions" and row["parent_id"] == document_id
            ]
            latest = max(versions, key=lambda row: row["ordinal"]) if versions else None
            latest_version = self.decoded(latest) if latest else {}
            sessions = [
                self.decoded(row)
                for row in uploads
                if row["entity_type"] == "sessions"
                and self.decoded(row).get("document_id") == document_id
            ]
            filename = (
                sessions[-1]["filename"] if sessions else doc.get("external_key", document_id)
            )
            applied = [
                row
                for row in data["publication_outbox"]
                if row["document_id"] == document_id and row["state"] == "APPLIED"
            ]
            intent = max(applied, key=lambda row: row["updated_at"]) if applied else None
            life_rows = [
                row
                for row in lifecycle
                if row["entity_type"] == "documents" and row["entity_id"] == document_id
            ]
            record = self.decoded(life_rows[0]) if life_rows else None
            result: dict[str, Any] = {
                "document_id": document_id,
                "filename": filename,
                "processing_state": latest_version.get("processing_state"),
                "publication_state": latest_version.get("publication_state"),
                "lifecycle_state": record.get("lifecycle_state") if record else None,
                "availability": "pending_review"
                if latest_version.get("processing_state") == "VALIDATED"
                else "failed",
            }
            if not intent:
                result.update(
                    decision="not_candidate", reasons=["没有已应用的发布快照，保留现状。"]
                )
                report.append(result)
                continue
            snapshot = self.decoded(intent)
            version_id, generation = intent["target_version_id"], intent["generation_id"]
            version_rows = [row for row in versions if row["entity_id"] == version_id]
            version = self.decoded(version_rows[0]) if version_rows else {}
            reviews = [
                row
                for row in uploads
                if row["entity_type"] == "reviews" and row["parent_id"] == version_id
            ]
            review = self.decoded(max(reviews, key=lambda row: row["ordinal"])) if reviews else {}
            policy = review.get("security_projection", {})
            jobs = [
                row
                for row in data["index_jobs"]
                if row["document_version_id"] == version_id and row["generation_id"] == generation
            ]
            index_job = max(jobs, key=lambda row: row["updated_at"]) if jobs else {}
            projections = [
                row
                for row in data["retrieval_chunk_projections"]
                if row["document_version_id"] == version_id
                and row["index_generation_id"] == generation
            ]
            manifest = dict(json.loads(index_job.get("expected_manifest_json") or "[]"))
            indexed_projections = [row for row in projections if row["chunk_id"] in manifest]
            parent_ids = {
                row["parent_chunk_id"] for row in indexed_projections if row["parent_chunk_id"]
            }
            space = projections[0]["space_id"] if projections else doc.get("space_id", "")
            release = next(
                (row for row in data["retrieval_release_state"] if row["space_id"] == space), {}
            )
            events = [
                row
                for row in lifecycle
                if row["entity_type"] == "audit_events" and row["parent_id"] == document_id
            ]
            startup_only = bool(events) and all(
                self.decoded(row).get("action") == "document.registered"
                and self.decoded(row).get("trace_id") == f"recover:{document_id}"
                for row in events
            )
            later = any(
                row["updated_at"] > intent["updated_at"]
                and self.decoded(row).get("action")
                not in ("document.registered", "publication.history_recovered")
                for row in events
            )
            later |= any(
                row["document_id"] == document_id
                and row["updated_at"] > intent["updated_at"]
                and row["operation"].split(":")[0] in ("rollback", "publish")
                for row in data["publication_outbox"]
            )
            tombstone = any(
                row["entity_type"] == "tombstones" and row["entity_id"] == document_id
                for row in lifecycle
            )
            scalar_fields = (
                "document_id",
                "document_version_id",
                "index_generation_id",
                "content_checksum",
                "lifecycle_projection",
                "current_version",
                "permission_revision",
                "visibility",
                "classification_level",
                "valid_from_epoch",
                "valid_to_epoch",
            )
            try:
                vectors = self.vector_probe(document_id, version_id, generation)
                vector_error = None
            except Exception as error:
                vectors, vector_error = [], type(error).__name__
            vector_map = {row["chunk_id"]: row for row in vectors}
            vector_matches = (
                bool(indexed_projections)
                and len(indexed_projections) == len(vectors)
                and all(
                    row["chunk_id"] in vector_map
                    and all(
                        row[field] == vector_map[row["chunk_id"]].get(field)
                        for field in scalar_fields
                    )
                    and json.loads(row["acl_scope_tokens_json"])
                    == vector_map[row["chunk_id"]].get("acl_scope_tokens")
                    for row in indexed_projections
                )
            )
            checks = {
                "快照与当前版本一致": snapshot.get("active_version_id") == version_id
                and doc.get("current_version_id") == version_id
                and doc.get("state") == "ACTIVE",
                "版本已完成解析并发布": version.get("processing_state") == "VALIDATED"
                and version.get("publication_state") == "SERVING",
                "人工复核记录与解析版本一致": review.get("decision") == "APPROVED"
                and review.get("quality_revision") == version.get("parser_revision")
                and bool(policy),
                "索引任务已完成且数量一致": index_job.get("state") == "READY"
                and index_job.get("expected_count") == len(indexed_projections) == len(manifest)
                and bool(indexed_projections)
                and all(
                    row["chunk_id"] in manifest or row["chunk_id"] in parent_ids
                    for row in projections
                )
                and all(
                    row["content_checksum"] == manifest[row["chunk_id"]]
                    for row in indexed_projections
                ),
                "索引代次与权限水位一致": release.get("active_generation_id") == generation
                and release.get("security_watermark", 0) >= snapshot.get("acl_revision", 1),
                "检索投影与复核权限一致": bool(projections)
                and all(
                    row["lifecycle_projection"] == "SERVING"
                    and row["current_version"]
                    and row["permission_revision"]
                    == snapshot.get("acl_revision")
                    == policy.get("permission_revision")
                    and row["visibility"] == policy.get("visibility")
                    and row["classification_level"] == policy.get("classification_level")
                    and json.loads(row["acl_scope_tokens_json"]) == policy.get("acl_scope_tokens")
                    and row["valid_from_epoch"] <= time.time()
                    and (not row["valid_to_epoch"] or row["valid_to_epoch"] > time.time())
                    for row in projections
                ),
                "向量记录与检索投影逐项一致": vector_matches,
            }
            evidence = {
                "record": record,
                "intent": {"snapshot": snapshot},
                "checks": checks,
                "later_lifecycle_operation": later,
                "tombstone": tombstone,
                "startup_recovery_only": startup_only,
            }
            decision, reasons, recovered = recovery_decision(evidence)
            result.update(
                decision=decision,
                reasons=reasons,
                checks=checks,
                version_id=version_id,
                generation_id=generation,
                space_id=space,
                vector_count=len(vectors),
                projection_count=len(projections),
                indexed_chunk_count=len(indexed_projections),
                parent_context_count=len(projections) - len(indexed_projections),
                vector_error=vector_error,
                startup_recovery_only=startup_only,
                recovered=recovered,
            )
            report.append(result)
        return report

    def run(self, output: Path, *, apply: bool = False) -> dict[str, Any]:
        output.mkdir(parents=True, exist_ok=True)
        with self.db.transaction() as connection:
            data = self.collect(connection, lock=apply)
            documents = self.diagnose(data)
            changed = []
            if apply:
                affected = [
                    item["document_id"] for item in documents if item["decision"] == "repairable"
                ]
                backup = {
                    table: [
                        row
                        for row in rows
                        if table != "lifecycle_entities"
                        or row.get("entity_id") in affected
                        or row.get("parent_id") in affected
                        or row.get("entity_type") == "audit_events"
                    ]
                    for table, rows in data.items()
                }
                backup_path = output / "repair-before.json"
                # Never replace a previous backup on repeat execution.
                if affected:
                    with backup_path.open("x", encoding="utf-8") as file:
                        json.dump(backup, file, ensure_ascii=False, default=str, indent=2)
                events = sorted(
                    (
                        self.decoded(row)
                        for row in data["lifecycle_entities"]
                        if row["entity_type"] == "audit_events"
                    ),
                    key=lambda row: row["sequence"],
                )
                for item in documents:
                    if item["decision"] != "repairable":
                        continue
                    document_id = item["document_id"]
                    record = next(
                        (
                            row
                            for row in data["lifecycle_entities"]
                            if row["entity_type"] == "documents" and row["entity_id"] == document_id
                        ),
                        None,
                    )
                    payload = json.dumps(item["recovered"], ensure_ascii=False, sort_keys=True)
                    if record:
                        cursor = self.db.execute(
                            connection,
                            'UPDATE lifecycle_entities SET '
                            'payload_json=?,entity_revision=entity_revision+1,updated_at=NOW(6)'
                            " WHERE tenant_id=? AND entity_type='documents' AND entity_id=?"
                            ' AND entity_revision=?',
                            (payload, self.tenant, document_id, record["entity_revision"]),
                        )
                        if cursor.rowcount != 1:
                            raise RuntimeError("REPAIR_CONCURRENT_MODIFICATION")
                    else:
                        self.db.execute(
                            connection,
                            'INSERT INTO '
                            'lifecycle_entities(tenant_id,entity_type,entity_id,logical_key,parent_id,ordinal,payload_json,entity_revision,created_at,updated_at)'
                            " VALUES (?,'documents',?,?,NULL,0,?,1,NOW(6),NOW(6))",
                            (self.tenant, document_id, document_id, payload),
                        )
                    sequence = events[-1]["sequence"] + 1 if events else 1
                    event = {
                        "sequence": sequence,
                        "action": "publication.history_recovered",
                        "resource_id": document_id,
                        "trace_id": "history-repair:" + document_id,
                        "governance_revision": "publication-history-repair:v1",
                        "previous_hash": events[-1]["event_hash"] if events else "0" * 64,
                    }
                    event["event_hash"] = hashlib.sha256(
                        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    self.db.execute(
                        connection,
                        'INSERT INTO '
                        'lifecycle_entities(tenant_id,entity_type,entity_id,logical_key,parent_id,ordinal,payload_json,entity_revision,created_at,updated_at)'
                        " VALUES (?,'audit_events',?,?,?,?,?,1,NOW(6),NOW(6))",
                        (
                            self.tenant,
                            str(sequence),
                            str(sequence),
                            document_id,
                            sequence - 1,
                            json.dumps(event, sort_keys=True),
                        ),
                    )
                    events.append(event)
                    changed.append(document_id)
            result = {
                "mode": "apply" if apply else "dry-run",
                "checked_at": time.time(),
                "changed": changed,
                "documents": documents,
                "database_rebuilt": False,
                "vectors_modified": False,
            }
        (output / ("applied.json" if apply else "diagnosis.json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
