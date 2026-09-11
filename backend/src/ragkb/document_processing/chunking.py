"""Token-aware, structure-aware chunking between parsing and embedding."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from ragkb.application.cancellation import check_cancelled
from ragkb.contracts.ports import EmbeddingPort
from ragkb.document_processing.qa_structure import condition_anchors
from ragkb.document_processing.visual_tables import table_windows
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.entities import Chunk
from ragkb.tokenization import TokenizerArtifact as TokenizerArtifact

_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)
_BOUNDARY_CHARACTERS = frozenset("。！？!?；;：:\n")


class TokenizerPort(Protocol):
    revision: str

    def spans(self, text: str) -> tuple[tuple[int, int], ...]: ...


class UnicodeApproximateTokenizer:
    revision = "unicode-cjk-tokenizer"

    def spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in _TOKEN_PATTERN.finditer(text))


_DEFAULT_TOKENIZER = UnicodeApproximateTokenizer()


def token_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return deterministic approximate token spans for Latin and CJK text."""

    return _DEFAULT_TOKENIZER.spans(text)


def count_tokens(text: str, tokenizer: TokenizerPort | None = None) -> int:
    return len((tokenizer or _DEFAULT_TOKENIZER).spans(text))


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
    return replace(locator, char_range=char_range)


def _windows(
    text: str, config: ChunkingConfig, tokenizer: TokenizerPort
) -> tuple[tuple[str, int, int], ...]:
    spans = tokenizer.spans(text)
    if not spans:
        return ()
    size = min(config.target_tokens, config.max_tokens)
    windows: list[tuple[str, int, int]] = []
    token_start = 0
    while token_start < len(spans):
        check_cancelled()
        token_end = min(len(spans), token_start + size)
        if token_end < len(spans):
            minimum_end = min(token_end, token_start + config.min_tokens)
            for candidate_end in range(token_end, minimum_end, -1):
                boundary_end = spans[candidate_end - 1][1]
                if text[boundary_end - 1 : boundary_end] in _BOUNDARY_CHARACTERS:
                    token_end = candidate_end
                    break
        start = spans[token_start][0]
        end = spans[token_end - 1][1]
        piece = text[start:end].strip()
        # Byte-level BPE can assign several tokens to one Unicode character.
        # Slicing by offsets restores that whole character and may cost more
        # tokens than the original token-index window, so recount the actual text.
        while piece and count_tokens(piece, tokenizer) > config.max_tokens:
            if token_end <= token_start + 1:
                raise ValueError("CHUNK_CHARACTER_EXCEEDS_TOKEN_BUDGET")
            token_end -= 1
            end = spans[token_end - 1][1]
            piece = text[start:end].strip()
        if piece:
            if (
                windows
                and token_end == len(spans)
                and count_tokens(piece, tokenizer) < config.min_tokens
                and count_tokens(text[windows[-1][1]:end].strip(), tokenizer) <= config.max_tokens
            ):
                _, previous_start, _ = windows.pop()
                merged = text[previous_start:end].strip()
                windows.append((merged, previous_start, end))
            else:
                windows.append((piece, start, end))
        if token_end == len(spans):
            break
        token_start = max(token_start + 1, token_end - config.overlap_tokens)
    return tuple(windows)


def _table_windows(
    text: str, config: ChunkingConfig, tokenizer: TokenizerPort
) -> tuple[tuple[str, int, int], ...]:
    """Keep complete rows together; header context is attached separately."""
    rows = text.splitlines(keepends=True)
    html_rows = list(re.finditer(r"<tr\b[^>]*>.*?</tr\s*>", text, re.I | re.S))
    if html_rows:
        # MinerU often emits an entire HTML table on one line. Preserve real
        # row boundaries and original character offsets rather than splitting
        # the serialized table as a paragraph.
        boundaries = [0, *(row.end() for row in html_rows[:-1]), len(text)]
        rows = [
            text[left:right] for left, right in zip(boundaries[:-1], boundaries[1:], strict=True)
        ]
    result: list[tuple[str, int, int]] = []
    start = end = tokens = 0
    for row in rows:
        size = count_tokens(row, tokenizer)
        if tokens and tokens + size > config.target_tokens:
            value = text[start:end].strip()
            if value:
                result.append((value, start, end))
            start, tokens = end, 0
        if size > config.max_tokens:
            result.extend(
                (piece, end + left, end + right)
                for piece, left, right in _windows(row, config, tokenizer)
            )
            end += len(row)
            start = end
            continue
        end += len(row)
        tokens += size
    if text[start:end].strip():
        result.append((text[start:end].strip(), start, end))
    return tuple(result)


