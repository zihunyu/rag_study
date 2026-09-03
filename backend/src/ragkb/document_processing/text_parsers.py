"""Plain text, Markdown, HTML, and text-layer PDF parsers."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from ragkb.contracts.ports import ParsingDeferred
from ragkb.document_processing.parser_common import canonical_document, text_nodes
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.ids import new_uuid7


class PlainTextParser:
    revision = "plain-text-parser:g1-v2"

    def __init__(self, source_format: str) -> None:
        self.source_format = source_format

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        blocks = source.read_text(encoding="utf-8").splitlines()
        node_types: list[NodeType] = []
        rendered: list[str] = []
        metadata: list[dict[str, Any]] = []
        for block in blocks:
            heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", block)
            if self.source_format == "markdown" and heading:
                rendered.append(heading.group(2))
                node_types.append(NodeType.HEADING)
                metadata.append({"heading_level": len(heading.group(1))})
            else:
                rendered.append(block)
                node_types.append(NodeType.PARAGRAPH)
                metadata.append({})
        nodes = text_nodes(
            rendered,
            locator_factory=lambda _index, offset, length: SourceLocator(
                char_range=(offset, offset + length)
            ),
            node_types=node_types,
            metadata=metadata,
        )
        if not nodes:
            raise ParsingDeferred("PARSE_EMPTY", "text document has no usable content")
        return canonical_document(
            source, document_version_id, self.source_format, self.revision, nodes
        )


class _HTMLExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, NodeType, dict[str, Any]]] = []
        self._ignored_depth = 0
        self._heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        heading = re.fullmatch(r"h([1-6])", tag.casefold())
        if heading:
            self._heading_level = int(heading.group(1))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        if re.fullmatch(r"h[1-6]", tag.casefold()):
            self._heading_level = None

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.blocks.append(
                (
                    data.strip(),
                    NodeType.HEADING if self._heading_level is not None else NodeType.PARAGRAPH,
                    ({"heading_level": self._heading_level} if self._heading_level else {}),
                )
            )


class HTMLUploadParser:
    revision = "html-upload-parser:g1-v2"

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        extractor = _HTMLExtractor()
        extractor.feed(source.read_text(encoding="utf-8"))
        nodes = text_nodes(
            [item[0] for item in extractor.blocks],
            locator_factory=lambda _index, offset, length: SourceLocator(
                char_range=(offset, offset + length)
            ),
            node_types=[item[1] for item in extractor.blocks],
            metadata=[item[2] for item in extractor.blocks],
        )
        if not nodes:
            raise ParsingDeferred("PARSE_EMPTY", "HTML document has no visible text")
        return canonical_document(source, document_version_id, "html", self.revision, nodes)


class TextPDFParser:
    revision = "pypdf-text:g1-v1"

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        reader = PdfReader(str(source))
        nodes: list[CanonicalNode] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            nodes.append(
                CanonicalNode(
                    node_id=new_uuid7(),
                    parent_node_id=None,
                    node_type=NodeType.PARAGRAPH,
                    original_text=text,
                    display_text=text,
                    locator=SourceLocator(page=page_number),
                    metadata={},
                )
            )
        if not nodes:
            raise ParsingDeferred(
                "OCR_REQUIRED", "PDF has no usable text layer; MinerU/OCR is required"
            )
        return canonical_document(source, document_version_id, "pdf_text", self.revision, nodes)
