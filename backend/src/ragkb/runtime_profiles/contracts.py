"""Typed boundaries between shared runtime assembly and profile-specific adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ragkb.adapters.local_indexing import SQLiteLocalIndexingSink
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.model_http import HttpxJsonTransport
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.mysql_governance import MySQLGovernanceRepository
from ragkb.adapters.mysql_upload import MySQLUploadRepository
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.adapters.zilliz import ZillizChunkIndexingSink
from ragkb.application.lifecycle import InMemoryLifecycleStore
from ragkb.application.tracing import TracerPort
from ragkb.config import EnvSettings
from ragkb.contracts.auth import AuthenticatorPort
from ragkb.contracts.jobs import PersistentJobQueuePort
from ragkb.contracts.lifecycle import CleanupExecutorPort, PublicationReadinessPort
from ragkb.contracts.ports import (
    DocumentProjectionPort,
    EmbeddingPort,
    HybridIndexPort,
    RerankerPort,
    RetrievalProjectionPort,
    RetrievalReleasePort,
)
from ragkb.contracts.rag import (
    BufferedGenerationPort,
    ClaimVerifierPort,
    RAGRunRepositoryPort,
    VerifiedAnswerCachePort,
)
from ragkb.document_processing.chunking import TokenizerPort
from ragkb.document_processing.parsers import ParserRouter
from ragkb.engineering_security.references import ReferenceStorePort
from ragkb.infrastructure.governance_repository import SQLiteGovernanceRepository
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.infrastructure.upload_repository import SQLiteUploadRepository

UploadRepository = SQLiteUploadRepository | MySQLUploadRepository
GovernanceRepository = SQLiteGovernanceRepository | MySQLGovernanceRepository
IndexingSink = SQLiteLocalIndexingSink | ZillizChunkIndexingSink


@dataclass(frozen=True)
class PersistenceAdapters:
    governance_repository: GovernanceRepository
    repository: UploadRepository
    queue: PersistentJobQueuePort
    mysql_control: MySQLControlPlaneAdapter | None = None
    redis_adapter: RedisCacheRateLimitAdapter | None = None


@dataclass(frozen=True)
class RetrievalAdapters:
    control_plane: RetrievalProjectionPort
    model_transport: HttpxJsonTransport | None
    provider_transports: tuple[HttpxJsonTransport, ...]
    embedding: EmbeddingPort
    reranker: RerankerPort
    index: HybridIndexPort
    generator: BufferedGenerationPort
    verifier: ClaimVerifierPort
    indexing_sink: IndexingSink


@dataclass(frozen=True)
class LifecycleAdapters:
    store: InMemoryLifecycleStore
    projection: DocumentProjectionPort
    cleanup_executors: dict[str, CleanupExecutorPort]
    publication_readiness: PublicationReadinessPort


@dataclass(frozen=True)
class RAGPersistenceAdapters:
    repository: RAGRunRepositoryPort
    reference_store: ReferenceStorePort


class RuntimeProfileFactory(Protocol):
    """Only boundary allowed to choose local or production implementations."""

    name: str

    def build_persistence(
        self, settings: EnvSettings, database: SQLiteDatabase
    ) -> PersistenceAdapters: ...

    def build_retrieval(
        self,
        settings: EnvSettings,
        database: SQLiteDatabase,
        persistence: PersistenceAdapters,
        tracer: TracerPort,
    ) -> RetrievalAdapters: ...

    def build_lifecycle(
        self,
        settings: EnvSettings,
        database: SQLiteDatabase,
        storage: LocalFileStorage,
        persistence: PersistenceAdapters,
        retrieval: RetrievalAdapters,
        tenant_id: str,
    ) -> LifecycleAdapters: ...

    def build_parser(
        self, settings: EnvSettings, root: Path, storage: LocalFileStorage
    ) -> ParserRouter: ...

    def build_tokenizer(self, settings: EnvSettings, root: Path) -> TokenizerPort: ...

    def build_rag_persistence(
        self, database: SQLiteDatabase, persistence: PersistenceAdapters
    ) -> RAGPersistenceAdapters: ...

    def build_retrieval_release(
        self,
        settings: EnvSettings,
        retrieval: RetrievalAdapters,
        lifecycle_store: InMemoryLifecycleStore,
        tenant_id: str,
        space_id: str,
    ) -> RetrievalReleasePort: ...

    def build_authenticator(self, settings: EnvSettings, tenant_id: str) -> AuthenticatorPort: ...

    def build_answer_cache(
        self, settings: EnvSettings, persistence: PersistenceAdapters
    ) -> VerifiedAnswerCachePort: ...