class TokenAwareChunker:
    """Create searchable children and larger parent context chunks with stable IDs."""

    def __init__(
        self,
        config: ChunkingConfig | None = None,
        *,
        tokenizer: TokenizerPort | None = None,
    ) -> None:
        self.config = config or ChunkingConfig()
        self.tokenizer = tokenizer or _DEFAULT_TOKENIZER
        self.tokenizer_id = self.tokenizer.revision
        self.revision = (
            f"token-aware:v6-bpe-budgets:{self.config.strategy}:"
            f"{self.config.target_tokens}:{self.config.overlap_tokens}"
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
        headings: list[tuple[int, str]] = []
        document_title = next(
            (
                node.display_text.strip()
                for node in document.nodes
                if node.node_type is NodeType.HEADING
            ),
            str(document.nodes[0].metadata.get("document_title", "")),
        )
        document_title = str(document.nodes[0].metadata.get("document_title") or document_title)
        for node in document.nodes:
            check_cancelled()
            if node.node_type is NodeType.HEADING:
                level = int(node.metadata.get("heading_level", 1))
                headings = [(depth, title) for depth, title in headings if depth < level]
                headings.append((level, node.display_text.strip()))
                heading = " / ".join(title for _, title in headings)
                if self.config.strategy == "structure":
                    continue
            node_heading = str(node.metadata.get("section_path", heading))
            node_heading = "" if node_heading == "root" else node_heading
            section_id, section_path = self._section(
                node, node_heading, document.document_version_id
            )
            windows = (
                table_windows(node, self.config, self.tokenizer)
                if node.metadata.get("visual_table")
                else [
                    (text, start, end, {})
                    for text, start, end in (
                        _table_windows if node.node_type is NodeType.TABLE else _windows
                    )(node.original_text, self.config, self.tokenizer)
                ]
            )
            for piece_index, (text, start, end, window_metadata) in enumerate(windows):
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
                        retrieval_text=self._retrieval_text(
                            node, node_heading, text, document_title
                        ),
                        locator=_locator_for_slice(node, start, end),
                        content_sha256=checksum,
                        token_count=count_tokens(text, self.tokenizer),
                        kind=node.node_type.value,
                        chunking_revision=self.revision,
                        tokenizer_id=self.tokenizer_id,
                        metadata={
                            **{
                                key: value
                                for key, value in node.metadata.items()
                                if key != "visual_table"
                            },
                            "chunk_index": len(children),
                            "node_id": node.node_id,
                            "section_path": section_path,
                            "heading": node_heading,
                            "document_title": document_title,
                            **(
                                {
                                    "source_spans": [
                                        {
                                            "locator": node.locator.to_dict(),
                                            "role": "table_context",
                                        },
                                    ]
                                }
                                if node.node_type is NodeType.TABLE
                                and "source_spans" not in node.metadata
                                else {}
                            ),
                            **window_metadata,
                            **condition_anchors(text),
                        },
                    )
                )

        parents: list[Chunk] = []
        grouped: list[Chunk] = []

        def flush_parent() -> None:
            nonlocal grouped
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
                retrieval_text="\n".join(
                    filter(
                        None,
                        (document_title, str(grouped[0].metadata.get("section_path", "")), text),
                    )
                ),
                locator=grouped[0].locator,
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                token_count=count_tokens(text, self.tokenizer),
                kind="parent",
                chunking_revision=self.revision,
                tokenizer_id=self.tokenizer_id,
                metadata={
                    **condition_anchors(text),
                    "child_chunk_ids": [item.id for item in grouped],
                    "nonvisual_source_spans": [
                        {
                            "chunk_id": item.id,
                            "text": item.display_text,
                            "locator": item.locator.to_dict(),
                        }
                        for item in grouped
                        if not visual_ids(item.metadata)
                    ]
                    if any(visual_ids(item.metadata) for item in grouped)
                    else [],
                    "visual_asset_ids": list(
                        dict.fromkeys(
                            identity for item in grouped for identity in visual_ids(item.metadata)
                        )
                    ),
                    "source_spans": [
                        {
                            "chunk_id": item.id,
                            "locator": {
                                **item.locator.to_dict(),
                                **(
                                    {"source_spans": item.metadata["source_spans"]}
                                    if "source_spans" in item.metadata
                                    else {}
                                ),
                            },
                        }
                        for item in grouped
                    ],
                    "section_path": grouped[0].metadata.get("section_path", "root"),
                    "document_title": document_title,
                },
            )
            parents.append(parent)
            for index in range(len(children)):
                if children[index] in grouped:
                    children[index] = Chunk(
                        **{**children[index].__dict__, "parent_chunk_id": parent_id}
                    )
            grouped = []

        for child in children:
            if grouped and (
                child.section_id != grouped[-1].section_id
                or count_tokens(
                    "\n".join(item.display_text for item in (*grouped, child)), self.tokenizer
                ) > self.config.parent_max_tokens
            ):
                flush_parent()
            grouped.append(child)
        flush_parent()
        return ChunkingResult(tuple(children), tuple(parents), self.revision)

    @staticmethod
    def _retrieval_text(
        node: CanonicalNode, heading: str, text: str, document_title: str = ""
    ) -> str:
        context: list[str] = []
        if document_title and document_title not in heading:
            context.append(f"DOCUMENT_TITLE: {document_title}")
        if heading:
            context.append(heading)
        table_header = str(node.metadata.get("table_header", "")).strip()
        if node.node_type is NodeType.TABLE and not table_header:
            header = re.search(r"<thead\b[^>]*>.*?</thead\s*>", node.original_text, re.I | re.S)
            if header is None:
                candidate = re.search(r"<tr\b[^>]*>.*?</tr\s*>", node.original_text, re.I | re.S)
                header = (
                    candidate if candidate and re.search(r"<th\b", candidate[0], re.I) else None
                )
            if header:
                table_header = header[0]
        if node.node_type is NodeType.TABLE and not table_header and "|" in node.original_text:
            table_header = node.original_text.splitlines()[0].strip()
        if (
            node.node_type is NodeType.TABLE
            and table_header
            and table_header != text
            and not node.metadata.get("visual_table")
        ):
            context.append(f"TABLE_HEADER: {table_header}")
        context.append(text)
        return "\n".join(context)


