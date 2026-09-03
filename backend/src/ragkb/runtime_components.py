"""Compose safe local G1-G4 adapters without external or billable calls."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from ragkb.adapters.auth import (
    LocalSingleUserAuthenticator,
    OIDCJWTAuthenticator,
    unavailable_oidc_decoder,
)
from ragkb.adapters.local_cleanup import LocalOriginalCleanupExecutor
from ragkb.adapters.local_storage import LocalFileStorage
from ragkb.adapters.publication_readiness import SQLitePublicationReadiness
from ragkb.adapters.rag_stubs import (
    DeterministicBufferedGenerator,
    LifecycleAwareFinalPermission,
)
from ragkb.adapters.retrieval_memory import InMemoryHybridIndex
from ragkb.adapters.sqlite_retrieval import SQLiteRetrievalControlPlane
from ragkb.adapters.stubs import DeterministicEmbedding, DeterministicReranker
from ragkb.application.evidence import SearchBackedEvidenceProvider
from ragkb.application.governance import GovernanceService
from ragkb.application.lifecycle import LifecycleService
from ragkb.application.observability import LocalObservabilityService
from ragkb.application.qa import TrustedQAService
from ragkb.application.search import HybridSearchService
from ragkb.application.uploads import UploadService
from ragkb.config import EnvSettings, build_env_report, find_repository_root, load_env
from ragkb.contracts.auth import AuthenticatorPort
from ragkb.document_processing.parsers import ParserRouter
from ragkb.engineering_security.file_validation import UploadFileValidator
from ragkb.engineering_security.malware import SignatureMalwareScanner
from ragkb.engineering_security.references import HMACReferenceSigner
from ragkb.infrastructure.governance_repository import SQLiteGovernanceRepository
from ragkb.infrastructure.lifecycle_repository import SQLiteLifecycleStore
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
    report = build_env_report(loaded, "G0")
    if loaded.settings is None or not report["summary"]["gate_ready"]:  # type: ignore[index]
        raise RuntimeError("config/.env has blocking G0 issues; run python scripts/check_env.py")
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
    observability = LocalObservabilityService(governance_repository)
    repository = SQLiteUploadRepository(database)
    queue = SQLitePersistentJobQueue(database)
    tenant_id, space_id = repository.ensure_local_hierarchy(
        settings.auth_local_tenant,
        "general_knowledge",
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
    lifecycle_store = SQLiteLifecycleStore(database)
    lifecycle_service = LifecycleService(
        lifecycle_store,
        tenant_id,
        cleanup_executors={
            "local_file": LocalOriginalCleanupExecutor(storage, repository),
        },
        publication_readiness=SQLitePublicationReadiness(database),
    )
    search_service = HybridSearchService(
        DeterministicEmbedding(),
        InMemoryHybridIndex(security_watermark=0),
        SQLiteRetrievalControlPlane(database),
        DeterministicReranker(),
        bm25_top_k=settings.retrieval_bm25_top_k,
        dense_top_k=settings.retrieval_dense_top_k,
        rrf_k=settings.retrieval_rrf_k,
        rerank_top_k=settings.retrieval_rerank_top_k,
        final_evidence_count=settings.retrieval_final_evidence_count,
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
        authenticator = OIDCJWTAuthenticator(settings, verified_decoder=unavailable_oidc_decoder)
    else:
        authenticator = LocalSingleUserAuthenticator(settings, tenant_id=tenant_id)
    generator = DeterministicBufferedGenerator()
    evidence_provider = SearchBackedEvidenceProvider(
        search_service,
        space_id=space_id,
        active_generation_id="local-g3-generation",
        active_permission_revision=lambda: max(
            (record.acl_revision for record in lifecycle_store.documents.values()),
            default=0,
        ),
        required_security_watermark=lambda: 0,
        prompt_revision=generator.revision,
        model_revision=generator.revision,
        final_evidence_count=settings.retrieval_final_evidence_count,
    )
    qa_service = TrustedQAService(
        evidence_provider,
        generator,
        LifecycleAwareFinalPermission(lifecycle_store, tenant_id),
        reference_signer,
        rag_repository,
    )
    return RuntimeComponents(
        repository_root=root,
        storage=storage,
        database=database,
        repository=repository,
        queue=queue,
        uploads=uploads,
        parser_router=ParserRouter(),
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
        tenant_id=tenant_id,
        space_id=space_id,
        settings=settings,
    )
