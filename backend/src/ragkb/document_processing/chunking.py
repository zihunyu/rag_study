"""Token-aware, structure-aware chunking between parsing and embedding."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.entities import Chunk

_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)


def token_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return deterministic approximate token spans for Latin and CJK text."""

    return tuple((match.start(), match.end()) for match in _TOKEN_PATTERN.finditer(text))


def count_tokens(text: str) -> int:
    return len(token_spans(text))


@dataclass(frozen=True)
class ChunkingConfig:
    strategy: str = "structure"
    target_tokens: int = 600
    overlap_tokens: int = 80
    min_tokens: int = 20
    max_tokens: int = 800
    parent_max_tokens: int = 1200

    def __post_init__(self) -> None:
        if self.strategy not in {"token", "structure", "semantic"}:
            raise ValueError("unsupported chunk strategy")
        if self.target_tokens < 1 or self.max_tokens < self.target_tokens:
            raise ValueError("chunk token limits are invalid")
        if self.overlap_tokens < 0 or self.overlap_tokens >= self.target_tokens:
            raise ValueError("chunk overlap must be below target size")
        if self.min_tokens < 1 or self.min_tokens > self.target_tokens:
            raise ValueError("minimum chunk size is invalid")
        if self.parent_max_tokens < self.target_tokens:
            raise ValueError("parent chunk size must cover a child chunk")


@dataclass(frozen=True)
class ChunkingResult:
    chunks: tuple[Chunk, ...]
    parent_chunks: tuple[Chunk, ...]
    revision: str


def _stable_id(prefix: str, *parts: str) -> str:
    value = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(value, usedforsecurity=False).hexdigest()[:24]}"


def _locator_for_slice(node: CanonicalNode, start: int, end: int) -> SourceLocator:
    locator = node.locator
    char_range = locator.char_range
    if char_range is not None:
        char_range = (char_range[0] + start, char_range[0] + end)
    return SourceLocator(
        page=locator.page,
        slide=locator.slide,
        sheet=locator.sheet,
        cell_range=locator.cell_range,
        row=locator.row,
        bbox=locator.bbox,
        char_range=char_range,
        start_time=locator.start_time,
        end_time=locator.end_time,
    )


def _windows(text: str, config: ChunkingConfig) -> tuple[tuple[str, int, int], ...]:
    spans = token_spans(text)
    if not spans:
        return ()
    size = min(config.target_tokens, config.max_tokens)
    step = size - config.overlap_tokens
    windows: list[tuple[str, int, int]] = []
    token_start = 0
    while token_start < len(spans):
        token_end = min(len(spans), token_start + size)
        start = spans[token_start][0]
        end = spans[token_end - 1][1]
        piece = text[start:end].strip()
        if piece:
            if windows and token_end == len(spans) and count_tokens(piece) < config.min_tokens:
                previous, previous_start, _ = windows.pop()
                merged = f"{previous}\n{text[start:end].strip()}"
                windows.append((merged, previous_start, end))
            else:
                windows.append((piece, start, end))
        if token_end == len(spans):
            break
        token_start += step
    return tuple(windows)


