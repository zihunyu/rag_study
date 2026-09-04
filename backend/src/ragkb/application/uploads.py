"""Quarantine-to-original upload workflow with idempotency and recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterable
from dataclasses import asdict
from typing import Any

from ragkb.contracts.jobs import PersistentJobQueuePort
from ragkb.contracts.ports import ContentStoragePort, StorageIntegrityError
from ragkb.contracts.uploads import UploadRepositoryPort
from ragkb.domain.state_machines import UploadSessionState
from ragkb.domain.uploads import OptimisticConcurrencyError, UploadSession
from ragkb.engineering_security.file_validation import FileValidationError, UploadFileValidator
from ragkb.engineering_security.malware import MalwareScanPort


class UploadStateError(RuntimeError):
    pass


class MalwareRejectedError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class UploadService:
    revision = "upload-service:g1-v1"

    def __init__(
        self,
        repository: UploadRepositoryPort,
        queue: PersistentJobQueuePort,
        storage: ContentStoragePort,
        validator: UploadFileValidator,
        malware_scanner: MalwareScanPort,
        tenant_id: str,
        queue_max_attempts: int = 3,
        quarantine_max_bytes: int | None = None,
        max_concurrent_streams: int = 4,
    ) -> None:
        if max_concurrent_streams < 1:
            raise ValueError("UPLOAD_STREAM_CONCURRENCY_INVALID")
        self.repository = repository
        self.queue = queue
        self.storage = storage
        self.validator = validator
        self.malware_scanner = malware_scanner
        self.tenant_id = tenant_id
        self.queue_max_attempts = queue_max_attempts
        self.quarantine_max_bytes = quarantine_max_bytes or validator.max_size_bytes * 10
        self._stream_slots = asyncio.Semaphore(max_concurrent_streams)

    @staticmethod
    def request_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def create_session(
        self,
        *,
        space_id: str,
        filename: str,
        expected_size: int,
        expected_sha256: str,
        declared_mime: str,
        idempotency_key: str,
        target_document_id: str | None = None,
        target_document_row_version: int | None = None,
    ) -> UploadSession:
        safe_filename = self.validator.validate_filename(filename)
        if expected_size < 0:
            raise FileValidationError("DOC_INVALID_SIZE", "expected_size must be non-negative")
        if expected_size > self.validator.max_size_bytes:
            raise FileValidationError("DOC_SIZE_LIMIT", "file exceeds configured size limit")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise FileValidationError(
                "DOC_INVALID_HASH", "expected_sha256 must be a SHA-256 hex digest"
            )
        payload = {
            "space_id": space_id,
            "filename": safe_filename,
            "expected_size": expected_size,
            "expected_sha256": expected_sha256.casefold(),
            "declared_mime": declared_mime,
            "target_document_id": target_document_id,
            "target_document_row_version": target_document_row_version,
        }
        return self.repository.create_upload_session(
            tenant_id=self.tenant_id,
            space_id=space_id,
            filename=safe_filename,
            expected_size=expected_size,
            expected_sha256=expected_sha256.casefold(),
            declared_mime=declared_mime,
            idempotency_key=idempotency_key,
            request_hash=self.request_hash(payload),
            target_document_id=target_document_id,
            target_document_row_version=target_document_row_version,
        )

    def create_version_session(
        self,
        *,
        document_id: str,
        expected_document_row_version: int,
        space_id: str,
        filename: str,
        expected_size: int,
        expected_sha256: str,
        declared_mime: str,
        idempotency_key: str,
    ) -> UploadSession:
        return self.create_session(
            space_id=space_id,
            filename=filename,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            declared_mime=declared_mime,
            idempotency_key=idempotency_key,
            target_document_id=document_id,
            target_document_row_version=expected_document_row_version,
        )

    def upload_content(
        self, session_id: str, content: bytes, *, expected_row_version: int
    ) -> UploadSession:
        session = self.repository.get_session(session_id)
        if session.row_version != expected_row_version:
            raise OptimisticConcurrencyError(session_id)
        if session.state not in {UploadSessionState.CREATED, UploadSessionState.FAILED}:
            raise UploadStateError(f"cannot upload content in state {session.state.value}")
        if len(content) > self.validator.max_size_bytes:
            raise FileValidationError("DOC_SIZE_LIMIT", "file exceeds configured size limit")
        if len(content) != session.expected_size:
            raise FileValidationError("DOC_SIZE_MISMATCH", "uploaded size differs from declaration")
        if hashlib.sha256(content).hexdigest() != session.expected_sha256:
            raise FileValidationError("DOC_HASH_MISMATCH", "uploaded hash differs from declaration")
        self.storage.write_bytes("quarantine", session.quarantine_key, content)
        return self.repository.update_session(
            session.id,
            session.row_version,
            UploadSessionState.UPLOADED,
            error_code=None,
        )

    async def upload_content_stream(
        self,
        session_id: str,
        chunks: AsyncIterable[bytes],
        *,
        expected_row_version: int,
        content_length: int | None,
    ) -> UploadSession:
        session = self.repository.get_session(session_id)
        if session.row_version != expected_row_version:
            raise OptimisticConcurrencyError(session_id)
        if session.state not in {UploadSessionState.CREATED, UploadSessionState.FAILED}:
            raise UploadStateError(f"cannot upload content in state {session.state.value}")
        if content_length is not None:
            if content_length < 0:
                raise FileValidationError(
                    "DOC_CONTENT_LENGTH_INVALID", "Content-Length must be non-negative"
                )
            if content_length > self.validator.max_size_bytes:
                raise FileValidationError("DOC_SIZE_LIMIT", "file exceeds configured size limit")
            if content_length != session.expected_size:
                raise FileValidationError(
                    "DOC_SIZE_MISMATCH", "Content-Length differs from declared size"
                )
        async with self._stream_slots:
            try:
                written = await self.storage.write_stream(
                    "quarantine",
                    session.quarantine_key,
                    chunks,
                    max_bytes=self.validator.max_size_bytes,
                    quota_bytes=self.quarantine_max_bytes,
                    content_length=content_length,
                )
            except StorageIntegrityError as error:
                messages = {
                    "DOC_SIZE_LIMIT": "file exceeds configured size limit",
                    "UPLOAD_QUARANTINE_QUOTA_EXCEEDED": "upload quarantine capacity exhausted",
                }
                raise FileValidationError(
                    error.code, messages.get(error.code, "streaming upload failed")
                ) from error
        if written.size != session.expected_size:
            self.storage.delete("quarantine", session.quarantine_key)
            raise FileValidationError("DOC_SIZE_MISMATCH", "uploaded size differs from declaration")
        if written.sha256 != session.expected_sha256:
            self.storage.delete("quarantine", session.quarantine_key)
            raise FileValidationError("DOC_HASH_MISMATCH", "uploaded hash differs from declaration")
        return self.repository.update_session(
            session.id,
            session.row_version,
            UploadSessionState.UPLOADED,
            error_code=None,
        )

    def _complete_response(self, session: UploadSession) -> dict[str, Any]:
        if not session.document_id or not session.document_version_id or not session.job_id:
            raise UploadStateError("completed session is missing result identifiers")
        return {
            "upload_session_id": session.id,
            "document_id": session.document_id,
            "document_version_id": session.document_version_id,
            "job_id": session.job_id,
            "status": "QUEUED",
            "real_acceptance": False,
        }

    def complete(
        self,
        session_id: str,
        *,
        expected_row_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation = f"complete-upload:{session_id}"
        command_hash = self.request_hash({"upload_session_id": session_id})
        replay = self.repository.idempotency_response(operation, idempotency_key, command_hash)
        if replay:
            return replay
        session = self.repository.get_session(session_id)
        if session.row_version != expected_row_version:
            raise OptimisticConcurrencyError(session_id)
        if session.state is UploadSessionState.COMPLETED:
            response = self._complete_response(session)
            self.repository.save_idempotency_response(
                operation, idempotency_key, command_hash, session.id, response
            )
            return response
        if session.state not in {
            UploadSessionState.UPLOADED,
            UploadSessionState.VALIDATED,
            UploadSessionState.PROMOTED,
        }:
            raise UploadStateError(f"cannot complete session in state {session.state.value}")
        if session.state is UploadSessionState.UPLOADED:
            quarantine_path = self.storage.path_for("quarantine", session.quarantine_key)
            try:
                detected = self.validator.inspect(
                    quarantine_path,
                    filename=session.filename,
                    expected_size=session.expected_size,
                    expected_sha256=session.expected_sha256,
                    declared_mime=session.declared_mime,
                )
                verdict = self.malware_scanner.scan(quarantine_path)
                if not verdict.clean:
                    raise MalwareRejectedError(verdict.reason_code or "MALWARE_REJECTED")
            except (FileValidationError, MalwareRejectedError) as error:
                code = error.code if isinstance(error, FileValidationError) else error.reason_code
                self.repository.update_session(
                    session.id,
                    session.row_version,
                    UploadSessionState.FAILED,
                    error_code=code,
                )
                raise
            session = self.repository.update_session(
                session.id,
                session.row_version,
                UploadSessionState.VALIDATED,
                detected_format=detected.source_format,
                detected_mime=detected.mime_type,
                error_code=None,
            )
        if session.state is UploadSessionState.VALIDATED:
            original_key = (
                (
                    f"tenant/{session.tenant_id}/space/{session.space_id}/"
                    f"document/{session.target_document_id}/upload/{session.id}/"
                    f"original/{session.filename}"
                )
                if session.target_document_id
                else (
                    f"tenant/{session.tenant_id}/space/{session.space_id}/"
                    f"document/{session.id}/version/1/original/{session.filename}"
                )
            )
            try:
                self.storage.promote(
                    "quarantine",
                    session.quarantine_key,
                    original_key,
                    session.expected_sha256,
                )
            except StorageIntegrityError as error:
                self.repository.update_session(
                    session.id,
                    session.row_version,
                    UploadSessionState.FAILED,
                    error_code=error.code,
                )
                raise FileValidationError(
                    error.code, "existing original does not match the validated upload"
                ) from error
            session = self.repository.update_session(
                session.id,
                session.row_version,
                UploadSessionState.PROMOTED,
                original_key=original_key,
            )
        document_id, version_id = self.repository.ensure_document_version(session)
        session = self.repository.get_session(session.id)
        queue_hash = self.request_hash(
            {
                "document_version_id": version_id,
                "content_sha256": session.expected_sha256,
                "upload_revision": self.revision,
            }
        )
        job = self.queue.enqueue(
            "process_document",
            {
                "tenant_id": session.tenant_id,
                "space_id": session.space_id,
                "document_id": document_id,
                "document_version_id": version_id,
                "source_format": session.detected_format,
                "real_acceptance": False,
            },
            f"ingest:{version_id}",
            queue_hash,
            max_attempts=self.queue_max_attempts,
        )
        completed = self.repository.update_session(
            session.id,
            session.row_version,
            UploadSessionState.COMPLETED,
            document_id=document_id,
            document_version_id=version_id,
            job_id=job.id,
        )
        response = self._complete_response(completed)
        self.repository.save_idempotency_response(
            operation, idempotency_key, command_hash, session.id, response
        )
        return response

    def abort(
        self,
        session_id: str,
        *,
        expected_row_version: int,
        idempotency_key: str,
    ) -> UploadSession:
        operation = f"abort-upload:{session_id}"
        command_hash = self.request_hash({"upload_session_id": session_id})
        replay = self.repository.idempotency_response(operation, idempotency_key, command_hash)
        if replay:
            return self.repository.get_session(str(replay["upload_session_id"]))
        session = self.repository.get_session(session_id)
        if session.row_version != expected_row_version:
            raise OptimisticConcurrencyError(session_id)
        if session.state not in {
            UploadSessionState.CREATED,
            UploadSessionState.UPLOADED,
            UploadSessionState.FAILED,
        }:
            raise UploadStateError(f"cannot abort session in state {session.state.value}")
        self.storage.delete("quarantine", session.quarantine_key)
        aborted = self.repository.update_session(
            session.id, session.row_version, UploadSessionState.ABORTED
        )
        self.repository.save_idempotency_response(
            operation,
            idempotency_key,
            command_hash,
            session.id,
            {"upload_session_id": session.id},
        )
        return aborted

    @staticmethod
    def session_dict(session: UploadSession) -> dict[str, Any]:
        result = asdict(session)
        result["state"] = session.state.value
        return result