class SemanticChunker(TokenAwareChunker):
    """Optional semantic boundary strategy with an injected, locally testable scorer."""

    def __init__(
        self,
        boundary_score: Callable[[str, str], float],
        *,
        threshold: float = 0.45,
        config: ChunkingConfig | None = None,
        tokenizer: TokenizerPort | None = None,
    ) -> None:
        super().__init__(config or ChunkingConfig(strategy="semantic"), tokenizer=tokenizer)
        self.boundary_score = boundary_score
        self.threshold = threshold

    def chunk(self, document: CanonicalDocument, *, tenant_id: str) -> ChunkingResult:
        nodes: Sequence[CanonicalNode] = document.nodes
        merged_nodes: list[CanonicalNode] = []
        current: list[CanonicalNode] = []
        heading = ""
        boundary_indexes: list[int] = []

        preload = getattr(self.boundary_score, "preload", None)
        if callable(preload):
            preload(
                tuple(
                    node.display_text
                    for node in nodes
                    if node.node_type
                    not in {
                        NodeType.HEADING,
                        NodeType.TABLE,
                        NodeType.IMAGE,
                        NodeType.AUDIO,
                    }
                )
            )

        def compatible(left: CanonicalNode, right: CanonicalNode) -> bool:
            if left.node_type != right.node_type:
                return False
            if left.node_type in {NodeType.TABLE, NodeType.IMAGE, NodeType.AUDIO}:
                return False
            return bool(
                left.locator.page == right.locator.page
                and left.locator.slide == right.locator.slide
                and left.locator.sheet == right.locator.sheet
                and left.parent_node_id == right.parent_node_id
            )

        def flush() -> None:
            nonlocal current
            if not current:
                return
            text = "\n".join(item.original_text for item in current)
            metadata = {
                **(current[0].metadata if len(current) == 1 else {}),
                "section_path": current[0].metadata.get("section_path", heading or "root"),
                "heading": current[0].metadata.get("section_path", heading),
                "source_node_ids": [item.node_id for item in current],
                "source_spans": [item.locator.to_dict() for item in current],
            }
            merged_nodes.append(
                CanonicalNode(
                    node_id=_stable_id("semantic-node", *(item.node_id for item in current)),
                    parent_node_id=current[0].parent_node_id,
                    node_type=current[0].node_type,
                    original_text=text,
                    display_text=text,
                    locator=current[0].locator,
                    metadata=metadata,
                )
            )
            current = []

        for index, node in enumerate(nodes):
            check_cancelled()
            if node.node_type is NodeType.HEADING:
                flush()
                heading = node.display_text.strip()
                boundary_indexes.append(index)
                continue
            should_split = bool(
                current
                and (
                    not compatible(current[-1], node)
                    or (
                        count_tokens(
                            "\n".join(item.original_text for item in (*current, node)),
                            self.tokenizer,
                        )
                        > self.config.target_tokens
                        or self.boundary_score(current[-1].display_text, node.display_text)
                        < self.threshold
                    )
                )
            )
            if should_split:
                flush()
                boundary_indexes.append(index)
            current.append(node)
            if node.node_type in {NodeType.TABLE, NodeType.IMAGE, NodeType.AUDIO}:
                flush()
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
            ),
            tokenizer=self.tokenizer,
        )
        result = delegate.chunk(semantic_document, tenant_id=tenant_id)
        scorer_revision = str(getattr(self.boundary_score, "revision", "callable"))
        revision = (
            f"semantic:{scorer_revision}:{self.tokenizer.revision}:"
            f"{self.threshold}:{self.config.target_tokens}:{self.config.max_tokens}"
        )
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


