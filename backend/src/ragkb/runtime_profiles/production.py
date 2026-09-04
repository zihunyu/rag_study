"""Fail-closed production runtime factory."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ragkb.adapters.auth import (
    LocalSingleUserAuthenticator,
    OIDCDiscoveryJWTDecoder,
    OIDCJWTAuthenticator,
)
from ragkb.adapters.external_cleanup import (
    ExternalProjectionCleanupExecutor,
    ProjectionInspectorPort,
    RedisDocumentCleanupExecutor,
)
from ragkb.adapters.local_cleanup import LocalOriginalCleanupExecutor
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.adapters.model_http import (
    HttpxJsonTransport,
    OpenAICompatibleBufferedGenerator,
    OpenAICompatibleClaimVerifier,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
)
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.mysql_governance import MySQLGovernanceRepository
from ragkb.adapters.mysql_index_saga import MySQLIndexSagaLedger
from ragkb.adapters.mysql_lifecycle import MySQLLifecycleStore
from ragkb.adapters.mysql_rag import MySQLRAGRunRepository
from ragkb.adapters.mysql_references import MySQLReferenceStore
from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane
from ragkb.adapters.mysql_upload import MySQLUploadRepository
from ragkb.adapters.provider_http import MinerUHttpTransport
from ragkb.adapters.rag_cache import RedisVerifiedAnswerCache
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.adapters.redis_queue import RedisPersistentJobQueue
from ragkb.adapters.repository_readiness import RepositoryPublicationReadiness
from ragkb.adapters.retrieval_projection import CompositeDocumentProjection
from ragkb.adapters.zilliz import MilvusHybridAdapter, ZillizChunkIndexingSink, ZillizCloudAdapter
from ragkb.application.lifecycle import InMemoryLifecycleStore
from ragkb.application.provider_runners import MinerUExecutionRunner
from ragkb.application.qa import CompositeClaimVerifier, DeterministicClaimVerifier
from ragkb.application.tracing import TracerPort
from ragkb.config import EnvSettings
from ragkb.contracts.ports import DocumentProjectionPort, RetrievalReleasePort
from ragkb.document_processing.chunking import TokenizerArtifact
from ragkb.document_processing.mineru_parser import MinerUProductionParser
from ragkb.document_processing.parsers import FallbackParser, ParserRouter
from ragkb.document_processing.text_parsers import TextPDFParser
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from ragkb.infrastructure.provider_results import LocalProviderResultStore
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.runtime_profiles.contracts import (
    LifecycleAdapters,
    PersistenceAdapters,
    RAGPersistenceAdapters,
    RetrievalAdapters,
)


class ProductionRuntimeFactory:
    """Construct only external production adapters and reject profile drift."""

    name = "production"

    @staticmethod
    def _guard(settings: EnvSettings) -> None:
        if settings.rag_runtime_profile != "production" or settings.app_env != "production":
            raise RuntimeError("PRODUCTION_FACTORY_REQUIRES_PRODUCTION_SETTINGS")
        if settings.auth_mode == "local_single_user" and settings.app_host not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise RuntimeError("LOCAL_SINGLE_USER_AUTH_REQUIRES_LOOPBACK_APP_HOST")

    @staticmethod
    def _tenant_id(settings: EnvSettings) -> str:
        return (
            settings.auth_local_tenant
            if settings.auth_mode == "local_single_user"
            else settings.oidc_tenant_id
        )

    def build_persistence(
        self, settings: EnvSettings, database: SQLiteDatabase
    ) -> PersistenceAdapters:
        del database
        self._guard(settings)
        mysql = MySQLControlPlaneAdapter(settings)
        redis = RedisCacheRateLimitAdapter(settings)
        return PersistenceAdapters(
            governance_repository=MySQLGovernanceRepository(mysql, self._tenant_id(settings)),
            repository=MySQLUploadRepository(
                mysql,
                self._tenant_id(settings),
                settings.retrieval_active_generation_id,
            ),
            queue=RedisPersistentJobQueue(redis),
            mysql_control=mysql,
            redis_adapter=redis,
        )

    def build_retrieval(
        self,
        settings: EnvSettings,
        database: SQLiteDatabase,
        persistence: PersistenceAdapters,
        tracer: TracerPort,
    ) -> RetrievalAdapters:
        del database
        self._guard(settings)
        mysql = self._mysql(persistence)
        control_plane = MySQLRetrievalControlPlane(mysql)
        embedding_transport = HttpxJsonTransport(settings)
        reranker_transport = HttpxJsonTransport(settings)
        generator_transport = HttpxJsonTransport(settings)
        verifier_transport = HttpxJsonTransport(settings)
        embedding = OpenAICompatibleEmbeddingAdapter(
            settings,
            transport=embedding_transport,
            external_call_approved=settings.real_provider_calls_enabled,
        )
        index_factory = (
            MilvusHybridAdapter if settings.vector_backend == "milvus" else ZillizCloudAdapter
        )
        index = index_factory(
            settings,
            watermark_provider=lambda context: (
                cast(RetrievalReleasePort, control_plane)
                .current_release(context.tenant_id, context.space_ids[0])
                .security_watermark
            ),
        )
        generator = OpenAICompatibleBufferedGenerator(
            settings,
            transport=generator_transport,
            external_call_approved=settings.real_provider_calls_enabled,
        )
        verifier = CompositeClaimVerifier(
            DeterministicClaimVerifier(settings.llm_allowed_output_domains),
            OpenAICompatibleClaimVerifier(
                settings,
                transport=verifier_transport,
                external_call_approved=settings.real_provider_calls_enabled,
            ),
        )
        return RetrievalAdapters(
            control_plane=control_plane,
            model_transport=generator_transport,
            provider_transports=(
                embedding_transport,
                reranker_transport,
                generator_transport,
                verifier_transport,
            ),
            embedding=embedding,
            reranker=OpenAICompatibleRerankerAdapter(
                settings,
                transport=reranker_transport,
                external_call_approved=settings.real_provider_calls_enabled,
            ),
            index=index,
            generator=generator,
            verifier=verifier,
            indexing_sink=ZillizChunkIndexingSink(
                index,
                control_plane,
                embedding,
                settings,
                generation_id=settings.retrieval_active_generation_id,
                tracer=tracer,
                saga=MySQLIndexSagaLedger(mysql),
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
        del database
        self._guard(settings)
        mysql = self._mysql(persistence)
        redis = self._redis(persistence)
        repository = cast(MySQLUploadRepository, persistence.repository)
        control_plane = cast(MySQLRetrievalControlPlane, retrieval.control_plane)
        projection = CompositeDocumentProjection(
            (
                cast(DocumentProjectionPort, retrieval.index),
                cast(DocumentProjectionPort, control_plane),
            )
        )
        return LifecycleAdapters(
            store=MySQLLifecycleStore(mysql, tenant_id),
            projection=projection,
            cleanup_executors={
                "local_file": LocalOriginalCleanupExecutor(storage, repository),
                "mysql": ExternalProjectionCleanupExecutor(
                    cast(DocumentProjectionPort, control_plane),
                    cast(ProjectionInspectorPort, control_plane),
                    store_name="mysql",
                ),
                "zilliz_projection": ExternalProjectionCleanupExecutor(
                    cast(DocumentProjectionPort, retrieval.index),
                    cast(ProjectionInspectorPort, retrieval.index),
                    store_name="zilliz",
                ),
                "redis": RedisDocumentCleanupExecutor(redis),
            },
            publication_readiness=RepositoryPublicationReadiness(repository),
        )

    def build_parser(
        self, settings: EnvSettings, root: Path, storage: LocalFileStorage
    ) -> ParserRouter:
        self._guard(settings)
        artifacts_root = settings.local_storage_artifacts_dir
        if not artifacts_root.is_absolute():
            artifacts_root = root / artifacts_root
        result_store = LocalProviderResultStore(artifacts_root)
        runner = MinerUExecutionRunner(
            MinerUTokenPool(
                settings.mineru_tokens,
                max_concurrency_per_token=settings.mineru_max_concurrency_per_token,
                max_failures=settings.mineru_token_max_failures,
                cooldown_seconds=settings.mineru_token_cooldown_seconds,
                failover_enabled=settings.mineru_failover_enabled,
            ),
            MinerUHttpTransport(settings.mineru_base_url),
            JsonCheckpointStore(storage.root / "provider-checkpoints/mineru-runtime.json"),
            result_store,
            external_call_approved=settings.real_provider_calls_enabled,
            attempt_revision="mineru-production-runtime:v1",
            scope="production-runtime",
            max_files=settings.mineru_runtime_max_files,
            max_requests=settings.mineru_runtime_max_requests,
            max_polls_per_file=settings.mineru_runtime_max_polls_per_file,
            poll_interval_seconds=settings.mineru_runtime_poll_interval_seconds,
            timeout_seconds=settings.mineru_timeout_seconds,
            model_version=settings.mineru_model_version,
            enable_table=settings.mineru_enable_table,
            enable_formula=settings.mineru_enable_formula,
        )
        pdf = MinerUProductionParser(runner, result_store, source_format="pdf", is_ocr=True)
        return ParserRouter(
            {
                "pdf": FallbackParser(
                    TextPDFParser(), pdf, fallback_codes=frozenset({"OCR_REQUIRED"})
                ),
                "pdf_scanned": pdf,
                "image": MinerUProductionParser(
                    runner, result_store, source_format="image", is_ocr=True
                ),
                "doc": MinerUProductionParser(
                    runner, result_store, source_format="doc", is_ocr=False
                ),
                "ppt": MinerUProductionParser(
                    runner, result_store, source_format="ppt", is_ocr=False
                ),
            }
        )

    def build_tokenizer(self, settings: EnvSettings, root: Path) -> TokenizerArtifact:
        self._guard(settings)
        artifact = settings.tokenizer_artifact_path
        return TokenizerArtifact(
            artifact if artifact.is_absolute() else root / artifact,
            settings.tokenizer_artifact_sha256,
            settings.tokenizer_id,
        )

    def build_rag_persistence(
        self, database: SQLiteDatabase, persistence: PersistenceAdapters
    ) -> RAGPersistenceAdapters:
        del database
        mysql = self._mysql(persistence)
        return RAGPersistenceAdapters(
            repository=MySQLRAGRunRepository(mysql),
            reference_store=MySQLReferenceStore(mysql),
        )

    def build_retrieval_release(
        self,
        settings: EnvSettings,
        retrieval: RetrievalAdapters,
        lifecycle_store: InMemoryLifecycleStore,
        tenant_id: str,
        space_id: str,
    ) -> RetrievalReleasePort:
        del lifecycle_store, tenant_id, space_id
        self._guard(settings)
        return cast(RetrievalReleasePort, retrieval.control_plane)

    def build_authenticator(self, settings: EnvSettings, tenant_id: str):  # type: ignore[no-untyped-def]
        self._guard(settings)
        if settings.auth_mode == "local_single_user":
            return LocalSingleUserAuthenticator(settings, tenant_id=tenant_id)
        return OIDCJWTAuthenticator(settings, verified_decoder=OIDCDiscoveryJWTDecoder(settings))

    def build_answer_cache(
        self, settings: EnvSettings, persistence: PersistenceAdapters
    ) -> RedisVerifiedAnswerCache:
        self._guard(settings)
        return RedisVerifiedAnswerCache(
            self._redis(persistence), ttl_seconds=settings.llm_generation_cache_ttl_seconds
        )

    @staticmethod
    def _mysql(persistence: PersistenceAdapters) -> MySQLControlPlaneAdapter:
        if persistence.mysql_control is None:
            raise RuntimeError("PRODUCTION_MYSQL_CONTROL_REQUIRED")
        return persistence.mysql_control

    @staticmethod
    def _redis(persistence: PersistenceAdapters) -> RedisCacheRateLimitAdapter:
        if persistence.redis_adapter is None:
            raise RuntimeError("PRODUCTION_REDIS_ADAPTER_REQUIRED")
        return persistence.redis_adapter
