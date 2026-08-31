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
from ragkb.infrastructure.sqlite import SQLiteDatabase


class SQLiteUploadRepository:
    revision = "sqlite-upload-repository:g1-v1"

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
    ) -> UploadSession:
        operation = f"create-upload-session:{space_id}"
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
            session_id = new_uuid7()
            quarantine_key = f"upload-sessions/{session_id}/{filename}"
            connection.execute(
                """
                INSERT INTO upload_sessions(
                    id, tenant_id, space_id, filename, expected_size, expected_sha256,
                    declared_mime, state, quarantine_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                return current.document_id, current.document_version_id
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
            version_id = new_uuid7()
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
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    session.tenant_id,
                    document_id,
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
            return document_id, version_id

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

    def save_canonical_document(self, document: Any) -> None:
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                "SELECT tenant_id FROM document_versions WHERE id = ?",
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
                        content_sha256, token_count, status
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'STAGED')
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
