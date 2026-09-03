"""Publication readiness delegated to an authoritative repository aggregate."""

from __future__ import annotations

from typing import Protocol

from ragkb.contracts.lifecycle import PublicationReadiness


class ReadinessRepositoryPort(Protocol):
    def publication_readiness(
        self, document_id: str, version_id: str, *, rollback: bool = False
    ) -> PublicationReadiness: ...


class RepositoryPublicationReadiness:
    revision = "repository-publication-readiness:g4-v1"

    def __init__(self, repository: ReadinessRepositoryPort) -> None:
        self.repository = repository

    def check(
        self, document_id: str, version_id: str, *, rollback: bool = False
    ) -> PublicationReadiness:
        return self.repository.publication_readiness(document_id, version_id, rollback=rollback)
