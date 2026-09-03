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
    kind: str = "paragraph"
    chunking_revision: str = "node-per-chunk:g1-v1"
    tokenizer_id: str = "whitespace-estimate:g1-v1"
    status: str = "STAGED"
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("chunk ordinal must be non-negative")
        if not self.original_text.strip() or not self.retrieval_text.strip():
            raise ValueError("chunk text must be non-empty")
        if self.token_count < 1:
            raise ValueError("chunk token_count must be positive")
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in self.content_sha256
        ):
            raise ValueError("chunk content_sha256 must be a SHA-256 hex digest")
        if not self.kind or not self.chunking_revision or not self.tokenizer_id:
            raise ValueError("chunk kind and revision identifiers are required")
