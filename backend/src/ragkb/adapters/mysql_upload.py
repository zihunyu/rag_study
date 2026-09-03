"""MySQL-authoritative aggregate repository for the single-instance upload workflow."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.contracts.lifecycle import PublicationReadiness
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


def _empty_state() -> dict[str, Any]:
    return {
        "spaces": {},
        "sessions": {},
        "documents": {},
        "versions": {},
        "quality": {},
        "reviews": {},
        "lineage": {},
        "candidates": {},
        "idempotency": {},
    }


class MySQLUploadRepository:
    revision = "mysql-upload-aggregate:g4-v1"
    cleanable_partitions = frozenset({"original", "artifacts", "quarantine", "temp"})

    def __init__(
        self,
        control: MySQLControlPlaneAdapter,
        tenant_id: str,
        generation_id: str,
    ) -> None:
        self.control = control
        self.tenant_id = tenant_id
        self.generation_id = generation_id

    def _load(self, cursor: Any, *, locked: bool = False) -> dict[str, Any]:
        statement = (
            "SELECT state_json FROM upload_state_v2 WHERE tenant_id=%s FOR UPDATE"
            if locked
            else "SELECT state_json FROM upload_state_v2 WHERE tenant_id=%s"
        )
        cursor.execute(statement, (self.tenant_id,))
        row = cursor.fetchone()
        if row is None:
            return _empty_state()
        value = row["state_json"] if isinstance(row, dict) else row[0]
        loaded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(loaded, dict):
            raise ValueError("MYSQL_UPLOAD_STATE_INVALID")
        return loaded

    def _mutate[Result](self, callback: Callable[[dict[str, Any]], Result]) -> Result:
        connection = self.control.connect()
        try:
            cursor = connection.cursor()
            state = self._load(cursor, locked=True)
            result = callback(state)
            payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
            cursor.execute(
                """
                INSERT INTO upload_state_v2(tenant_id, state_json, updated_at)
                VALUES (%s, %s, NOW(6)) AS incoming
                ON DUPLICATE KEY UPDATE state_json=incoming.state_json, updated_at=NOW(6)
                """,
                (self.tenant_id, payload),
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _read(self) -> dict[str, Any]:
        connection = self.control.connect()
        try:
            return self._load(connection.cursor())
        finally:
            connection.close()

    @staticmethod
    def _session(data: dict[str, Any]) -> UploadSession:
        return UploadSession(
            **{
                **data,
                "state": UploadSessionState(str(data["state"])),
            }
        )

    def ensure_local_hierarchy(
        self,
        tenant_code: str,
        space_name: str,
        *,
        tenant_id_override: str | None = None,
        space_id_override: str | None = None,
    ) -> tuple[str, str]:
        if tenant_id_override and tenant_id_override != self.tenant_id:
            raise ValueError("TENANT_ID_OVERRIDE_MISMATCH")

        def mutate(state: dict[str, Any]) -> tuple[str, str]:
            space_id = space_id_override or next(iter(state["spaces"]), new_uuid7())
            existing = state["spaces"].get(space_id)
            if existing is not None and existing["name"] != space_name:
                raise ValueError("SPACE_ID_OVERRIDE_MISMATCH")
            state["spaces"][space_id] = {
                "id": space_id,
                "tenant_id": self.tenant_id,
                "name": space_name,
                "status": "ACTIVE",
                "tenant_code": tenant_code,
            }
            return self.tenant_id, space_id

        return self._mutate(mutate)

    def list_spaces(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._read()["spaces"].values()]

    def idempotency_response(
        self, operation: str, key: str, request_hash: str
    ) -> dict[str, Any] | None:
        item = self._read()["idempotency"].get(f"{operation}:{key}")
        if item is None:
            return None
        if item["request_hash"] != request_hash:
            raise IdempotencyConflictError("idempotency key reused with a different request hash")
        return dict(item["response"])

    def save_idempotency_response(
        self,
        operation: str,
        key: str,
        request_hash: str,
        resource_id: str,
        response: dict[str, Any],
    ) -> None:
        def mutate(state: dict[str, Any]) -> None:
            identity = f"{operation}:{key}"
            existing = state["idempotency"].get(identity)
            if existing is not None and existing["request_hash"] != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key reused with a different request hash"
                )
            state["idempotency"][identity] = {
                "request_hash": request_hash,
                "resource_id": resource_id,
                "response": response,
            }

        self._mutate(mutate)

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
        if tenant_id != self.tenant_id:
            raise ResourceNotFoundError(tenant_id)

        def mutate(state: dict[str, Any]) -> UploadSession:
            if space_id not in state["spaces"]:
                raise ResourceNotFoundError(space_id)
            operation = (
                f"create-version-upload-session:{target_document_id}"
                if target_document_id
                else f"create-upload-session:{space_id}"
            )
            identity = f"{operation}:{idempotency_key}"
            existing = state["idempotency"].get(identity)
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflictError(
                        "idempotency key reused with a different request hash"
                    )
                return self._session(state["sessions"][existing["resource_id"]])
            if target_document_id is not None:
                document = state["documents"].get(target_document_id)
                if document is None or document["state"] == DocumentState.DELETED.value:
                    raise ResourceNotFoundError(target_document_id)
                if int(document["row_version"]) != target_document_row_version:
                    raise OptimisticConcurrencyError(target_document_id)
            session_id = new_uuid7()
            data = {
                "id": session_id,
                "tenant_id": tenant_id,
                "space_id": space_id,
                "filename": filename,
                "expected_size": expected_size,
                "expected_sha256": expected_sha256,
                "declared_mime": declared_mime,
                "state": UploadSessionState.CREATED.value,
                "quarantine_key": f"upload-sessions/{session_id}/{filename}",
                "original_key": None,
                "detected_format": None,
                "detected_mime": None,
                "document_id": None,
                "document_version_id": None,
                "target_document_id": target_document_id,
                "target_document_row_version": target_document_row_version,
                "job_id": None,
                "error_code": None,
                "row_version": 1,
            }
            state["sessions"][session_id] = data
            state["idempotency"][identity] = {
                "request_hash": request_hash,
                "resource_id": session_id,
                "response": {"upload_session_id": session_id},
            }
            return self._session(data)

        return self._mutate(mutate)

    def get_session(self, session_id: str) -> UploadSession:
        data = self._read()["sessions"].get(session_id)
        if data is None:
            raise ResourceNotFoundError(session_id)
        return self._session(data)

    def update_session(
        self,
        session_id: str,
        expected_row_version: int,
        state: UploadSessionState,
        **fields: str | None,
    ) -> UploadSession:
        def mutate(data: dict[str, Any]) -> UploadSession:
            session = data["sessions"].get(session_id)
            if session is None:
                raise ResourceNotFoundError(session_id)
            if int(session["row_version"]) != expected_row_version:
                raise OptimisticConcurrencyError(session_id)
            transition_upload(UploadSessionState(session["state"]), state)
            session.update(fields)
            session["state"] = state.value
            session["row_version"] = int(session["row_version"]) + 1
            return self._session(session)

        return self._mutate(mutate)

    def ensure_document_version(self, session: UploadSession) -> tuple[str, str]:
        if not session.original_key or not session.detected_mime:
            raise ValueError("promoted session metadata is incomplete")

        def mutate(state: dict[str, Any]) -> tuple[str, str]:
            current = state["sessions"][session.id]
            if current.get("document_id") and current.get("document_version_id"):
                return str(current["document_id"]), str(current["document_version_id"])
            document_id = session.target_document_id or new_uuid7()
            document = state["documents"].get(document_id)
            if document is None:
                document = {
                    "id": document_id,
                    "tenant_id": self.tenant_id,
                    "source_id": "mysql-upload-source",
                    "external_key": session.filename,
                    "state": DocumentState.ACTIVE.value,
                    "current_version_id": None,
                    "row_version": 1,
                }
                state["documents"][document_id] = document
            elif int(document["row_version"]) != session.target_document_row_version:
                raise OptimisticConcurrencyError(document_id)
            versions = [
                item for item in state["versions"].values() if item["document_id"] == document_id
            ]
            version_id = new_uuid7()
            state["versions"][version_id] = {
                "id": version_id,
                "tenant_id": self.tenant_id,
                "document_id": document_id,
                "version_no": len(versions) + 1,
                "content_sha256": session.expected_sha256,
                "original_key": session.original_key,
                "mime_type": session.detected_mime,
                "processing_state": VersionProcessingState.PROCESSING.value,
                "publication_state": PublicationState.DRAFT.value,
                "parser_revision": None,
            }
            current["document_id"] = document_id
            current["document_version_id"] = version_id
            if session.target_document_id:
                document["row_version"] = int(document["row_version"]) + 1
            return document_id, version_id

        return self._mutate(mutate)

    def get_document(self, document_id: str) -> dict[str, Any]:
        document = self._read()["documents"].get(document_id)
        if document is None:
            raise ResourceNotFoundError(document_id)
        return dict(document)

    def get_versions(self, document_id: str) -> list[dict[str, Any]]:
        return sorted(
            [
                dict(item)
                for item in self._read()["versions"].values()
                if item["document_id"] == document_id
            ],
            key=lambda item: int(item["version_no"]),
        )

    def get_version(self, version_id: str) -> dict[str, Any]:
        version = self._read()["versions"].get(version_id)
        if version is None:
            raise ResourceNotFoundError(version_id)
        return dict(version)

    def list_original_keys(self, document_id: str) -> tuple[str, ...]:
        return tuple(str(item["original_key"]) for item in self.get_versions(document_id))

    def record_local_content(
        self,
        document_id: str,
        version_id: str | None,
        partition: str,
        storage_key: str,
        content_kind: str,
    ) -> None:
        def mutate(state: dict[str, Any]) -> None:
            state["lineage"].setdefault(document_id, []).append(
                {
                    "version_id": version_id,
                    "partition": partition,
                    "storage_key": storage_key,
                    "content_kind": content_kind,
                }
            )

        self._mutate(mutate)

    def list_local_content_lineage(self, document_id: str) -> tuple[tuple[str, str], ...]:
        state = self._read()
        values = {
            (str(item["partition"]), str(item["storage_key"]))
            for item in state["lineage"].get(document_id, [])
        }
        values.update(("original", key) for key in self.list_original_keys(document_id))
        return tuple(sorted(values))

    def save_canonical_document(self, document: Any) -> None:
        def mutate(state: dict[str, Any]) -> None:
            version = state["versions"].get(document.document_version_id)
            if version is None:
                raise ResourceNotFoundError(document.document_version_id)
            version["processing_state"] = VersionProcessingState.VALIDATED.value
            version["parser_revision"] = document.parser_revision
            state["candidates"][document.document_version_id] = {
                "document_id": version["document_id"],
                "generation_id": self.generation_id,
                "projection_state": "BUILDING",
                "required_watermark": 1,
                "observed_watermark": 0,
                "expected_checksum": version["content_sha256"],
                "observed_checksum": "",
            }

        self._mutate(mutate)

    def save_chunking_result(self, document: Any, result: Any) -> None:
        del document
        if not result.chunks:
            raise ValueError("CHUNKING_RESULT_EMPTY")

    def mark_index_ready(self, version_id: str) -> None:
        def mutate(state: dict[str, Any]) -> None:
            candidate = state["candidates"].get(version_id)
            version = state["versions"].get(version_id)
            if candidate is None or version is None:
                raise ResourceNotFoundError(version_id)
            candidate.update(
                projection_state="STAGED",
                observed_watermark=candidate["required_watermark"],
                observed_checksum=version["content_sha256"],
            )

        self._mutate(mutate)

    def save_quality_report(self, report: DocumentQualityReport) -> None:
        def mutate(state: dict[str, Any]) -> None:
            state["quality"][report.document_version_id] = {
                "version_id": report.document_version_id,
                "source_format": report.source_format,
                "parser_revision": report.parser_revision,
                "node_count": report.node_count,
                "locator_coverage": report.locator_coverage,
                "issue_codes": list(report.issue_codes),
                "disposition": report.disposition.value,
                "real_acceptance": False,
            }

        self._mutate(mutate)

    def get_quality_report(self, version_id: str) -> dict[str, Any]:
        item = self._read()["quality"].get(version_id)
        if item is None:
            raise ResourceNotFoundError(version_id)
        return dict(item)

    def save_document_review(
        self,
        *,
        version_id: str,
        reviewer_id: str,
        decision: str,
        comment: str,
        quality_revision: str,
        security_revision: str | None,
        security_projection: dict[str, Any] | None,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        operation = f"review-document-version:{version_id}"

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            identity = f"{operation}:{idempotency_key}"
            existing = state["idempotency"].get(identity)
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflictError("review idempotency conflict")
                return dict(existing["response"])
            if version_id not in state["quality"]:
                raise ResourceNotFoundError(version_id)
            result = {
                "review_id": new_uuid7(),
                "document_version_id": version_id,
                "reviewer_id": reviewer_id,
                "decision": decision,
                "comment": comment,
                "quality_revision": quality_revision,
                "security_revision": security_revision,
                "security_projection": security_projection,
                "real_acceptance": False,
            }
            state["reviews"].setdefault(version_id, []).append(result)
            state["idempotency"][identity] = {
                "request_hash": request_hash,
                "resource_id": result["review_id"],
                "response": result,
            }
            return result

        return self._mutate(mutate)

    def publication_readiness(
        self, document_id: str, version_id: str, *, rollback: bool = False
    ) -> PublicationReadiness:
        state = self._read()
        document = state["documents"].get(document_id)
        version = state["versions"].get(version_id)
        quality = state["quality"].get(version_id)
        reviews = state["reviews"].get(version_id, [])
        review = reviews[-1] if reviews else None
        candidate = state["candidates"].get(version_id)
        error: str | None = None
        if document is None or version is None:
            error = "PUBLICATION_TARGET_NOT_FOUND"
        elif version["processing_state"] != VersionProcessingState.VALIDATED.value:
            error = "PUBLICATION_VERSION_NOT_VALIDATED"
        elif quality is None:
            error = "PUBLICATION_QUALITY_REPORT_MISSING"
        elif review is None or review["decision"] != "APPROVED":
            error = "PUBLICATION_REVIEW_NOT_APPROVED"
        elif not review.get("security_projection"):
            error = "PUBLICATION_SECURITY_REVIEW_REQUIRED"
        elif candidate is None:
            error = "PUBLICATION_CANDIDATE_MISSING"
        elif candidate["projection_state"] != ("RETIRED" if rollback else "STAGED"):
            error = "PUBLICATION_PROJECTION_NOT_STAGED"
        elif candidate["generation_id"] != self.generation_id:
            error = "PUBLICATION_GENERATION_MISMATCH"
        elif int(candidate["observed_watermark"]) < int(candidate["required_watermark"]):
            error = "PUBLICATION_WATERMARK_NOT_READY"
        elif candidate["observed_checksum"] != version["content_sha256"]:
            error = "PUBLICATION_CHECKSUM_MISMATCH"
        return PublicationReadiness(
            ready=error is None,
            document_id=document_id,
            version_id=version_id,
            generation_id=str(candidate["generation_id"]) if candidate else "",
            projection_state=str(candidate["projection_state"]) if candidate else "",
            required_watermark=int(candidate["required_watermark"]) if candidate else 0,
            observed_watermark=int(candidate["observed_watermark"]) if candidate else 0,
            expected_checksum=str(version["content_sha256"]) if version else "",
            observed_checksum=str(candidate["observed_checksum"]) if candidate else "",
            document_row_version=int(document["row_version"]) if document else -1,
            error_code=error,
        )

    def set_document_current_version(self, document_id: str, version_id: str) -> None:
        def mutate(state: dict[str, Any]) -> None:
            document = state["documents"].get(document_id)
            if document is None:
                raise ResourceNotFoundError(document_id)
            previous = document.get("current_version_id")
            if previous == version_id:
                return
            document["current_version_id"] = version_id
            document["row_version"] = int(document["row_version"]) + 1
            state["versions"][version_id]["publication_state"] = PublicationState.SERVING.value
            if previous and previous in state["versions"] and previous != version_id:
                state["versions"][previous]["publication_state"] = PublicationState.SUPERSEDED.value

        self._mutate(mutate)

    def _mark_version(self, version_id: str, state_value: VersionProcessingState) -> None:
        def mutate(state: dict[str, Any]) -> None:
            version = state["versions"].get(version_id)
            if version is None:
                raise ResourceNotFoundError(version_id)
            version["processing_state"] = state_value.value

        self._mutate(mutate)

    def mark_version_quarantined(self, version_id: str, parser_revision: str) -> None:
        del parser_revision
        self._mark_version(version_id, VersionProcessingState.QUARANTINED)

    def mark_version_failed(self, version_id: str, parser_revision: str) -> None:
        del parser_revision
        self._mark_version(version_id, VersionProcessingState.FAILED)

    def mark_version_cancelled(self, version_id: str) -> None:
        self._mark_version(version_id, VersionProcessingState.CANCELLED)

    def mark_version_processing(self, version_id: str) -> None:
        self._mark_version(version_id, VersionProcessingState.PROCESSING)
