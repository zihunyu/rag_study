"""Repository port for upload, document and version application use cases."""

from __future__ import annotations

from typing import Any, Protocol

from ragkb.domain.pagination import PageKey, RepositoryPage
from ragkb.domain.retrieval import SearchContext
from ragkb.domain.state_machines import UploadSessionState
from ragkb.domain.uploads import UploadSession
from ragkb.domain.validation import DocumentQualityReport


class UploadRepositoryPort(Protocol):
    def get_document(self, document_id: str) -> dict[str, Any]: ...

    def get_versions(self, document_id: str) -> list[dict[str, Any]]: ...

    def get_document_space(self, document_id: str) -> str: ...

    def create_space(self, tenant_id: str, name: str) -> dict[str, str]: ...

    def list_spaces(self) -> list[dict[str, str]]: ...

    def get_space(self, space_id: str) -> dict[str, str]: ...

    def list_documents(self, space_id: str) -> list[dict[str, Any]]: ...

    def list_documents_page(
        self,
        space_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        current_only: bool = False,
        after: PageKey | None = None,
    ) -> RepositoryPage: ...

    def list_chunks_page(
        self,
        version_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        context: SearchContext | None = None,
        preview: bool = False,
        after: PageKey | None = None,
    ) -> RepositoryPage: ...

    def list_chunk_ids(
        self, version_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[str]: ...

    def list_chunks(
        self,
        version_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        context: SearchContext | None = None,
        preview: bool = False,
    ) -> list[dict[str, Any]]: ...

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
    ) -> UploadSession: ...

    def idempotency_response(
        self, operation: str, key: str, request_hash: str
    ) -> dict[str, Any] | None: ...

    def save_idempotency_response(
        self,
        operation: str,
        key: str,
        request_hash: str,
        resource_id: str,
        response: dict[str, Any],
    ) -> None: ...

    def get_session(self, session_id: str) -> UploadSession: ...

    def update_session(
        self,
        session_id: str,
        expected_row_version: int,
        state: UploadSessionState,
        **fields: str | None,
    ) -> UploadSession: ...

    def ensure_document_version(self, session: UploadSession) -> tuple[str, str]: ...

    def get_version(self, version_id: str) -> dict[str, Any]: ...

    def save_canonical_document(self, document: Any) -> None: ...

    def save_quality_report(self, report: DocumentQualityReport) -> None: ...

    def ingestion_complete(self, version_id: str) -> bool: ...

    def record_local_content(
        self,
        document_id: str,
        version_id: str | None,
        partition: str,
        storage_key: str,
        content_kind: str,
    ) -> None: ...

    def mark_version_quarantined(self, version_id: str, parser_revision: str) -> None: ...

    def mark_version_failed(self, version_id: str, parser_revision: str) -> None: ...

    def mark_version_cancelled(self, version_id: str) -> None: ...

    def mark_version_processing(self, version_id: str) -> None: ...
