"""Explicitly non-acceptance OCR, legacy Office, and ASR fallback routes."""

from __future__ import annotations

import wave
from pathlib import Path

from ragkb.contracts.ports import ParsingDeferred
from ragkb.document_processing.parser_common import canonical_document, checksum, text_nodes
from ragkb.domain.documents import CanonicalDocument, CanonicalNode, NodeType, SourceLocator
from ragkb.domain.ids import new_uuid7


class ImageParserRoute:
    revision = "offline-ocr-stub:g4-v1"

    def __init__(self, source_format: str = "image") -> None:
        self.source_format = source_format

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        if not source.read_bytes():
            raise ParsingDeferred("PARSE_EMPTY", "image fixture is empty")
        text = f"[offline OCR stub:{source.name}:{checksum(source)[:12]}]"
        node = CanonicalNode(
            node_id=new_uuid7(),
            parent_node_id=None,
            node_type=NodeType.IMAGE,
            original_text=text,
            display_text=text,
            locator=SourceLocator(page=1, bbox=(0.0, 0.0, 1.0, 1.0)),
            metadata={"offline_stub": True},
        )
        return canonical_document(
            source,
            document_version_id,
            self.source_format,
            self.revision,
            [node],
            quality_issues=["offline_ocr_stub_real_effect_blocked"],
        )


class OfflineOfficeConversionStubParser:
    revision = "offline-office-conversion-stub:g4-v1"

    def __init__(self, source_format: str) -> None:
        self.source_format = source_format

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        if not source.read_bytes():
            raise ParsingDeferred("PARSE_EMPTY", "legacy Office fixture is empty")
        text = f"[offline Office conversion stub:{source.name}:{checksum(source)[:12]}]"
        nodes = text_nodes(
            [text],
            locator_factory=lambda _index, offset, length: SourceLocator(
                char_range=(offset, offset + length)
            ),
        )
        return canonical_document(
            source,
            document_version_id,
            self.source_format,
            self.revision,
            nodes,
            quality_issues=["offline_office_conversion_stub_real_effect_blocked"],
        )


class OfflineASRStubParser:
    revision = "offline-asr-stub:g4-v1"

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        if not source.read_bytes():
            raise ParsingDeferred("PARSE_EMPTY", "audio fixture is empty")
        duration = 1.0
        if source.suffix.casefold() == ".wav":
            try:
                with wave.open(str(source), "rb") as audio:
                    duration = audio.getnframes() / max(1, audio.getframerate())
            except wave.Error:
                duration = 1.0
        text = f"[offline ASR stub:{source.name}:{checksum(source)[:12]}]"
        node = CanonicalNode(
            node_id=new_uuid7(),
            parent_node_id=None,
            node_type=NodeType.AUDIO,
            original_text=text,
            display_text=text,
            locator=SourceLocator(start_time=0.0, end_time=max(duration, 0.001)),
            metadata={"offline_stub": True},
        )
        return canonical_document(
            source,
            document_version_id,
            "audio",
            self.revision,
            [node],
            quality_issues=["offline_asr_stub_real_effect_blocked"],
        )
