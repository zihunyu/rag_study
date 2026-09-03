"""Lifecycle cleanup execution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CleanupExecutionResult:
    executed: bool
    postcondition_met: bool
    error_code: str | None = None


class CleanupExecutorPort(Protocol):
    revision: str

    def execute(self, document_id: str) -> CleanupExecutionResult: ...


@dataclass(frozen=True)
class PublicationReadiness:
    ready: bool
    document_id: str
    version_id: str
    generation_id: str
    projection_state: str
    required_watermark: int
    observed_watermark: int
    expected_checksum: str
    observed_checksum: str
    document_row_version: int
    error_code: str | None = None


class PublicationReadinessPort(Protocol):
    revision: str

    def check(
        self, document_id: str, version_id: str, *, rollback: bool = False
    ) -> PublicationReadiness: ...
