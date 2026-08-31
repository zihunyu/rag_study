"""G1 knowledge hierarchy entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ragkb.domain.documents import SourceLocator
from ragkb.domain.state_machines import DocumentState, PublicationState, VersionProcessingState


@dataclass(frozen=True)
class Tenant:
    id: str
    code: str
    status: str = "ACTIVE"


@dataclass(frozen=True)
class KnowledgeSpace:
    id: str
    tenant_id: str
    name: str
    status: str = "ACTIVE"


@dataclass(frozen=True)
class Corpus:
    id: str
    tenant_id: str
    space_id: str
    name: str


@dataclass(frozen=True)
class Source:
    id: str
    tenant_id: str
    corpus_id: str
    kind: str
    external_key: str


@dataclass(frozen=True)
class Document:
    id: str
    tenant_id: str
    source_id: str
    external_key: str
    state: DocumentState = DocumentState.ACTIVE
    current_version_id: str | None = None
    row_version: int = 1


@dataclass(frozen=True)
class DocumentVersion:
    id: str
    tenant_id: str
    document_id: str
    version_no: int
    content_sha256: str
    original_key: str
    mime_type: str
    processing_state: VersionProcessingState = VersionProcessingState.PROCESSING
    publication_state: PublicationState = PublicationState.DRAFT
    created_at: datetime | None = None


@dataclass(frozen=True)
class Section:
    id: str
    tenant_id: str
    version_id: str
    ordinal: int
    title: str
    path: str
    parent_id: str | None = None


@dataclass(frozen=True)
class Chunk:
    id: str
    tenant_id: str
    version_id: str
    section_id: str
    ordinal: int
    original_text: str
    display_text: str
    retrieval_text: str
    locator: SourceLocator
    content_sha256: str
    token_count: int
    parent_chunk_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
