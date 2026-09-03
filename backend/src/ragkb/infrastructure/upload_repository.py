"""SQLite upload, document and version repository for the G1 local adapter."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from ragkb.domain.ids import new_uuid7
from ragkb.domain.state_machines import (
    DocumentState,
    PublicationState,
    UploadSessionState,
    VersionProcessingState,
    transition_upload,
)
from ragkb.domain.uploads import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    ResourceNotFoundError,
    UploadSession,
)
from ragkb.domain.validation import DocumentQualityReport
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLiteUploadRepository:
    revision = "sqlite-upload-repository:g1-v1"
    cleanable_partitions = frozenset({"original", "artifacts", "quarantine", "temp"})

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.database.initialize()

    @staticmethod
    def _session(row: sqlite3.Row) -> UploadSession:
        return UploadSession(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            space_id=str(row["space_id"]),
            filename=str(row["filename"]),
            expected_size=int(row["expected_size"]),
            expected_sha256=str(row["expected_sha256"]),
            declared_mime=str(row["declared_mime"]),
            state=UploadSessionState(str(row["state"])),
            quarantine_key=str(row["quarantine_key"]),
            original_key=str(row["original_key"]) if row["original_key"] is not None else None,
            detected_format=(
                str(row["detected_format"]) if row["detected_format"] is not None else None
            ),
            detected_mime=(str(row["detected_mime"]) if row["detected_mime"] is not None else None),
            document_id=str(row["document_id"]) if row["document_id"] is not None else None,
            document_version_id=(
                str(row["document_version_id"]) if row["document_version_id"] is not None else None
            ),
            target_document_id=(
                str(row["target_document_id"]) if row["target_document_id"] is not None else None
            ),
            target_document_row_version=(
                int(row["target_document_row_version"])
                if row["target_document_row_version"] is not None
                else None
            ),
            job_id=str(row["job_id"]) if row["job_id"] is not None else None,
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            row_version=int(row["row_version"]),
        )

    def ensure_local_hierarchy(self, tenant_code: str, space_name: str) -> tuple[str, str]:
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            tenant = connection.execute(
                "SELECT id FROM tenants WHERE code = ?", (tenant_code,)
            ).fetchone()
            if tenant is None:
                tenant_id = new_uuid7()
                connection.execute(
                    "INSERT INTO tenants(id, code, status, created_at) VALUES (?, ?, 'ACTIVE', ?)",
                    (tenant_id, tenant_code, now),
                )
            else:
                tenant_id = str(tenant["id"])
            space = connection.execute(
                "SELECT id FROM knowledge_spaces WHERE tenant_id = ? AND name = ?",
                (tenant_id, space_name),
            ).fetchone()
            if space is None:
                space_id = new_uuid7()
                connection.execute(
                    """
                    INSERT INTO knowledge_spaces(id, tenant_id, name, status, created_at)
                    VALUES (?, ?, ?, 'ACTIVE', ?)
                    """,
                    (space_id, tenant_id, space_name, now),
                )
            else:
                space_id = str(space["id"])
            corpus = connection.execute(
                "SELECT id FROM corpora WHERE space_id = ? AND name = 'uploads'", (space_id,)
            ).fetchone()
            if corpus is None:
                corpus_id = new_uuid7()
                connection.execute(
                    """
                    INSERT INTO corpora(id, tenant_id, space_id, name, created_at)
                    VALUES (?, ?, ?, 'uploads', ?)
                    """,
                    (corpus_id, tenant_id, space_id, now),
                )
            else:
                corpus_id = str(corpus["id"])
            source = connection.execute(
                "SELECT id FROM sources WHERE corpus_id = ? AND external_key = 'local-upload'",
                (corpus_id,),
            ).fetchone()
            if source is None:
                connection.execute(
                    """
                    INSERT INTO sources(id, tenant_id, corpus_id, kind, external_key, created_at)
                    VALUES (?, ?, ?, 'UPLOAD', 'local-upload', ?)
                    """,
                    (new_uuid7(), tenant_id, corpus_id, now),
                )
            return tenant_id, space_id

    def list_spaces(self) -> list[dict[str, str]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, tenant_id, name, status FROM knowledge_spaces ORDER BY created_at"
            ).fetchall()
            return [dict(row) for row in rows]

    def _idempotency_in(
        self,
        connection: sqlite3.Connection,
        operation: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT request_hash, response_json FROM idempotency_records
            WHERE operation = ? AND idempotency_key = ?
            """,
            (operation, key),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_hash"]) != request_hash:
            raise IdempotencyConflictError("idempotency key reused with a different request hash")
        return json.loads(str(row["response_json"])) if row["response_json"] is not None else {}

    def idempotency_response(
        self, operation: str, key: str, request_hash: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            return self._idempotency_in(connection, operation, key, request_hash)

    def save_idempotency_response(
        self,
        operation: str,
        key: str,
        request_hash: str,
        resource_id: str,
        response: dict[str, Any],
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            existing = self._idempotency_in(connection, operation, key, request_hash)
            if existing is not None:
                return
            connection.execute(
                """
                INSERT INTO idempotency_records(
                    operation, idempotency_key, request_hash, resource_id, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    operation,
                    key,
                    request_hash,
                    resource_id,
                    json.dumps(response, sort_keys=True),
                    time.time(),
                ),
            )

    def create_upload_session(
        self,
        *,
        tenant_id: str,
        space_id: str,
        filename: str,
        expected_size: int,
        expected_sha256: str,
        declared_mime: str,
        idempotency_key: str,
        request_hash: str,
        target_document_id: str | None = None,
        target_document_row_version: int | None = None,
    ) -> UploadSession:
        operation = (
            f"create-version-upload-session:{target_document_id}"
            if target_document_id
            else f"create-upload-session:{space_id}"
        )
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            existing = self._idempotency_in(connection, operation, idempotency_key, request_hash)
            if existing is not None:
                return self._get_session_in(connection, str(existing["upload_session_id"]))
            space = connection.execute(
                "SELECT id FROM knowledge_spaces WHERE id = ? AND tenant_id = ?",
                (space_id, tenant_id),
            ).fetchone()
            if space is None:
                raise ResourceNotFoundError(space_id)
            if target_document_id is not None:
                document = connection.execute(
                    "SELECT tenant_id, row_version, state FROM documents WHERE id = ?",
                    (target_document_id,),
                ).fetchone()
                if document is None or str(document["tenant_id"]) != tenant_id:
                    raise ResourceNotFoundError(target_document_id)
                if str(document["state"]) == "DELETED":
                    raise ResourceNotFoundError(target_document_id)
                if (
                    target_document_row_version is None
                    or int(document["row_version"]) != target_document_row_version
                ):
                    raise OptimisticConcurrencyError(target_document_id)
            session_id = new_uuid7()
            quarantine_key = f"upload-sessions/{session_id}/{filename}"
            connection.execute(
                """
                INSERT INTO upload_sessions(
                    id, tenant_id, space_id, filename, expected_size, expected_sha256,
                    declared_mime, state, quarantine_key, target_document_id,
                    target_document_row_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    tenant_id,
                    space_id,
                    filename,
                    expected_size,
                    expected_sha256,
                    declared_mime,
                    UploadSessionState.CREATED.value,
                    quarantine_key,
                    target_document_id,
                    target_document_row_version,
                    now,
                    now,
                ),
            )
            response = {"upload_session_id": session_id}
            connection.execute(
                """
                INSERT INTO idempotency_records(
                    operation, idempotency_key, request_hash, resource_id, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    operation,
                    idempotency_key,
                    request_hash,
                    session_id,
                    json.dumps(response, sort_keys=True),
                    now,
                ),
            )
            return self._get_session_in(connection, session_id)

    def _get_session_in(self, connection: sqlite3.Connection, session_id: str) -> UploadSession:
        row = connection.execute(
            "SELECT * FROM upload_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError(session_id)
        return self._session(row)

    def get_session(self, session_id: str) -> UploadSession:
        with self.database.connect() as connection:
            return self._get_session_in(connection, session_id)

    def update_session(
        self,
        session_id: str,
        expected_row_version: int,
        state: UploadSessionState,
        **fields: str | None,
    ) -> UploadSession:
        allowed = {
            "original_key",
            "detected_format",
            "detected_mime",
            "document_id",
            "document_version_id",
            "job_id",
            "error_code",
        }
        if not set(fields).issubset(allowed):
            raise ValueError("unsupported upload session update field")
        assignments = ["state = ?", "row_version = row_version + 1", "updated_at = ?"]
        values: list[object] = [state.value, time.time()]
        for name, value in fields.items():
            assignments.append(f"{name} = ?")
            values.append(value)
        values.extend((session_id, expected_row_version))
        with self.database.transaction(immediate=True) as connection:
            current = self._get_session_in(connection, session_id)
            if current.row_version != expected_row_version:
                raise OptimisticConcurrencyError(session_id)
            transition_upload(current.state, state)
            query = (
                f"UPDATE upload_sessions SET {', '.join(assignments)} "  # noqa: S608
                "WHERE id = ? AND row_version = ?"
            )
            cursor = connection.execute(
                query,
                values,
            )
            if cursor.rowcount != 1:
                if (
                    connection.execute(
                        "SELECT 1 FROM upload_sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    is None
                ):
                    raise ResourceNotFoundError(session_id)
                raise OptimisticConcurrencyError(session_id)
            return self._get_session_in(connection, session_id)

    def ensure_document_version(self, session: UploadSession) -> tuple[str, str]:
        if session.document_id and session.document_version_id:
            return session.document_id, session.document_version_id
        if session.original_key is None or session.detected_mime is None:
            raise ValueError("session must be promoted before creating a document version")
        now = time.time()
        with self.database.transaction(immediate=True) as connection:
            current = self._get_session_in(connection, session.id)
            if current.document_id and current.document_version_id:
                self._ensure_draft_lifecycle(
                    connection,
                    current.tenant_id,
                    current.document_id,
                    current.document_version_id,
                )
                return current.document_id, current.document_version_id
            version_id = new_uuid7()
            if current.target_document_id is not None:
                document_id = current.target_document_id
                document = connection.execute(
                    "SELECT tenant_id, row_version, state FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
                if document is None or str(document["tenant_id"]) != session.tenant_id:
                    raise ResourceNotFoundError(document_id)
                if str(document["state"]) == "DELETED":
                    raise ResourceNotFoundError(document_id)
                if (
                    current.target_document_row_version is None
                    or int(document["row_version"]) != current.target_document_row_version
                ):
                    raise OptimisticConcurrencyError(document_id)
                version_no = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version
                        FROM document_versions WHERE document_id = ?
                        """,
                        (document_id,),
                    ).fetchone()["next_version"]
                )
                cursor = connection.execute(
                    """
                    UPDATE documents SET row_version = row_version + 1, updated_at = ?
                    WHERE id = ? AND row_version = ?
                    """,
                    (now, document_id, current.target_document_row_version),
                )
                if cursor.rowcount != 1:
                    raise OptimisticConcurrencyError(document_id)
            else:
                source = connection.execute(
                    """
                    SELECT src.id FROM sources src
                    JOIN corpora c ON c.id = src.corpus_id
                    WHERE c.space_id = ? AND src.external_key = 'local-upload'
                    """,
                    (session.space_id,),
                ).fetchone()
                if source is None:
                    raise ResourceNotFoundError("local-upload source")
                document_id = session.id
                version_no = 1
                connection.execute(
                    """
                    INSERT INTO documents(
                        id, tenant_id, source_id, external_key, state, current_version_id,
                        row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, 1, ?, ?)
                    """,
                    (
                        document_id,
                        session.tenant_id,
                        source["id"],
                        session.id,
                        DocumentState.ACTIVE.value,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO document_versions(
                    id, tenant_id, document_id, version_no, content_sha256, original_key,
                    mime_type, processing_state, publication_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    session.tenant_id,
                    document_id,
                    version_no,
                    session.expected_sha256,
                    session.original_key,
                    session.detected_mime,
                    VersionProcessingState.PROCESSING.value,
                    PublicationState.DRAFT.value,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE upload_sessions SET document_id = ?, document_version_id = ?,
                    row_version = row_version + 1, updated_at = ? WHERE id = ?
                """,
                (document_id, version_id, now, session.id),
            )
            if current.target_document_id is None:
                self._ensure_draft_lifecycle(
                    connection,
                    session.tenant_id,
                    document_id,
                    version_id,
                )
            for partition, storage_key, content_kind in (
                ("original", session.original_key, "source_original"),
                ("quarantine", session.quarantine_key, "upload_quarantine"),
            ):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO local_content_lineage(
                        document_id, version_id, partition, storage_key,
                        content_kind, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        version_id,
                        partition,
                        storage_key,
                        content_kind,
                        now,
                    ),
                )
            return document_id, version_id

    @staticmethod
    def _ensure_draft_lifecycle(
        connection: sqlite3.Connection,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO lifecycle_records(
                document_id, tenant_id, active_version_id, version_history_json,
                lifecycle_state, acl_revision, visible, tombstoned, row_version
            ) VALUES (?, ?, ?, '[]', 'DRAFT', 1, 0, 0, 1)
            """,
            (document_id, tenant_id, version_id),
        )

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError(document_id)
            return dict(row)

    def get_versions(self, document_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_versions WHERE document_id = ? ORDER BY version_no",
                (document_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_version(self, version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError(version_id)
            return dict(row)

    def list_original_keys(self, document_id: str) -> tuple[str, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT original_key FROM document_versions
                WHERE document_id = ? ORDER BY version_no
                """,
                (document_id,),
            ).fetchall()
        return tuple(str(row["original_key"]) for row in rows if row["original_key"])

    def record_local_content(
        self,
        document_id: str,
        version_id: str | None,
        partition: str,
        storage_key: str,
        content_kind: str,
    ) -> None:
        if partition not in self.cleanable_partitions:
            raise ValueError("local content partition is not cleanup-authorized")
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO local_content_lineage(
                    document_id, version_id, partition, storage_key,
                    content_kind, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, version_id, partition, storage_key, content_kind, time.time()),
            )

    def save_quality_report(self, report: DocumentQualityReport) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO document_quality_reports(
                    version_id, source_format, parser_revision, node_count,
                    locator_coverage, issue_codes_json, disposition,
                    real_acceptance, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    report.document_version_id,
                    report.source_format,
                    report.parser_revision,
                    report.node_count,
                    report.locator_coverage,
                    json.dumps(report.issue_codes),
                    report.disposition.value,
                    time.time(),
                ),
            )

    def get_quality_report(self, version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_quality_reports WHERE version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError(version_id)
        result = dict(row)
        result["issue_codes"] = json.loads(str(result.pop("issue_codes_json")))
        return result

    def save_document_review(
        self,
        *,
        version_id: str,
        reviewer_id: str,
        decision: str,
        comment: str,
        quality_revision: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        operation = f"review-document-version:{version_id}"
        with self.database.transaction(immediate=True) as connection:
            existing = self._idempotency_in(connection, operation, idempotency_key, request_hash)
            if existing is not None:
                return existing
            if (
                connection.execute(
                    "SELECT 1 FROM document_quality_reports WHERE version_id = ?",
                    (version_id,),
                ).fetchone()
                is None
            ):
                raise ResourceNotFoundError(version_id)
            review_id = new_uuid7()
            result = {
                "review_id": review_id,
                "document_version_id": version_id,
                "reviewer_id": reviewer_id,
                "decision": decision,
                "comment": comment,
                "quality_revision": quality_revision,
                "real_acceptance": False,
            }
            connection.execute(
                """
                INSERT INTO document_reviews(
                    review_id, version_id, reviewer_id, decision, comment,
                    quality_revision, real_acceptance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    review_id,
                    version_id,
                    reviewer_id,
                    decision,
                    comment,
                    quality_revision,
                    time.time(),
                ),
            )
            connection.execute(
                """
                INSERT INTO idempotency_records(
                    operation, idempotency_key, request_hash, resource_id,
                    response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    operation,
                    idempotency_key,
                    request_hash,
                    review_id,
                    json.dumps(result, sort_keys=True),
                    time.time(),
                ),
            )
            return result

    def list_local_content_lineage(self, document_id: str) -> tuple[tuple[str, str], ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT partition, storage_key FROM local_content_lineage
                WHERE document_id = ? ORDER BY partition, storage_key
                """,
                (document_id,),
            ).fetchall()
            versions = connection.execute(
                """
                SELECT original_key FROM document_versions
                WHERE document_id = ? ORDER BY version_no
                """,
                (document_id,),
            ).fetchall()
            sessions = connection.execute(
                """
                SELECT quarantine_key, original_key FROM upload_sessions
                WHERE target_document_id = ? OR document_id = ?
                """,
                (document_id, document_id),
            ).fetchall()
        lineage = {(str(row["partition"]), str(row["storage_key"])) for row in rows}
        for row in versions:
            original_key = str(row["original_key"])
            lineage.add(("original", original_key))
            prefix, separator, _ = original_key.rpartition("/original/")
            if separator:
                lineage.add(("artifacts", f"{prefix}/artifacts/canonical-document-v1.json"))
        for row in sessions:
            lineage.add(("quarantine", str(row["quarantine_key"])))
            if row["original_key"]:
                lineage.add(("original", str(row["original_key"])))
        return tuple(sorted(lineage))

    def save_canonical_document(self, document: Any) -> None:
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                """
                SELECT tenant_id, document_id, content_sha256
                FROM document_versions WHERE id = ?
                """,
                (document.document_version_id,),
            ).fetchone()
            if version is None:
                raise ResourceNotFoundError(document.document_version_id)
            tenant_id = str(version["tenant_id"])
            connection.execute(
                "DELETE FROM chunks WHERE version_id = ?", (document.document_version_id,)
            )
            connection.execute(
                "DELETE FROM sections WHERE version_id = ?", (document.document_version_id,)
            )
            section_id = new_uuid7()
            first_locator = document.nodes[0].locator.to_dict()
            connection.execute(
                """
                INSERT INTO sections(
                    id, tenant_id, version_id, parent_id, ordinal, title, path, locator_json
                ) VALUES (?, ?, ?, NULL, 0, ?, ?, ?)
                """,
                (
                    section_id,
                    tenant_id,
                    document.document_version_id,
                    "Document",
                    "/",
                    json.dumps(first_locator, sort_keys=True),
                ),
            )
            for ordinal, node in enumerate(document.nodes):
                text_hash = hashlib.sha256(node.original_text.encode("utf-8")).hexdigest()
                connection.execute(
                    """
                    INSERT INTO chunks(
                        id, tenant_id, version_id, section_id, parent_chunk_id, ordinal,
                        original_text, display_text, retrieval_text, locator_json,
                        content_sha256, token_count, kind, chunking_revision, tokenizer_id, status
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'STAGED')
                    """,
                    (
                        new_uuid7(),
                        tenant_id,
                        document.document_version_id,
                        section_id,
                        ordinal,
                        node.original_text,
                        node.display_text,
                        node.display_text,
                        json.dumps(node.locator.to_dict(), sort_keys=True),
                        text_hash,
                        max(1, len(node.original_text.split())),
                        node.node_type.value,
                        "node-per-chunk:g1-v1",
                        "whitespace-estimate:g1-v1",
                    ),
                )
            connection.execute(
                """
                UPDATE document_versions SET processing_state = ?, parser_revision = ?
                WHERE id = ?
                """,
                (
                    VersionProcessingState.VALIDATED.value,
                    document.parser_revision,
                    document.document_version_id,
                ),
            )
            connection.execute(
                """
                UPDATE lifecycle_records SET lifecycle_state = 'STAGED', visible = 0,
                    row_version = row_version + 1
                WHERE document_id = ? AND tombstoned = 0
                    AND lifecycle_state IN ('DRAFT', 'STAGED')
                    AND EXISTS (
                        SELECT 1 FROM documents d
                        WHERE d.id = lifecycle_records.document_id
                            AND d.current_version_id IS NULL
                    )
                """,
                (str(version["document_id"]),),
            )
            connection.execute(
                """
                INSERT INTO publication_candidates(
                    version_id, document_id, generation_id, projection_state,
                    required_watermark, observed_watermark, expected_checksum,
                    observed_checksum, updated_at
                ) VALUES (?, ?, ?, 'STAGED', 0, 0, ?, ?, ?)
                ON CONFLICT(version_id) DO UPDATE SET
                    generation_id = excluded.generation_id,
                    projection_state = 'STAGED',
                    required_watermark = excluded.required_watermark,
                    observed_watermark = excluded.observed_watermark,
                    expected_checksum = excluded.expected_checksum,
                    observed_checksum = excluded.observed_checksum,
                    updated_at = excluded.updated_at
                WHERE publication_candidates.projection_state NOT IN ('ACTIVE', 'RETIRED')
                """,
                (
                    document.document_version_id,
                    str(version["document_id"]),
                    f"local-generation:{document.document_version_id}",
                    str(version["content_sha256"]),
                    str(version["content_sha256"]),
                    time.time(),
                ),
            )

    def mark_version_quarantined(self, version_id: str, parser_revision: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE document_versions SET processing_state = ?, parser_revision = ? WHERE id = ?
                """,
                (VersionProcessingState.QUARANTINED.value, parser_revision, version_id),
            )
            if cursor.rowcount != 1:
                raise ResourceNotFoundError(version_id)

    def mark_version_failed(self, version_id: str, parser_revision: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE document_versions SET processing_state = ?, parser_revision = ? WHERE id = ?
                """,
                (VersionProcessingState.FAILED.value, parser_revision, version_id),
            )
            if cursor.rowcount != 1:
                raise ResourceNotFoundError(version_id)

    def mark_version_cancelled(self, version_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM chunks WHERE version_id = ?", (version_id,))
            connection.execute("DELETE FROM sections WHERE version_id = ?", (version_id,))
            cursor = connection.execute(
                "UPDATE document_versions SET processing_state = ? WHERE id = ?",
                (VersionProcessingState.CANCELLED.value, version_id),
            )
            if cursor.rowcount != 1:
                raise ResourceNotFoundError(version_id)

    def mark_version_processing(self, version_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                "SELECT processing_state FROM document_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if version is None:
                raise ResourceNotFoundError(version_id)
            state = VersionProcessingState(str(version["processing_state"]))
            if state not in {
                VersionProcessingState.CANCELLED,
                VersionProcessingState.FAILED,
                VersionProcessingState.QUARANTINED,
            }:
                raise ValueError(f"version cannot be retried from {state.value}")
            connection.execute("DELETE FROM chunks WHERE version_id = ?", (version_id,))
            connection.execute("DELETE FROM sections WHERE version_id = ?", (version_id,))
            connection.execute(
                """
                UPDATE document_versions SET processing_state = ?, parser_revision = NULL
                WHERE id = ?
                """,
                (VersionProcessingState.PROCESSING.value, version_id),
            )
