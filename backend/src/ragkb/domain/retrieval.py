"""Vendor-independent G2 indexing and retrieval contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ragkb.domain.errors import RetrievalFailClosed

SearchChannel = Literal["bm25", "dense"]


@dataclass(frozen=True)
class SearchContext:
    tenant_id: str
    space_ids: tuple[str, ...]
    subject_scope_tokens: tuple[str, ...]
    clearance_level: int
    as_of_epoch: int
    active_generation_id: str
    active_permission_revision: int
    required_security_watermark: int

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.space_ids or not self.active_generation_id:
            raise ValueError("tenant, spaces and active generation are required")
        if self.clearance_level < 0 or self.as_of_epoch < 0:
            raise ValueError("clearance and as-of time must be non-negative")


@dataclass(frozen=True)
class IndexCandidate:
    chunk_id: str
    document_version_id: str
    parent_chunk_id: str | None
    channel: SearchChannel
    rank: int
    score: float


@dataclass(frozen=True)
class AuthorizedChunk:
    chunk_id: str
    tenant_id: str
    space_id: str
    document_id: str
    document_version_id: str
    parent_chunk_id: str | None
    display_text: str
    retrieval_text: str
    locator: dict[str, Any]
    content_checksum: str
    visibility: Literal["TENANT", "RESTRICTED"]
    acl_scope_tokens: tuple[str, ...]
    classification_level: int
    lifecycle_projection: str
    valid_from_epoch: int
    valid_to_epoch: int
    permission_revision: int
    current_version: bool


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    document_id: str
    document_version_id: str
    text: str
    locator: dict[str, Any]
    fused_score: float
    rerank_position: int
    channels: tuple[SearchChannel, ...]
    parent_chunk_id: str | None = None
    parent_text: str | None = None
    valid_from_epoch: int = 0
    valid_to_epoch: int = 0
    permission_revision: int = 0
    current_version: bool = True


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    observed_security_watermark: int
    real_acceptance: bool = False
    degraded: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalRelease:
    tenant_id: str
    space_id: str
    active_generation_id: str
    active_permission_revision: int
    security_watermark: int

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.space_id or not self.active_generation_id:
            raise ValueError("retrieval release identifiers are required")
        if self.active_permission_revision < 0 or self.security_watermark < 0:
            raise ValueError("retrieval release revisions must be non-negative")


class SecurityWatermarkNotReady(RetrievalFailClosed):
    pass
