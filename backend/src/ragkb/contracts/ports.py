"""Replaceable ports kept independent of framework and supplier SDKs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ParsedNode:
    node_id: str
    node_type: str
    original_text: str
    display_text: str
    locator: Mapping[str, object]


@dataclass(frozen=True)
class CanonicalDocument:
    document_version_id: str
    language: str
    nodes: tuple[ParsedNode, ...]
    parser_revision: str
    normalization_revision: str
    content_checksum: str
    quality_issues: tuple[str, ...] = ()


class ContentStoragePort(Protocol):
    def write_bytes(self, partition: str, key: str, content: bytes) -> Path: ...

    def read_bytes(self, partition: str, key: str) -> bytes: ...


class ParserPort(Protocol):
    revision: str

    def parse(self, source: Path, document_version_id: str) -> CanonicalDocument: ...


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


class JobQueuePort(Protocol):
    def enqueue(self, job_id: str, payload: Mapping[str, object]) -> None: ...

    def dequeue(self) -> tuple[str, Mapping[str, object]] | None: ...
