"""Vendor-independent G2 indexing and retrieval contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ragkb.domain.errors import RetrievalFailClosed

SearchChannel = Literal["bm25", "dense"]


@dataclass(frozen=True)
class SecurityProjection:
    """Immutable, review-bound authorization facts copied into every search projection."""

    visibility: Literal["TENANT", "RESTRICTED"]
    classification_level: int
    acl_scope_tokens: tuple[str, ...]
    lifecycle_projection: str
    permission_revision: int
    valid_from_epoch: int
    valid_to_epoch: int = 0

    def __post_init__(self) -> None:
        if self.classification_level < 0 or self.classification_level > 3:
            raise ValueError("classification level must be in 0..3")
        if self.permission_revision < 1:
            raise ValueError("permission revision must be positive")
        if self.valid_from_epoch < 0 or self.valid_to_epoch < 0:
            raise ValueError("security validity must be non-negative")
        if self.valid_to_epoch and self.valid_to_epoch <= self.valid_from_epoch:
            raise ValueError("security validity end must be after its start")
        if self.visibility == "RESTRICTED" and not self.acl_scope_tokens:
            # Empty ACL is deliberately allowed only for the unapproved fail-closed projection.
            if self.lifecycle_projection not in {"DRAFT", "STAGED"}:
                raise ValueError("serving restricted content requires at least one ACL scope")
        if self.visibility == "TENANT" and self.acl_scope_tokens:
            raise ValueError("tenant-visible content cannot carry restricted ACL scopes")

    @classmethod
    def unapproved(cls, *, permission_revision: int = 1, now: int = 0) -> SecurityProjection:
        return cls(
            visibility="RESTRICTED",
            classification_level=3,
            acl_scope_tokens=(),
            lifecycle_projection="STAGED",
            permission_revision=permission_revision,
            valid_from_epoch=now,
        )


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
    vector_pk: str | None = None


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
    index_generation_id: str = ""


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
    display_text: str = ""
    retrieval_text: str = ""
    generation_context: str = ""


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