class EmbeddingSemanticBoundaryScorer:
    """Cosine similarity scorer backed by the configured embedding provider."""

    def __init__(self, embedding: EmbeddingPort) -> None:
        self.embedding = embedding
        self.revision = f"embedding-boundary:{embedding.revision}"
        self._cache: dict[str, tuple[float, ...]] = {}

    def preload(self, texts: Sequence[str]) -> None:
        missing = tuple(dict.fromkeys(text for text in texts if text not in self._cache))
        if not missing:
            return
        vectors = self.embedding.embed(missing)
        if len(vectors) != len(missing):
            raise ValueError("semantic scorer embedding count mismatch")
        for text, vector in zip(missing, vectors, strict=True):
            self._cache[text] = tuple(map(float, vector))

    def _vector(self, text: str) -> tuple[float, ...]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        vectors = self.embedding.embed((text,))
        if len(vectors) != 1:
            raise ValueError("semantic scorer requires exactly one embedding")
        vector = tuple(map(float, vectors[0]))
        self._cache[text] = vector
        return vector

    def __call__(self, left: str, right: str) -> float:
        left_vector = self._vector(left)
        right_vector = self._vector(right)
        if len(left_vector) != len(right_vector):
            raise ValueError("semantic scorer embedding dimension mismatch")
        left_norm = math.sqrt(sum(value * value for value in left_vector))
        right_norm = math.sqrt(sum(value * value for value in right_vector))
        if not left_norm or not right_norm:
            return 0.0
        return sum(
            left_value * right_value
            for left_value, right_value in zip(left_vector, right_vector, strict=True)
        ) / (left_norm * right_norm)


def visual_ids(metadata: dict[str, object]) -> list[str]:
    value = metadata.get("visual_asset_ids")
    return [str(item) for item in value] if isinstance(value, list) else []
