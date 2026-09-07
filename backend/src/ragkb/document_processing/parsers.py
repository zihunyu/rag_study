"""Parser routing facade; implementations are split by format family."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from ragkb.application.cancellation import cancellation_active, cancellation_scope, check_cancelled
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
    revision = "parser-router"

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
        self._overridden = frozenset(overrides or {})

    def route(self, source_format: str) -> ParserPort:
        try:
            return self._routes[source_format]
        except KeyError as error:
            raise ParsingDeferred(
                "PARSE_ROUTE_UNAVAILABLE", f"no parser route for {source_format}"
            ) from error

    def artifact_keys(self, version_id: str) -> tuple[str, ...]:
        keys: set[str] = set()
        for parser in self._routes.values():
            method = getattr(parser, "artifact_keys", None)
            if callable(method):
                keys.update(method(version_id))
        return tuple(sorted(keys))

    def parse(
        self,
        source_format: str,
        source: Path,
        document_version_id: str,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> CanonicalDocument:
        with cancellation_scope(cancel_check):
            document = self._parse(source_format, source, document_version_id)
            check_cancelled()
            return document

    def _parse(
        self, source_format: str, source: Path, document_version_id: str
    ) -> CanonicalDocument:
        try:
            # Local Worker paths need the same interruptible process boundary as
            # production. Direct parser calls without a task keep their old behavior.
            if (
                cancellation_active()
                and source_format not in self._overridden
                and source_format
                in {"txt", "markdown", "html", "pdf", "docx", "pptx", "xlsx", "xls", "csv"}
            ):
                from ragkb.document_processing.isolated_parser import IsolatedNativeParser

                return IsolatedNativeParser(source_format).parse(source, document_version_id)
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
        check_cancelled()
        try:
            return self.primary.parse(source, document_version_id)
        except ParsingDeferred as error:
            if error.code not in self.fallback_codes:
                raise
            check_cancelled()
            return self.fallback.parse(source, document_version_id)
