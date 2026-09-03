"""Replaceable ports kept independent of framework and supplier SDKs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ragkb.domain.documents import CanonicalDocument
from ragkb.domain.retrieval import AuthorizedChunk, IndexCandidate, SearchContext

if TYPE_CHECKING:
    from ragkb.document_processing.chunking import ChunkingResult


class StorageIntegrityError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ParsingDeferred(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.real_acceptance = False


class ContentStoragePort(Protocol):
    def write_bytes(self, partition: str, key: str, content: bytes) -> Path: ...

    def read_bytes(self, partition: str, key: str) -> bytes: ...

    def path_for(self, partition: str, key: str) -> Path: ...

    def exists(self, partition: str, key: str) -> bool: ...

    def delete(self, partition: str, key: str) -> bool: ...

    def promote(
        self,
        source_partition: str,
        source_key: str,
        target_key: str,
        expected_sha256: str,
    ) -> Path: ...


class ParserPort(Protocol):
    revision: str

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument: ...


class ParserRouterPort(Protocol):
    revision: str

    def parse(
        self, source_format: str, source: Path, document_version_id: str
    ) -> CanonicalDocument: ...


class ChunkerPort(Protocol):
    revision: str

    def chunk(self, document: CanonicalDocument, *, tenant_id: str) -> ChunkingResult: ...


class EmbeddingPort(Protocol):
    revision: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class RerankerPort(Protocol):
    revision: str

    def rerank(self, query: str, documents: Sequence[str]) -> Sequence[int]: ...


class GenerationPort(Protocol):
    revision: str

    def generate(self, question: str, evidence: Sequence[str]) -> str: ...


class LexicalSearchPort(Protocol):
    revision: str

    def search(self, query: str, limit: int) -> Sequence[str]: ...


class PermissionProjectionPort(Protocol):
    def allowed(self, resource_tokens: Sequence[str], subject_tokens: Sequence[str]) -> bool: ...

    def watermark_ready(self, active_watermark: int, observed_watermark: int) -> bool: ...


class HybridIndexPort(Protocol):
    revision: str

    def observed_security_watermark(self, context: SearchContext) -> int: ...

    def search_bm25(
        self, query: str, context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]: ...

    def search_dense(
        self, vector: Sequence[float], context: SearchContext, limit: int
    ) -> Sequence[IndexCandidate]: ...


class RetrievalControlPlanePort(Protocol):
    revision: str

    def authorize_chunks(
        self, chunk_ids: Sequence[str], context: SearchContext
    ) -> Mapping[str, AuthorizedChunk]: ...

    def authorize_parent(
        self, parent_chunk_id: str, context: SearchContext
    ) -> AuthorizedChunk | None: ...
