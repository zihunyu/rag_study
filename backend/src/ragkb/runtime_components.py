"""Compose isolated local or fully external production RAG components."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import SecretStr

from ragkb.adapters.auth import (
    LocalSingleUserAuthenticator,
    OIDCDiscoveryJWTDecoder,
    OIDCJWTAuthenticator,
)
from ragkb.adapters.external_cleanup import (
    EmptyRedisDocumentCleanupExecutor,
    ExternalProjectionCleanupExecutor,
    ProjectionInspectorPort,
)
from ragkb.adapters.local_cleanup import LocalOriginalCleanupExecutor
from ragkb.adapters.local_indexing import SQLiteLocalHybridIndex, SQLiteLocalIndexingSink
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.adapters.model_http import (
    HttpxJsonTransport,
    OpenAICompatibleBufferedGenerator,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
)
from ragkb.adapters.mysql_control import MySQLControlPlaneAdapter
from ragkb.adapters.mysql_retrieval import MySQLRetrievalControlPlane
from ragkb.adapters.provider_http import MinerUHttpTransport
from ragkb.adapters.publication_readiness import SQLitePublicationReadiness
from ragkb.adapters.rag_cache import RedisVerifiedAnswerCache
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    LifecycleAwareFinalPermission,
)
from ragkb.adapters.redis_cache import RedisCacheRateLimitAdapter
from ragkb.adapters.retrieval_projection import CompositeDocumentProjection
from ragkb.adapters.sqlite_retrieval import (
    LocalRetrievalReleaseProvider,
    SQLiteRetrievalControlPlane,
)
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.adapters.zilliz import MilvusHybridAdapter, ZillizChunkIndexingSink, ZillizCloudAdapter
from ragkb.application.acceptance import load_acceptance_evidence
from ragkb.application.evidence import SearchBackedEvidenceProvider
from ragkb.application.governance import GovernanceService
from ragkb.application.lifecycle import LifecycleService
from ragkb.application.observability import LocalObservabilityService
from ragkb.application.provider_runners import MinerUExecutionRunner
from ragkb.application.qa import InMemoryVerifiedAnswerCache, TrustedQAService
from ragkb.application.search import HybridSearchService
from ragkb.application.tracing import TracerPort, build_runtime_tracer
from ragkb.application.uploads import UploadService
from ragkb.config import EnvSettings, build_env_report, find_repository_root, load_env
from ragkb.contracts.auth import AuthenticatorPort
from ragkb.contracts.lifecycle import CleanupExecutorPort
from ragkb.contracts.ports import (
    ChunkerPort,
    DocumentProjectionPort,
    EmbeddingPort,
    HybridIndexPort,
    RerankerPort,
    RetrievalProjectionPort,
    RetrievalReleasePort,
)
from ragkb.contracts.rag import BufferedGenerationPort
from ragkb.document_processing.chunking import (
    ChunkingConfig,
    EmbeddingSemanticBoundaryScorer,
    SemanticChunker,
    TokenAwareChunker,
)
from ragkb.document_processing.mineru_parser import MinerUProductionParser
from ragkb.document_processing.parsers import FallbackParser, ParserRouter
from ragkb.document_processing.text_parsers import TextPDFParser
from ragkb.engineering_security.file_validation import UploadFileValidator
from ragkb.engineering_security.malware import SignatureMalwareScanner
from ragkb.engineering_security.references import HMACReferenceSigner
from ragkb.infrastructure.governance_repository import SQLiteGovernanceRepository
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
from ragkb.infrastructure.provider_checkpoints import JsonCheckpointStore
from ragkb.infrastructure.provider_results import LocalProviderResultStore
from ragkb.infrastructure.rag_repository import SQLiteRAGRunRepository
from ragkb.infrastructure.reference_repository import SQLiteReferenceStore
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.infrastructure.sqlite_queue import SQLitePersistentJobQueue
from ragkb.infrastructure.upload_repository import SQLiteUploadRepository


@dataclass(frozen=True)
class RuntimeComponents:
    repository_root: Path
    storage: LocalFileStorage
    database: SQLiteDatabase
    repository: SQLiteUploadRepository
    queue: SQLitePersistentJobQueue
    uploads: UploadService
    parser_router: ParserRouter
    search_service: HybridSearchService
    qa_service: TrustedQAService
    rag_repository: SQLiteRAGRunRepository
    reference_signer: HMACReferenceSigner
    reference_store: SQLiteReferenceStore
    authenticator: AuthenticatorPort
    lifecycle_service: LifecycleService
    lifecycle_store: SQLiteLifecycleStore
    governance_repository: SQLiteGovernanceRepository
    governance_service: GovernanceService
    observability: LocalObservabilityService
    chunker: ChunkerPort
    indexing_sink: SQLiteLocalIndexingSink | ZillizChunkIndexingSink
    model_transport: HttpxJsonTransport | None
    tracer: TracerPort
    retrieval_release: RetrievalReleasePort
    tenant_id: str
    space_id: str
    settings: EnvSettings


def build_runtime_components(
    *,
    repository_root: Path | None = None,
    storage_root: Path | None = None,
    database_path: Path | None = None,
    app_secret_override: SecretStr | None = None,
) -> RuntimeComponents:
    root = find_repository_root(repository_root)
    loaded = load_env(root)
    requested_gate = (
        "G4" if loaded.settings is not None and loaded.settings.app_env == "production" else "G0"
    )
    report = build_env_report(loaded, requested_gate)
    if loaded.settings is None or not report["summary"]["gate_ready"]:  # type: ignore[index]
        raise RuntimeError(
            f"config/.env has blocking {requested_gate} issues; run python scripts/check_env.py"
        )
    settings = loaded.settings
    if storage_root is None:
        configured = settings.local_storage_root
        storage_root = configured if configured.is_absolute() else root / configured
    storage = LocalFileStorage(storage_root)
    storage.ensure_layout()
    configured_database = settings.queue_database_path
    resolved_database = database_path or (
        configured_database if configured_database.is_absolute() else root / configured_database
    )
    database = SQLiteDatabase(resolved_database)
    governance_repository = SQLiteGovernanceRepository(database)
    governance_service = GovernanceService(governance_repository)
    tracer, local_tracer = build_runtime_tracer(
        enabled=settings.otel_enabled,
        endpoint=settings.otel_exporter_otlp_endpoint,
        service_name=settings.app_name,
    )
    observability = LocalObservabilityService(governance_repository, local_tracer)
    repository = SQLiteUploadRepository(database)
    queue = SQLitePersistentJobQueue(database)
    tenant_id, space_id = repository.ensure_local_hierarchy(
        settings.auth_local_tenant,
        "general_knowledge",
        tenant_id_override=(
            settings.oidc_tenant_id if settings.rag_runtime_profile == "production" else None
        ),
        space_id_override=(
            settings.oidc_default_space_id if settings.rag_runtime_profile == "production" else None
        ),
    )
    validator = UploadFileValidator(max_size_bytes=settings.upload_max_file_size_mb * 1024 * 1024)
    uploads = UploadService(
        repository,
        queue,
        storage,
        validator,
        SignatureMalwareScanner(),
        tenant_id,
        queue_max_attempts=settings.queue_max_retries + 1,
    )
    control_plane: RetrievalProjectionPort
    if settings.rag_runtime_profile == "production":
        control_plane = MySQLRetrievalControlPlane(MySQLControlPlaneAdapter(settings))
    else:
        control_plane = SQLiteRetrievalControlPlane(database)
    model_transport: HttpxJsonTransport | None = None
    embedding: EmbeddingPort
    reranker: RerankerPort
    index: HybridIndexPort
    generator: BufferedGenerationPort
    if settings.rag_runtime_profile == "production":
        model_transport = HttpxJsonTransport(settings)
        embedding = OpenAICompatibleEmbeddingAdapter(
            settings,
            transport=model_transport,
            external_call_approved=settings.real_provider_calls_enabled,
        )
        reranker = OpenAICompatibleRerankerAdapter(
            settings,
            transport=model_transport,
            external_call_approved=settings.real_provider_calls_enabled,
        )
        vector_adapter = (
            MilvusHybridAdapter if settings.vector_backend == "milvus" else ZillizCloudAdapter
        )
        index = vector_adapter(
            settings,
            watermark_provider=lambda context: (
                cast(RetrievalReleasePort, control_plane)
                .current_release(context.tenant_id, context.space_ids[0])
                .security_watermark
            ),
        )
        generator = OpenAICompatibleBufferedGenerator(
            settings,
            transport=model_transport,
            external_call_approved=settings.real_provider_calls_enabled,
        )
        indexing_sink: SQLiteLocalIndexingSink | ZillizChunkIndexingSink = ZillizChunkIndexingSink(
            index,
            control_plane,
            embedding,
            settings,
            generation_id=settings.retrieval_active_generation_id,
            tracer=tracer,
        )
    else:
        embedding = DeterministicEmbedding()
        reranker = DeterministicReranker()
        index = SQLiteLocalHybridIndex(database)
        generator = DeterministicBufferedGenerator()
        indexing_sink = SQLiteLocalIndexingSink(
            database,
            cast(SQLiteRetrievalControlPlane, control_plane),
            embedding,
            generation_id=settings.retrieval_active_generation_id,
            embedding_batch_size=settings.embedding_batch_size,
            tracer=tracer,
        )
    lifecycle_store = SQLiteLifecycleStore(database)
    lifecycle_projection = (
        CompositeDocumentProjection(
            (
                cast(DocumentProjectionPort, control_plane),
                cast(DocumentProjectionPort, index),
            )
        )
        if settings.rag_runtime_profile == "production"
        else control_plane
    )
    cleanup_executors: dict[str, CleanupExecutorPort] = {
        "local_file": LocalOriginalCleanupExecutor(storage, repository),
    }
    if settings.rag_runtime_profile == "production":
        cleanup_executors.update(
            {
                "mysql": ExternalProjectionCleanupExecutor(
                    cast(DocumentProjectionPort, control_plane),
                    cast(ProjectionInspectorPort, control_plane),
                    store_name="mysql",
                ),
                "zilliz_projection": ExternalProjectionCleanupExecutor(
                    cast(DocumentProjectionPort, index),
                    cast(ProjectionInspectorPort, index),
                    store_name="zilliz",
                ),
                "redis": EmptyRedisDocumentCleanupExecutor(),
            }
        )
    lifecycle_service = LifecycleService(
        lifecycle_store,
        tenant_id,
        cleanup_executors=cleanup_executors,
        publication_readiness=SQLitePublicationReadiness(database),
        retrieval_projection=lifecycle_projection,
        allow_external_cleanup=settings.external_lifecycle_mutations_enabled,
    )
    chunking_config = ChunkingConfig(
        strategy=settings.chunk_strategy,
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        min_tokens=settings.chunk_min_tokens,
        max_tokens=settings.chunk_max_tokens,
        parent_max_tokens=settings.parent_chunk_max_tokens,
    )
    chunker: ChunkerPort = (
        SemanticChunker(
            EmbeddingSemanticBoundaryScorer(embedding),
            threshold=settings.chunk_semantic_threshold,
            config=chunking_config,
        )
        if settings.chunk_strategy == "semantic"
        else TokenAwareChunker(chunking_config)
    )
    parser_router = ParserRouter()
    if settings.rag_runtime_profile == "production":
        artifacts_root = settings.local_storage_artifacts_dir
        if not artifacts_root.is_absolute():
            artifacts_root = root / artifacts_root
        mineru_result_store = LocalProviderResultStore(artifacts_root)
        mineru_runner = MinerUExecutionRunner(
            MinerUTokenPool(
                settings.mineru_tokens,
                max_concurrency_per_token=settings.mineru_max_concurrency_per_token,
                max_failures=settings.mineru_token_max_failures,
                cooldown_seconds=settings.mineru_token_cooldown_seconds,
                failover_enabled=settings.mineru_failover_enabled,
            ),
            MinerUHttpTransport(settings.mineru_base_url),
            JsonCheckpointStore(storage.root / "provider-checkpoints/mineru-runtime.json"),
            mineru_result_store,
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
        mineru_pdf = MinerUProductionParser(
            mineru_runner,
            mineru_result_store,
            source_format="pdf",
            is_ocr=True,
        )
        parser_router = ParserRouter(
            {
                "pdf": FallbackParser(
                    TextPDFParser(),
                    mineru_pdf,
                    fallback_codes=frozenset({"OCR_REQUIRED"}),
                ),
                "pdf_scanned": mineru_pdf,
                "image": MinerUProductionParser(
                    mineru_runner,
                    mineru_result_store,
                    source_format="image",
                    is_ocr=True,
                ),
                "doc": MinerUProductionParser(
                    mineru_runner,
                    mineru_result_store,
                    source_format="doc",
                    is_ocr=False,
                ),
                "ppt": MinerUProductionParser(
                    mineru_runner,
                    mineru_result_store,
                    source_format="ppt",
                    is_ocr=False,
                ),
            }
        )
    retrieval_release: RetrievalReleasePort = (
        cast(RetrievalReleasePort, control_plane)
        if settings.rag_runtime_profile == "production"
        else LocalRetrievalReleaseProvider(
            tenant_id=tenant_id,
            space_id=space_id,
            generation_id=settings.retrieval_active_generation_id,
            permission_revision=lambda: max(
                (record.acl_revision for record in lifecycle_store.documents.values()),
                default=0,
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
    )
    acceptance = False
    evidence_path = settings.rag_acceptance_evidence_path
    if not evidence_path.is_absolute():
        evidence_path = root / evidence_path
    if settings.rag_runtime_profile == "production" and evidence_path.is_file():
        acceptance_key = settings.rag_acceptance_signing_key
        if acceptance_key is not None:
            evidence = load_acceptance_evidence(
                evidence_path,
                signing_key=acceptance_key,
                max_age_hours=settings.rag_acceptance_max_age_hours,
                min_cases=settings.rag_acceptance_min_cases,
                required_query_types=settings.rag_acceptance_required_query_types,
            )
            acceptance = bool(
                evidence.embedding_revision == embedding.revision
                and evidence.reranker_revision == reranker.revision
                and evidence.model_revision == generator.revision
                and evidence.prompt_revision == settings.llm_prompt_revision
                and evidence.index_generation_id == settings.retrieval_active_generation_id
                and evidence.source_commit == settings.app_revision
            )
    search_service = HybridSearchService(
        embedding,
        index,
        control_plane,
        reranker,
        bm25_top_k=settings.retrieval_bm25_top_k,
        dense_top_k=settings.retrieval_dense_top_k,
        rrf_k=settings.retrieval_rrf_k,
        bm25_weight=settings.retrieval_bm25_weight,
        dense_weight=settings.retrieval_dense_weight,
        identifier_bm25_weight=settings.retrieval_identifier_bm25_weight,
        rerank_top_k=settings.retrieval_rerank_top_k,
        final_evidence_count=settings.retrieval_final_evidence_count,
        near_duplicate_threshold=settings.retrieval_near_duplicate_threshold,
        max_chunks_per_document=settings.retrieval_max_chunks_per_document,
        max_chunks_per_section=settings.retrieval_max_chunks_per_section,
        real_acceptance=acceptance,
        tracer=tracer,
        lifecycle_authorizer=lifecycle_store.authorizes_chunk,
    )
    rag_repository = SQLiteRAGRunRepository(database)
    reference_store = SQLiteReferenceStore(database)
    configured_secret = settings.app_secret_key
    reference_secret = (
        app_secret_override
        or (
            configured_secret
            if configured_secret is not None and len(configured_secret.get_secret_value()) >= 16
            else None
        )
        or SecretStr(secrets.token_urlsafe(48))
    )
    reference_signer = HMACReferenceSigner(reference_secret, reference_store)
    authenticator: AuthenticatorPort
    if settings.auth_mode == "oidc":
        authenticator = OIDCJWTAuthenticator(
            settings,
            verified_decoder=OIDCDiscoveryJWTDecoder(settings),
        )
    else:
        authenticator = LocalSingleUserAuthenticator(settings, tenant_id=tenant_id)
    evidence_provider = SearchBackedEvidenceProvider(
        search_service,
        space_id=space_id,
        active_generation_id=settings.retrieval_active_generation_id,
        active_permission_revision=lambda: max(
            (record.acl_revision for record in lifecycle_store.documents.values()),
            default=0,
        ),
        required_security_watermark=lambda: 0,
        prompt_revision=settings.llm_prompt_revision,
        model_revision=generator.revision,
        final_evidence_count=settings.retrieval_final_evidence_count,
        release_provider=retrieval_release,
    )
    answer_cache = (
        RedisVerifiedAnswerCache(
            RedisCacheRateLimitAdapter(settings),
            ttl_seconds=settings.llm_generation_cache_ttl_seconds,
        )
        if settings.rag_runtime_profile == "production"
        else InMemoryVerifiedAnswerCache()
    )
    qa_service = TrustedQAService(
        evidence_provider,
        generator,
        LifecycleAwareFinalPermission(lifecycle_store, tenant_id),
        reference_signer,
        rag_repository,
        answer_cache,
        tracer,
    )
    return RuntimeComponents(
        repository_root=root,
        storage=storage,
        database=database,
        repository=repository,
        queue=queue,
        uploads=uploads,
        parser_router=parser_router,
        search_service=search_service,
        qa_service=qa_service,
        rag_repository=rag_repository,
        reference_signer=reference_signer,
        reference_store=reference_store,
        authenticator=authenticator,
        lifecycle_service=lifecycle_service,
        lifecycle_store=lifecycle_store,
        governance_repository=governance_repository,
        governance_service=governance_service,
        observability=observability,
        chunker=chunker,
        indexing_sink=indexing_sink,
        model_transport=model_transport,
        tracer=tracer,
        retrieval_release=retrieval_release,
        tenant_id=tenant_id,
        space_id=space_id,
        settings=settings,
    )
