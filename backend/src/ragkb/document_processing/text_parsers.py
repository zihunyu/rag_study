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
    revision = "plain-text-parser:structure-v3"

    def __init__(self, source_format: str) -> None:
        self.source_format = source_format

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        text = source.read_text(encoding="utf-8")
        blocks = text.splitlines(keepends=True)
        nodes: list[CanonicalNode] = []
        offsets = [0]
        for line in blocks:
            offsets.append(offsets[-1] + len(line))
        index = 0
        while index < len(blocks):
            block = blocks[index]
            if not block.strip():
                index += 1
                continue
            start = offsets[index] + len(block) - len(block.lstrip())
            heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", block)
            kind = NodeType.PARAGRAPH
            metadata: dict[str, Any] = {"document_title": source.stem}
            if self.source_format == "markdown" and heading:
                value = heading.group(2)
                start = offsets[index] + heading.start(2)
                kind = NodeType.HEADING
                metadata["heading_level"] = len(heading.group(1))
                index += 1
            elif (
                self.source_format == "markdown"
                and index + 1 < len(blocks)
                and (
                    "|" in block
                    and re.fullmatch(
                        r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*",
                        blocks[index + 1],
                    )
                )
            ):
                end_index = index + 2
                while (
                    end_index < len(blocks)
                    and "|" in blocks[end_index]
                    and blocks[end_index].strip()
                ):
                    end_index += 1
                value = "".join(blocks[index:end_index]).strip()
                kind = NodeType.TABLE
                metadata["table_header"] = block.strip()
                index = end_index
            else:
                # Keep contiguous prose/list steps together. Tables and headings
                # remain separate structures; source offsets address the real file.
                end_index = index + 1
                while end_index < len(blocks) and blocks[end_index].strip():
                    if self.source_format == "markdown" and (
                        re.match(r"\s{0,3}#{1,6}\s", blocks[end_index]) or "|" in blocks[end_index]
                    ):
                        break
                    end_index += 1
                value = "".join(blocks[index:end_index]).strip()
                if re.match(r"(?:[-*+] |\d+[.)] )", value):
                    kind = NodeType.LIST
                index = end_index
            nodes.append(
                CanonicalNode(
                    new_uuid7(),
                    None,
                    kind,
                    value,
                    value,
                    SourceLocator(char_range=(start, start + len(value))),
                    metadata,
                )
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
        self._table_rows: list[list[str]] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        heading = re.fullmatch(r"h([1-6])", tag.casefold())
        if heading:
            self._heading_level = int(heading.group(1))
        if tag.casefold() == "table":
            self._table_rows = []
        elif tag.casefold() == "tr" and self._table_rows is not None:
            self._table_rows.append([])
        elif tag.casefold() in {"td", "th"} and self._table_rows is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        if re.fullmatch(r"h[1-6]", tag.casefold()):
            self._heading_level = None
        if tag.casefold() in {"td", "th"} and self._cell is not None:
            if self._table_rows is not None:
                if not self._table_rows:
                    self._table_rows.append([])
                self._table_rows[-1].append(" ".join(self._cell))
            self._cell = None
        if tag.casefold() == "table" and self._table_rows is not None:
            rows = [" | ".join(row) for row in self._table_rows if row]
            if rows:
                self.blocks.append(("\n".join(rows), NodeType.TABLE, {"table_header": rows[0]}))
            self._table_rows = None

    def handle_data(self, data: str) -> None:
        if self._table_rows is not None:
            if self._cell is not None and not self._ignored_depth and data.strip():
                self._cell.append(data.strip())
            return
        if not self._ignored_depth and data.strip():
            self.blocks.append(
                (
                    data.strip(),
                    NodeType.HEADING if self._heading_level is not None else NodeType.PARAGRAPH,
                    ({"heading_level": self._heading_level} if self._heading_level else {}),
                )
            )


class HTMLUploadParser:
    revision = "html-upload-parser"

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
    revision = "pypdf-text"

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
