"""CanonicalDocument and SourceLocator v1 contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"
    AUDIO = "audio"


@dataclass(frozen=True)
class SourceLocator:
    page: int | None = None
    slide: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    row: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    char_range: tuple[int, int] | None = None
    start_time: float | None = None
    end_time: float | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            raise ValueError("page is one-based")
        if self.slide is not None and self.slide < 1:
            raise ValueError("slide is one-based")
        if self.row is not None and self.row < 1:
            raise ValueError("row is one-based")
        if self.char_range is not None:
            start, end = self.char_range
            if start < 0 or end < start:
                raise ValueError("char_range must be ordered and non-negative")
        if self.start_time is not None and self.start_time < 0:
            raise ValueError("start_time must be non-negative")
        if self.end_time is not None:
            if self.end_time < 0 or (
                self.start_time is not None and self.end_time < self.start_time
            ):
                raise ValueError("end_time must be after start_time")
        if not any(
            value is not None
            for value in (
                self.page,
                self.slide,
                self.sheet,
                self.row,
                self.bbox,
                self.char_range,
                self.start_time,
            )
        ):
            raise ValueError("at least one source location is required")

    def to_dict(self) -> dict[str, object]:
        values = {
            "page": self.page,
            "slide": self.slide,
            "sheet": self.sheet,
            "cell_range": self.cell_range,
            "row": self.row,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "char_range": list(self.char_range) if self.char_range is not None else None,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class CanonicalNode:
    node_id: str
    parent_node_id: str | None
    node_type: NodeType
    original_text: str
    display_text: str
    locator: SourceLocator
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("node_id is required")
        if not self.original_text.strip():
            raise ValueError("canonical nodes cannot be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "parent_node_id": self.parent_node_id,
            "type": self.node_type.value,
            "original_text": self.original_text,
            "display_text": self.display_text,
            "locator": self.locator.to_dict(),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class CanonicalDocument:
    document_version_id: str
    language: str
    source_format: str
    nodes: tuple[CanonicalNode, ...]
    parser_revision: str
    normalization_revision: str
    content_checksum: str
    tables: tuple[dict[str, Any], ...] = ()
    media_refs: tuple[dict[str, Any], ...] = ()
    quality_issues: tuple[str, ...] = ()
    contract_version: str = "1.0"
    real_acceptance: bool = False

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError("CanonicalDocument must have at least one node")
        if len(self.content_checksum) != 64:
            raise ValueError("content_checksum must be a SHA-256 hex digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "document_version_id": self.document_version_id,
            "language": self.language,
            "source_format": self.source_format,
            "nodes": [node.to_dict() for node in self.nodes],
            "tables": list(self.tables),
            "media_refs": list(self.media_refs),
            "parser_revision": self.parser_revision,
            "normalization_revision": self.normalization_revision,
            "content_checksum": self.content_checksum,
            "quality_issues": list(self.quality_issues),
            "real_acceptance": self.real_acceptance,
        }
