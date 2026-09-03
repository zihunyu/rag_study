"""Parser routing facade; implementations are split by format family."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ragkb.contracts.ports import ParserPort, ParsingDeferred
from ragkb.document_processing.office_parsers import DOCXParser, PPTXParser, SpreadsheetParser
from ragkb.document_processing.offline_parsers import (
    ImageParserRoute,
    OfflineASRStubParser,
    OfflineOfficeConversionStubParser,
)
from ragkb.document_processing.text_parsers import (
    HTMLUploadParser,
    PlainTextParser,
    TextPDFParser,
)
from ragkb.domain.documents import CanonicalDocument

__all__ = [
    "DOCXParser",
    "FallbackParser",
    "HTMLUploadParser",
    "ImageParserRoute",
    "OfflineASRStubParser",
    "OfflineOfficeConversionStubParser",
    "PPTXParser",
    "ParserRouter",
    "PlainTextParser",
    "SpreadsheetParser",
    "TextPDFParser",
]


class ParserRouter:
    revision = "parser-router:g1-v2"

    def __init__(self, overrides: Mapping[str, ParserPort] | None = None) -> None:
        self._scanned_pdf_stub = ImageParserRoute("pdf_scanned")
        self._routes: dict[str, ParserPort] = {
            "txt": PlainTextParser("txt"),
            "markdown": PlainTextParser("markdown"),
            "html": HTMLUploadParser(),
            "pdf": TextPDFParser(),
            "image": ImageParserRoute(),
            "pdf_scanned": ImageParserRoute(),
            "doc": OfflineOfficeConversionStubParser("doc"),
            "ppt": OfflineOfficeConversionStubParser("ppt"),
            "docx": DOCXParser(),
            "pptx": PPTXParser(),
            "xlsx": SpreadsheetParser(),
            "xls": SpreadsheetParser(),
            "csv": SpreadsheetParser(),
            "audio": OfflineASRStubParser(),
        }
        self._routes.update(overrides or {})

    def route(self, source_format: str) -> ParserPort:
        try:
            return self._routes[source_format]
        except KeyError as error:
            raise ParsingDeferred(
                "PARSE_ROUTE_UNAVAILABLE", f"no parser route for {source_format}"
            ) from error

    def parse(
        self, source_format: str, source: Path, document_version_id: str
    ) -> CanonicalDocument:
        try:
            return self.route(source_format).parse(source, document_version_id)
        except ParsingDeferred as error:
            if source_format == "pdf" and error.code == "OCR_REQUIRED":
                return self._scanned_pdf_stub.parse(source, document_version_id)
            raise


class FallbackParser:
    def __init__(
        self,
        primary: ParserPort,
        fallback: ParserPort,
        *,
        fallback_codes: frozenset[str],
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.fallback_codes = fallback_codes
        self.revision = f"fallback:{primary.revision}:{fallback.revision}"

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument:
        try:
            return self.primary.parse(source, document_version_id)
        except ParsingDeferred as error:
            if error.code not in self.fallback_codes:
                raise
            return self.fallback.parse(source, document_version_id)
