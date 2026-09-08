"""Server-side document classification, filtering and keyset pagination.

All surfaces use the same SQL facts; a SERVING label alone never implies that
a document can answer questions. Historical versions do not multiply counts.
"""

from __future__ import annotations

import time
from typing import Any

from ragkb.domain.pagination import PageKey, RepositoryPage
from ragkb.infrastructure.workspace_db import WorkspaceDB


def _j(alias: str, field: str) -> str:
    return f"NULLIF(JSON_UNQUOTE(JSON_EXTRACT({alias}.payload_json, '$.{field}')), 'null')"


REASONS = {
    "processing": "文档仍在处理，完成后可检查质量并发布。",
    "failed": "最近版本处理失败，请查看任务原因并重试。",
    "cancelled": "最近版本处理已取消。",
    "withdrawn": "文档已撤回，不再参与问答。",
    "deleted": "文档已删除。",
    "inconsistent": "发布记录、当前版本与索引状态不一致，需要诊断或重新复核。",
    "pending_review": "解析完成，检查质量并确认发布后可参与问答。",
}


class WorkspaceQueries:
    def __init__(self, db: WorkspaceDB, tenant_id: str, generation_id: str) -> None:
        self.db, self.tenant_id, self.generation_id = db, tenant_id, generation_id

    def _facts(self) -> tuple[str, tuple[Any, ...]]:
        now = int(time.time())
        if self.db.mysql:
            j = _j
            sql = f"""
            WITH e AS (SELECT * FROM upload_entities WHERE tenant_id=?),
            versions AS (
              SELECT e.*, ROW_NUMBER() OVER(PARTITION BY parent_id
                ORDER BY ordinal DESC,entity_id DESC) rn
              FROM e WHERE entity_type='versions'),
            sessions AS (
              SELECT e.*, ROW_NUMBER() OVER(PARTITION BY {j("e", "document_version_id")}
                ORDER BY updated_at DESC,entity_id DESC) rn FROM e WHERE entity_type='sessions'),
            facts AS (
              SELECT d.entity_id document_id, v.entity_id version_id,
                COALESCE({j("d", "space_id")},{j("s", "space_id")}) space_id,
                COALESCE({j("s", "filename")},{j("d", "external_key")}) filename,
                {j("s", "job_id")} job_id, {j("s", "error_code")} error_code,
                CAST(COALESCE({j("v", "version_no")},1) AS UNSIGNED) version_no,
                {j("v", "processing_state")} processing_state,
                {j("v", "publication_state")} publication_state,
                {j("v", "parser_revision")} parser_revision,
                {j("v", "mime_type")} mime_type,
                {j("q", "disposition")} quality_disposition,
                {j("q", "issue_codes")} quality_issues,
                CAST(COALESCE({j("s", "expected_size")},0) AS UNSIGNED) size_bytes,
                CAST(UNIX_TIMESTAMP(GREATEST(d.updated_at,v.updated_at,
                  COALESCE(s.updated_at,d.updated_at),
                  COALESCE(l.updated_at,d.updated_at)))*1000 AS UNSIGNED) updated_ms,
                {j("d", "state")} document_state,
                {j("d", "current_version_id")} current_version_id,
                {j("l", "lifecycle_state")} lifecycle_state,
                {j("l", "active_version_id")} active_version_id,
                COALESCE({j("l", "visible")},'false') visible,
                COALESCE({j("l", "tombstoned")},'false') tombstoned,
                (SELECT COUNT(*) FROM retrieval_chunk_projections p
                  WHERE p.tenant_id=d.tenant_id AND p.document_version_id=v.entity_id
                  AND p.index_generation_id=?
                  AND COALESCE(JSON_EXTRACT(p.locator_json,'$.is_parent'),false)=false) chunk_count,
                (SELECT COUNT(*) FROM retrieval_chunk_projections p
                  JOIN retrieval_release_state r ON r.tenant_id=p.tenant_id
                    AND r.space_id=p.space_id
                  WHERE p.tenant_id=d.tenant_id AND p.document_id=d.entity_id
                  AND p.document_version_id={j("d", "current_version_id")}
                  AND p.index_generation_id=r.active_generation_id
                  AND p.lifecycle_projection='SERVING' AND p.current_version=1
                  AND p.permission_revision=CAST({j("l", "acl_revision")} AS UNSIGNED)
                  AND p.permission_revision<=r.active_permission_revision
                  AND r.security_watermark>=r.active_permission_revision
                  AND p.valid_from_epoch<=?
                  AND (p.valid_to_epoch=0 OR p.valid_to_epoch>?)) serving_count,
                (SELECT COUNT(*) FROM e av WHERE av.entity_type='versions'
                  AND av.entity_id={j("d", "current_version_id")}
                  AND {j("av", "publication_state")}='SERVING') serving_version
              FROM e d JOIN versions v ON v.parent_id=d.entity_id AND v.rn=1
              LEFT JOIN sessions s ON {j("s", "document_version_id")}=v.entity_id AND s.rn=1
              LEFT JOIN e q ON q.entity_type='quality' AND q.entity_id=v.entity_id
              LEFT JOIN lifecycle_entities l ON l.tenant_id=d.tenant_id
                AND l.entity_type='documents' AND l.entity_id=d.entity_id
              WHERE d.entity_type='documents'
            )
            """  # noqa: S608 - JSON identifiers are fixed, query values are parameterized
            args: tuple[Any, ...] = (self.tenant_id, self.generation_id, now, now)
        else:
            sql = """
            WITH versions AS (
              SELECT v.*,ROW_NUMBER() OVER(PARTITION BY document_id
                ORDER BY version_no DESC,id DESC) rn
              FROM document_versions v),
            sessions AS (
              SELECT s.*,ROW_NUMBER() OVER(PARTITION BY document_version_id
                ORDER BY updated_at DESC,id DESC) rn
              FROM upload_sessions s),
            facts AS (
              SELECT d.id document_id,v.id version_id,c.space_id,
                COALESCE(s.filename,d.external_key) filename,s.job_id,s.error_code,
                v.version_no,v.processing_state,v.publication_state,v.parser_revision,v.mime_type,
                q.disposition quality_disposition,q.issue_codes_json quality_issues,
                COALESCE(s.expected_size,0) size_bytes,
                CAST(MAX(d.updated_at,COALESCE(s.updated_at,d.updated_at))*1000
                  AS INTEGER) updated_ms,
                d.state document_state,d.current_version_id,l.lifecycle_state,l.active_version_id,
                COALESCE(l.visible,0) visible,COALESCE(l.tombstoned,0) tombstoned,
                (SELECT COUNT(*) FROM chunks ch
                  WHERE ch.version_id=v.id AND ch.kind!='parent') chunk_count,
                (SELECT COUNT(*) FROM retrieval_projections p WHERE p.document_id=d.id
                  AND p.document_version_id=d.current_version_id AND p.current_version=1
                  AND p.lifecycle_projection='SERVING' AND p.permission_revision=l.acl_revision
                  AND p.valid_from_epoch<=?
                  AND (p.valid_to_epoch=0 OR p.valid_to_epoch>?)) serving_count,
                (SELECT COUNT(*) FROM document_versions av WHERE av.id=d.current_version_id
                  AND av.publication_state='SERVING') serving_version
              FROM documents d JOIN sources src ON src.id=d.source_id
              JOIN corpora c ON c.id=src.corpus_id
              JOIN versions v ON v.document_id=d.id AND v.rn=1
              LEFT JOIN sessions s ON s.document_version_id=v.id AND s.rn=1
              LEFT JOIN document_quality_reports q ON q.version_id=v.id
              LEFT JOIN lifecycle_records l ON l.document_id=d.id
              WHERE d.tenant_id=?
            )
            """
            args = (now, now, self.tenant_id)
        sql += """, classified AS (
          SELECT facts.*,CASE
            WHEN document_state='DELETED' OR tombstoned IN (1,'true','1') THEN 'deleted'
            WHEN lifecycle_state IN ('REVOKED','TOMBSTONED') THEN 'withdrawn'
            WHEN lifecycle_state='ACTIVE' AND visible IN (1,'true','1')
              AND active_version_id=current_version_id AND serving_version>0 AND serving_count>0
              THEN 'available'
            WHEN publication_state='SERVING' OR lifecycle_state='ACTIVE' THEN 'inconsistent'
            WHEN processing_state IN ('FAILED','QUARANTINED') THEN 'failed'
            WHEN processing_state='CANCELLED' THEN 'cancelled'
            WHEN processing_state!='VALIDATED' THEN 'processing'
            ELSE 'pending_review' END availability FROM facts
        ) """
        return sql, args

    @staticmethod
    def decorate(row: dict[str, Any]) -> dict[str, Any]:
        availability = row["availability"]
        actions = ["view", "download", "upload_version", "delete"]
        quality_blocked = row.get("quality_disposition") == "BLOCKED_REAL_VALIDATION"
        if (
            row["processing_state"] == "VALIDATED"
            and not quality_blocked
            and (
                availability in ("pending_review", "withdrawn", "inconsistent")
                or (availability == "available" and row["version_id"] != row["current_version_id"])
            )
        ):
            actions.append("review_publish")
        if availability == "available":
            actions.extend(["ask", "revoke"])
        if row["job_id"] and row["processing_state"] in ("FAILED", "CANCELLED"):
            actions.append("retry")
        if row["processing_state"] in ("DRAFT", "PROCESSING") and row["job_id"]:
            actions.append("cancel")
        reasons = [REASONS[availability]] if availability in REASONS else []
        if quality_blocked:
            actions.append("review_quality")
            visual = "VISUAL_" in str(row.get("quality_issues", ""))
            reasons = [
                "最新版本图片复核尚未完成，请到原图与识别逐项处理后重新检查质量。"
                if visual
                else "最新版本质量检查未通过，请处理质量问题后再发布。"
            ]
        if availability == "available" and row["current_version_id"] != row["version_id"]:
            reasons.append("当前发布版本可用于问答；最新上传版本尚未发布。")
        return {
            **row,
            "is_answerable": availability == "available",
            "unavailability_reasons": reasons,
            "available_actions": actions,
            "updated_at": row["updated_ms"] / 1000,
        }

    def documents(
        self,
        space_id: str,
        *,
        q: str = "",
        processing: str = "",
        availability: str = "",
        sort: str = "updated_desc",
        limit: int = 30,
        after: PageKey | None = None,
        offset: int = 0,
        document_id: str = "",
    ) -> RepositoryPage:
        sql, args = self._facts()
        clauses = ["space_id=?", "availability!='deleted'"]
        values: list[Any] = [*args, space_id]
        for column, value in [
            ("processing_state", processing),
            ("availability", availability),
            ("document_id", document_id),
        ]:
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        if q:
            clauses.append("LOWER(filename) LIKE ? ESCAPE '!'")
            values.append(
                "%" + q.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
            )
        direction, comparator = ("ASC", ">") if sort == "updated_asc" else ("DESC", "<")
        if after:
            clauses.append(
                f"(updated_ms {comparator} ? OR (updated_ms=? AND document_id {comparator} ?))"
            )
            values.extend([after[0], after[0], after[1]])
        sql += "SELECT * FROM classified WHERE " + " AND ".join(clauses)  # noqa: S608
        sql += f" ORDER BY updated_ms {direction},document_id {direction} LIMIT ? OFFSET ?"
        with self.db.connection() as connection:
            rows = self.db.rows(connection, sql, [*values, limit + 1, offset])
        key = (
            (int(rows[limit - 1]["updated_ms"]), str(rows[limit - 1]["document_id"]))
            if len(rows) > limit
            else None
        )
        return RepositoryPage([self.decorate(row) for row in rows[:limit]], key)

    def summaries(self, space_ids: tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
        if space_ids == ():
            return {}
        sql, args = self._facts()
        sql += """SELECT space_id,COUNT(*) document_count,
          SUM(CASE WHEN availability='available' THEN 1 ELSE 0 END) answerable_count,
          SUM(CASE WHEN availability IN ('pending_review','inconsistent','failed','cancelled')
            OR (availability='available' AND version_id!=current_version_id)
            THEN 1 ELSE 0 END) pending_count,
          SUM(CASE WHEN availability='processing' THEN 1 ELSE 0 END) processing_count,
          COALESCE(SUM(chunk_count),0) chunk_count,MAX(updated_ms) updated_ms
          FROM classified WHERE availability!='deleted' """
        if space_ids is not None:
            sql += "AND space_id IN (" + ",".join("?" for _ in space_ids) + ") "
            args = (*args, *space_ids)
        sql += "GROUP BY space_id"
        with self.db.connection() as connection:
            rows = self.db.rows(connection, sql, args)
        return {
            row["space_id"]: {k: int(v or 0) for k, v in row.items() if k != "space_id"}
            for row in rows
        }

    def metadata(self, space_ids: tuple[str, ...] | None = None) -> dict[str, str]:
        if space_ids == ():
            return {}
        sql = "SELECT resource_id,description FROM workspace_metadata WHERE tenant_id=?"
        args: tuple[str, ...] = (self.tenant_id,)
        if space_ids is not None:
            sql += " AND resource_id IN (" + ",".join("?" for _ in space_ids) + ")"
            args = (*args, *space_ids)
        with self.db.connection() as connection:
            rows = self.db.rows(connection, sql, args)
        return {row["resource_id"]: row["description"] for row in rows}

    def set_description(self, space_id: str, description: str) -> None:
        with self.db.transaction() as connection:
            self.db.execute(
                connection,
                "DELETE FROM workspace_metadata WHERE tenant_id=? AND resource_id=?",
                (self.tenant_id, space_id),
            )
            self.db.execute(
                connection,
                "INSERT INTO workspace_metadata(tenant_id,resource_id,description) VALUES (?,?,?)",
                (self.tenant_id, space_id, description),
            )
