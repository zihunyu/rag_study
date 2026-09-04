"""Fail-closed local/testing runtime factory."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ragkb.adapters.auth import LocalSingleUserAuthenticator
from ragkb.adapters.local_cleanup import LocalOriginalCleanupExecutor
from ragkb.adapters.local_indexing import SQLiteLocalHybridIndex, SQLiteLocalIndexingSink
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.publication_readiness import SQLitePublicationReadiness
from ragkb.adapters.rag_stubs import DeterministicBufferedGenerator
from ragkb.adapters.sqlite_retrieval import (
    LocalRetrievalReleaseProvider,
    SQLiteRetrievalControlPlane,
)
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.application.lifecycle import InMemoryLifecycleStore
from ragkb.application.qa import DeterministicClaimVerifier, InMemoryVerifiedAnswerCache
from ragkb.application.tracing import TracerPort
from ragkb.config import EnvSettings
from ragkb.contracts.ports import DocumentProjectionPort
from ragkb.document_processing.chunking import TokenizerArtifact, UnicodeApproximateTokenizer
from ragkb.document_processing.parsers import ParserRouter
from ragkb.infrastructure.governance_repository import SQLiteGovernanceRepository
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
from ragkb.infrastructure.rag_repository import SQLiteRAGRunRepository
from ragkb.infrastructure.reference_repository import SQLiteReferenceStore
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.infrastructure.sqlite_queue import SQLitePersistentJobQueue
from ragkb.infrastructure.upload_repository import SQLiteUploadRepository
from ragkb.runtime_profiles.contracts import (
    LifecycleAdapters,
    PersistenceAdapters,
    RAGPersistenceAdapters,
    RetrievalAdapters,
)


class LocalRuntimeFactory:
    """Construct only non-production adapters; production settings are rejected."""

    name = "local"

    @staticmethod
    def _guard(settings: EnvSettings) -> None:
        if settings.rag_runtime_profile != "local" or settings.app_env == "production":
            raise RuntimeError("LOCAL_FACTORY_REJECTS_PRODUCTION_SETTINGS")

    def build_persistence(
        self, settings: EnvSettings, database: SQLiteDatabase
    ) -> PersistenceAdapters:
        self._guard(settings)
        return PersistenceAdapters(
            governance_repository=SQLiteGovernanceRepository(database),
            repository=SQLiteUploadRepository(database),
            queue=SQLitePersistentJobQueue(database),
        )

    def build_retrieval(
        self,
        settings: EnvSettings,
        database: SQLiteDatabase,
        persistence: PersistenceAdapters,
        tracer: TracerPort,
    ) -> RetrievalAdapters:
        self._guard(settings)
        embedding = DeterministicEmbedding()
        control_plane = SQLiteRetrievalControlPlane(database)
        return RetrievalAdapters(
            control_plane=control_plane,
            model_transport=None,
            provider_transports=(),
            embedding=embedding,
            reranker=DeterministicReranker(),
            index=SQLiteLocalHybridIndex(database),
            generator=DeterministicBufferedGenerator(),
            verifier=DeterministicClaimVerifier(settings.llm_allowed_output_domains),
            indexing_sink=SQLiteLocalIndexingSink(
                database,
                control_plane,
                embedding,
                generation_id=settings.retrieval_active_generation_id,
                embedding_batch_size=settings.embedding_batch_size,
                tracer=tracer,
            ),
        )

    def build_lifecycle(
        self,
        settings: EnvSettings,
        database: SQLiteDatabase,
        storage: LocalFileStorage,
        persistence: PersistenceAdapters,
        retrieval: RetrievalAdapters,
        tenant_id: str,
    ) -> LifecycleAdapters:
        self._guard(settings)
        repository = cast(SQLiteUploadRepository, persistence.repository)
        store = SQLiteLifecycleStore(database)
        return LifecycleAdapters(
            store=store,
            projection=cast(DocumentProjectionPort, retrieval.control_plane),
            cleanup_executors={
                "local_file": LocalOriginalCleanupExecutor(storage, repository),
            },
            publication_readiness=SQLitePublicationReadiness(database),
        )

    def build_parser(
        self, settings: EnvSettings, root: Path, storage: LocalFileStorage
    ) -> ParserRouter:
        del root, storage
        self._guard(settings)
        return ParserRouter()

    def build_tokenizer(
        self, settings: EnvSettings, root: Path
    ) -> TokenizerArtifact | UnicodeApproximateTokenizer:
        del root
        self._guard(settings)
        return UnicodeApproximateTokenizer()

    def build_rag_persistence(
        self, database: SQLiteDatabase, persistence: PersistenceAdapters
    ) -> RAGPersistenceAdapters:
        del persistence
        return RAGPersistenceAdapters(
            repository=SQLiteRAGRunRepository(database),
            reference_store=SQLiteReferenceStore(database),
        )

    def build_retrieval_release(
        self,
        settings: EnvSettings,
        retrieval: RetrievalAdapters,
        lifecycle_store: InMemoryLifecycleStore,
        tenant_id: str,
        space_id: str,
    ) -> LocalRetrievalReleaseProvider:
        self._guard(settings)
        return LocalRetrievalReleaseProvider(
            tenant_id=tenant_id,
            space_id=space_id,
            generation_id=settings.retrieval_active_generation_id,
            permission_revision=lambda: max(
                (record.acl_revision for record in lifecycle_store.documents.values()), default=0
            ),
            security_watermark=lambda: max(
                (
                    transition.observed_watermark
                    for transition in lifecycle_store.transitions.values()
                    if transition.status.value == "VERIFIED"
                ),
                default=0,
            ),
        )

    def build_authenticator(self, settings: EnvSettings, tenant_id: str):  # type: ignore[no-untyped-def]
        self._guard(settings)
        return LocalSingleUserAuthenticator(settings, tenant_id=tenant_id)

    def build_answer_cache(
        self, settings: EnvSettings, persistence: PersistenceAdapters
    ) -> InMemoryVerifiedAnswerCache:
        del persistence
        self._guard(settings)
        return InMemoryVerifiedAnswerCache()
