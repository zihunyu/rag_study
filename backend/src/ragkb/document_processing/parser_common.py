"""Shared canonical-document construction helpers for format parsers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.ids import new_uuid7


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_document(
    path: Path,
    document_version_id: str,
    source_format: str,
    parser_revision: str,
    nodes: list[CanonicalNode],
    *,
    tables: list[dict[str, Any]] | None = None,
    quality_issues: list[str] | None = None,
    real_acceptance: bool = False,
) -> CanonicalDocument:
    return CanonicalDocument(
        document_version_id=document_version_id,
        language="und",
        source_format=source_format,
        nodes=tuple(nodes),
        parser_revision=parser_revision,
        normalization_revision="normalization:g1-v1",
        content_checksum=checksum(path),
        tables=tuple(tables or []),
        quality_issues=tuple(quality_issues or []),
        real_acceptance=real_acceptance,
    )


def text_nodes(
    texts: list[str],
    *,
    locator_factory: Callable[[int, int, int], SourceLocator],
    node_types: Sequence[NodeType] | None = None,
    metadata: Sequence[dict[str, Any]] | None = None,
) -> list[CanonicalNode]:
    nodes: list[CanonicalNode] = []
    offset = 0
    for index, raw in enumerate(texts):
        text = raw.strip()
        if not text:
            continue
        nodes.append(
            CanonicalNode(
                node_id=new_uuid7(),
                parent_node_id=None,
                node_type=node_types[index] if node_types is not None else NodeType.PARAGRAPH,
                original_text=text,
                display_text=text,
                locator=locator_factory(index, offset, len(text)),
                metadata={"ordinal": index, **(metadata[index] if metadata is not None else {})},
            )
        )
        offset += len(text) + 1
    return nodes