class TokenAwareChunker:
    """Create searchable children and larger parent context chunks with stable IDs."""

    tokenizer_id = "unicode-cjk-tokenizer:v1"

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()
        self.revision = (
            f"token-aware:{self.config.strategy}:"
            f"{self.config.target_tokens}:{self.config.overlap_tokens}:v1"
        )

    def _section(
        self, node: CanonicalNode, heading: str, document_version_id: str
    ) -> tuple[str, str]:
        explicit = str(node.metadata.get("section_path", "")).strip()
        path = explicit or heading or "root"
        return _stable_id("section", document_version_id, path), path

    def chunk(self, document: CanonicalDocument, *, tenant_id: str) -> ChunkingResult:
        children: list[Chunk] = []
        heading = ""
        for node in document.nodes:
            if node.node_type is NodeType.HEADING:
                heading = node.display_text.strip()
                if self.config.strategy == "structure":
                    continue
            section_id, section_path = self._section(node, heading, document.document_version_id)
            for piece_index, (text, start, end) in enumerate(
                _windows(node.original_text, self.config)
            ):
                chunk_id = _stable_id(
                    "chunk", document.document_version_id, node.node_id, str(piece_index), text
                )
                checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
                children.append(
                    Chunk(
                        id=chunk_id,
                        tenant_id=tenant_id,
                        version_id=document.document_version_id,
                        section_id=section_id,
                        ordinal=len(children),
                        original_text=text,
                        display_text=text,
                        retrieval_text=(f"{heading}\n{text}" if heading else text),
                        locator=_locator_for_slice(node, start, end),
                        content_sha256=checksum,
                        token_count=count_tokens(text),
                        kind=node.node_type.value,
                        chunking_revision=self.revision,
                        tokenizer_id=self.tokenizer_id,
                        metadata={
                            "chunk_index": len(children),
                            "node_id": node.node_id,
                            "section_path": section_path,
                            "heading": heading,
                        },
                    )
                )

        parents: list[Chunk] = []
        grouped: list[Chunk] = []
        grouped_tokens = 0

        def flush_parent() -> None:
            nonlocal grouped, grouped_tokens
            if not grouped:
                return
            text = "\n".join(item.display_text for item in grouped)
            parent_id = _stable_id(
                "parent", document.document_version_id, grouped[0].section_id, text
            )
            parent = Chunk(
                id=parent_id,
                tenant_id=tenant_id,
                version_id=document.document_version_id,
                section_id=grouped[0].section_id,
                ordinal=len(parents),
                original_text=text,
                display_text=text,
                retrieval_text=text,
                locator=grouped[0].locator,
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                token_count=count_tokens(text),
                kind="parent",
                chunking_revision=self.revision,
                tokenizer_id=self.tokenizer_id,
                metadata={"child_chunk_ids": [item.id for item in grouped]},
            )
            parents.append(parent)
            for index in range(len(children)):
                if children[index] in grouped:
                    children[index] = Chunk(
                        **{**children[index].__dict__, "parent_chunk_id": parent_id}
                    )
            grouped = []
            grouped_tokens = 0

        for child in children:
            if grouped and (
                child.section_id != grouped[-1].section_id
                or grouped_tokens + child.token_count > self.config.parent_max_tokens
            ):
                flush_parent()
            grouped.append(child)
            grouped_tokens += child.token_count
        flush_parent()
        return ChunkingResult(tuple(children), tuple(parents), self.revision)


class SemanticChunker(TokenAwareChunker):
    """Optional semantic boundary strategy with an injected, locally testable scorer."""

    def __init__(
        self,
        boundary_score: Callable[[str, str], float],
        *,
        threshold: float = 0.45,
        config: ChunkingConfig | None = None,
    ) -> None:
        super().__init__(config or ChunkingConfig(strategy="semantic"))
        self.boundary_score = boundary_score
        self.threshold = threshold

    def chunk(self, document: CanonicalDocument, *, tenant_id: str) -> ChunkingResult:
        nodes: Sequence[CanonicalNode] = document.nodes
        merged_nodes: list[CanonicalNode] = []
        current: list[CanonicalNode] = []
        heading = ""
        boundary_indexes: list[int] = []

        def flush() -> None:
            nonlocal current
            if not current:
                return
            text = "\n".join(item.original_text for item in current)
            merged_nodes.append(
                CanonicalNode(
                    node_id=_stable_id("semantic-node", *(item.node_id for item in current)),
                    parent_node_id=None,
                    node_type=NodeType.PARAGRAPH,
                    original_text=text,
                    display_text=text,
                    locator=current[0].locator,
                    metadata={"section_path": heading or "root", "heading": heading},
                )
            )
            current = []

        for index, node in enumerate(nodes):
            if node.node_type is NodeType.HEADING:
                flush()
                heading = node.display_text.strip()
                boundary_indexes.append(index)
                continue
            should_split = bool(
                current
                and (
                    count_tokens("\n".join(item.original_text for item in (*current, node)))
                    > self.config.target_tokens
                    or self.boundary_score(current[-1].display_text, node.display_text)
                    < self.threshold
                )
            )
            if should_split:
                flush()
                boundary_indexes.append(index)
            current.append(node)
        flush()
        semantic_document = replace(document, nodes=tuple(merged_nodes))
        delegate = TokenAwareChunker(
            ChunkingConfig(
                strategy="structure",
                target_tokens=self.config.target_tokens,
                overlap_tokens=self.config.overlap_tokens,
                min_tokens=self.config.min_tokens,
                max_tokens=self.config.max_tokens,
                parent_max_tokens=self.config.parent_max_tokens,
            )
        )
        result = delegate.chunk(semantic_document, tenant_id=tenant_id)
        revision = f"semantic:{self.threshold}:v1"
        enriched = tuple(
            replace(
                item,
                chunking_revision=revision,
                metadata={**item.metadata, "semantic_boundaries": tuple(boundary_indexes)},
            )
            for item in result.chunks
        )
        parents = tuple(replace(item, chunking_revision=revision) for item in result.parent_chunks)
        return ChunkingResult(enriched, parents, revision)
