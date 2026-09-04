"""Shared runtime assembly delegated to an explicit environment profile factory."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from ragkb.adapters.local_indexing import SQLiteLocalIndexingSink
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.model_http import (
    HttpxJsonTransport,
)
from ragkb.adapters.mysql_governance import MySQLGovernanceRepository
from ragkb.adapters.mysql_upload import MySQLUploadRepository
from ragkb.adapters.rag_stubs import (
    LifecycleAwareFinalPermission,
)
from ragkb.adapters.zilliz import ZillizChunkIndexingSink
from ragkb.application.acceptance import load_acceptance_evidence
from ragkb.application.evidence import SearchBackedEvidenceProvider
from ragkb.application.governance import GovernanceService
from ragkb.application.lifecycle import InMemoryLifecycleStore, LifecycleService
from ragkb.application.observability import LocalObservabilityService
from ragkb.application.qa import (
    TrustedQAService,
)
from ragkb.application.search import HybridSearchService
from ragkb.application.tracing import TracerPort, build_runtime_tracer
from ragkb.application.uploads import UploadService
from ragkb.config import EnvSettings, build_env_report, find_repository_root, load_env
from ragkb.contracts.auth import AuthenticatorPort
from ragkb.contracts.jobs import PersistentJobQueuePort
from ragkb.contracts.ports import (
    ChunkerPort,
    RetrievalReleasePort,
)
from ragkb.contracts.rag import RAGRunRepositoryPort
from ragkb.document_processing.chunking import (
    ChunkingConfig,
    EmbeddingSemanticBoundaryScorer,
    SemanticChunker,
    TokenAwareChunker,
)
from ragkb.document_processing.parsers import ParserRouter
from ragkb.engineering_security.file_validation import UploadFileValidator
from ragkb.engineering_security.malware import SignatureMalwareScanner
from ragkb.engineering_security.references import HMACReferenceSigner, ReferenceStorePort
from ragkb.infrastructure.governance_repository import SQLiteGovernanceRepository
from ragkb.infrastructure.sqlite import SQLiteDatabase
from ragkb.infrastructure.upload_repository import SQLiteUploadRepository
from ragkb.runtime_profiles.factory import select_runtime_factory


@dataclass(frozen=True)
class RuntimeComponents:
    repository_root: Path
    storage: LocalFileStorage
    database: SQLiteDatabase
    repository: SQLiteUploadRepository | MySQLUploadRepository
    queue: PersistentJobQueuePort
    uploads: UploadService
    parser_router: ParserRouter
    search_service: HybridSearchService
    qa_service: TrustedQAService
    rag_repository: RAGRunRepositoryPort
    reference_signer: HMACReferenceSigner
    reference_store: ReferenceStorePort
    authenticator: AuthenticatorPort
    lifecycle_service: LifecycleService
    lifecycle_store: InMemoryLifecycleStore
    governance_repository: SQLiteGovernanceRepository | MySQLGovernanceRepository
    governance_service: GovernanceService
    observability: LocalObservabilityService
    chunker: ChunkerPort
    indexing_sink: SQLiteLocalIndexingSink | ZillizChunkIndexingSink
    model_transport: HttpxJsonTransport | None
    provider_transports: tuple[HttpxJsonTransport, ...]
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
    profile_factory = select_runtime_factory(settings)
    persistence = profile_factory.build_persistence(settings, database)
    governance_repository = persistence.governance_repository
    governance_service = GovernanceService(governance_repository)
    tracer, local_tracer = build_runtime_tracer(
        enabled=settings.otel_enabled,
        endpoint=settings.otel_exporter_otlp_endpoint,
        service_name=settings.app_name,
    )
    observability = LocalObservabilityService(governance_repository, local_tracer)
    repository = persistence.repository
    queue = persistence.queue
    tenant_id, space_id = repository.ensure_local_hierarchy(
        settings.auth_local_tenant,
        "general_knowledge",
        tenant_id_override=(
            (
                settings.auth_local_tenant
                if settings.auth_mode == "local_single_user"
                else settings.oidc_tenant_id
            )
            if profile_factory.name == "production"
            else None
        ),
        space_id_override=(
            (
                "general_knowledge"
                if settings.auth_mode == "local_single_user"
                else settings.oidc_default_space_id
            )
            if profile_factory.name == "production"
            else None
        ),
    )
    validator = UploadFileValidator(
        max_size_bytes=settings.upload_max_file_size_mb * 1024 * 1024,
        max_archive_uncompressed_bytes=settings.upload_max_archive_uncompressed_bytes,
        max_archive_entry_uncompressed_bytes=(settings.upload_max_archive_entry_uncompressed_bytes),
        max_archive_nesting_depth=settings.upload_max_archive_nesting_depth,
        archive_validation_timeout_seconds=settings.upload_archive_validation_timeout_seconds,
    )
    uploads = UploadService(
        repository,
        queue,
        storage,
        validator,
        SignatureMalwareScanner(),
        tenant_id,
        queue_max_attempts=settings.queue_max_retries + 1,
        quarantine_max_bytes=int(settings.upload_quarantine_max_gb * 1024**3),
        max_concurrent_streams=settings.upload_max_concurrent_streams,
    )
    retrieval = profile_factory.build_retrieval(settings, database, persistence, tracer)
    control_plane = retrieval.control_plane
    model_transport = retrieval.model_transport
    provider_transports = retrieval.provider_transports
    embedding = retrieval.embedding
    reranker = retrieval.reranker
    index = retrieval.index
    generator = retrieval.generator
    verifier = retrieval.verifier
    indexing_sink = retrieval.indexing_sink
    lifecycle = profile_factory.build_lifecycle(
        settings, database, storage, persistence, retrieval, tenant_id
    )
    lifecycle_store = lifecycle.store
    lifecycle_service = LifecycleService(
        lifecycle_store,
        tenant_id,
        cleanup_executors=lifecycle.cleanup_executors,
        publication_readiness=lifecycle.publication_readiness,
        retrieval_projection=lifecycle.projection,
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
    tokenizer = profile_factory.build_tokenizer(settings, root)
    chunker: ChunkerPort = (
        SemanticChunker(
            EmbeddingSemanticBoundaryScorer(embedding),
            threshold=settings.chunk_semantic_threshold,
            config=chunking_config,
            tokenizer=tokenizer,
        )
        if settings.chunk_strategy == "semantic"
        else TokenAwareChunker(chunking_config, tokenizer=tokenizer)
    )
    parser_router = profile_factory.build_parser(settings, root, storage)
    retrieval_release = profile_factory.build_retrieval_release(
        settings, retrieval, lifecycle_store, tenant_id, space_id
    )
    acceptance = False
    evidence_path = settings.rag_acceptance_evidence_path
    if not evidence_path.is_absolute():
        evidence_path = root / evidence_path
    if profile_factory.name == "production" and evidence_path.is_file():
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
                and evidence.verifier_revision == verifier.revision
                and evidence.tokenizer_revision == tokenizer.revision
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
    rag_persistence = profile_factory.build_rag_persistence(database, persistence)
    rag_repository = rag_persistence.repository
    reference_store = rag_persistence.reference_store
    configured_secret = settings.app_secret_key
    if profile_factory.name == "production" and settings.reference_signing_keyring is None:
        raise RuntimeError("PRODUCTION_REFERENCE_SIGNING_KEYRING_REQUIRED")
    reference_secret = (
        app_secret_override
        or (
            configured_secret
            if configured_secret is not None and len(configured_secret.get_secret_value()) >= 16
            else None
        )
        or SecretStr(secrets.token_urlsafe(48))
    )
    reference_keys: dict[str, SecretStr]
    if settings.reference_signing_keyring is not None:
        try:
            loaded_keyring = json.loads(settings.reference_signing_keyring.get_secret_value())
        except json.JSONDecodeError as error:
            raise RuntimeError("REFERENCE_SIGNING_KEYRING_INVALID") from error
        if not isinstance(loaded_keyring, dict):
            raise RuntimeError("REFERENCE_SIGNING_KEYRING_INVALID")
        reference_keys = {str(kid): SecretStr(str(value)) for kid, value in loaded_keyring.items()}
    else:
        reference_keys = {settings.reference_active_kid: reference_secret}
    reference_signer = HMACReferenceSigner(
        reference_keys,
        reference_store,
        active_kid=settings.reference_active_kid,
    )
    authenticator = profile_factory.build_authenticator(settings, tenant_id)
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
        verifier_revision=verifier.revision,
        final_evidence_count=settings.retrieval_final_evidence_count,
        release_provider=retrieval_release,
    )
    answer_cache = profile_factory.build_answer_cache(settings, persistence)
    qa_service = TrustedQAService(
        evidence_provider,
        generator,
        LifecycleAwareFinalPermission(lifecycle_store, tenant_id),
        reference_signer,
        rag_repository,
        answer_cache,
        tracer,
        verifier=verifier,
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
        provider_transports=provider_transports,
        tracer=tracer,
        retrieval_release=retrieval_release,
        tenant_id=tenant_id,
        space_id=space_id,
        settings=settings,
    )
