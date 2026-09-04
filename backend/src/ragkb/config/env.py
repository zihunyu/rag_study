"""Typed config/.env loader. Secret values never leave the settings object."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

UNCONFIGURED_MARKERS = frozenset({"", "change_me", "__fill_me__", "deferred", "not_applicable"})
TOKEN_STRATEGY_ROUND_ROBIN: Final[Literal["round_robin"]] = "round_robin"  # noqa: S105


def find_repository_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config/.env.example").is_file():
            return candidate
    raise FileNotFoundError("repository root marker config/.env.example was not found")


def _csv(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (tuple, list)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def _placeholder(value: str) -> bool:
    normalized = value.strip().strip('"').strip("'").casefold()
    return (
        normalized in UNCONFIGURED_MARKERS
        or normalized.startswith("change_me")
        or normalized.startswith("token_account_")
        or "your-" in normalized
        or "your_" in normalized
    )


class EnvSettings(BaseModel):
    """Every config/.env.example key has an explicit field and type."""

    model_config = ConfigDict(
        alias_generator=lambda field_name: field_name.upper(),
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    app_name: str = "Enterprise RAG Knowledge Base"
    app_env: Literal["development", "testing", "production"] = "development"
    rag_runtime_profile: Literal["local", "production"] = "local"
    rag_acceptance_evidence_path: Path = Path("./artifacts/acceptance/real-rag.json")
    rag_acceptance_signing_key: SecretStr | None = None
    rag_acceptance_max_age_hours: int = Field(default=168, gt=0)
    rag_acceptance_min_cases: int = Field(default=10, gt=0)
    real_acceptance_max_provider_calls: int = Field(default=60, gt=0)
    real_acceptance_max_input_tokens: int = Field(default=200_000, gt=0)
    real_acceptance_max_output_tokens: int = Field(default=20_000, gt=0)
    embedding_input_cost_per_million_cny: float = Field(default=0, ge=0)
    reranker_input_cost_per_million_cny: float = Field(default=0, ge=0)
    llm_input_cost_per_million_cny: float = Field(default=0, ge=0)
    llm_output_cost_per_million_cny: float = Field(default=0, ge=0)
    verifier_input_cost_per_million_cny: float = Field(default=0, ge=0)
    verifier_output_cost_per_million_cny: float = Field(default=0, ge=0)
    rag_acceptance_required_query_types: tuple[str, ...] = (
        "identifier",
        "keyword",
        "semantic",
        "multihop",
        "temporal",
        "negative",
        "unanswerable",
        "permission",
    )
    real_provider_calls_enabled: bool = False
    external_lifecycle_mutations_enabled: bool = False
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    frontend_host: str = "127.0.0.1"
    frontend_port: int = Field(default=5173, ge=1, le=65535)
    app_timezone: str = "Asia/Hong_Kong"
    app_debug: bool = True
    app_secret_key: SecretStr | None = None
    reference_active_kid: str = "local-v1"
    reference_signing_keyring: SecretStr | None = None
    app_revision: str = ""
    deployment_topology: Literal["single_instance", "multi_instance"] = "single_instance"
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173",)

    local_storage_root: Path = Path("./data/storage")
    local_storage_original_dir: Path = Path("./data/storage/original")
    local_storage_artifacts_dir: Path = Path("./data/storage/artifacts")
    local_storage_quarantine_dir: Path = Path("./data/storage/quarantine")
    local_storage_temp_dir: Path = Path("./data/storage/temp")
    local_storage_audit_dir: Path = Path("./data/storage/audit")
    local_storage_backup_dir: Path = Path("./data/storage/backups")
    local_storage_max_gb: int = Field(default=100, gt=0)
    local_storage_min_free_gb: float = Field(default=1.0, ge=0)
    local_storage_checksum_algorithm: Literal["sha256"] = "sha256"

    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "rag_kb"
    mysql_user: str = "rag_app"
    mysql_password: SecretStr | None = None
    mysql_charset: Literal["utf8mb4"] = "utf8mb4"
    mysql_ssl: bool = False
    mysql_pool_size: int = Field(default=10, gt=0)
    mysql_max_overflow: int = Field(default=20, ge=0)
    mysql_connect_timeout_seconds: float = Field(default=10, gt=0)

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_user: str = ""
    redis_password: SecretStr | None = None
    redis_db: int = Field(default=0, ge=0)
    redis_ssl: bool = False
    redis_key_prefix: str = "ragkb:"
    redis_timeout_seconds: float = Field(default=5, gt=0)

    zilliz_cloud_uri: str = ""
    zilliz_cloud_token: SecretStr | None = None
    zilliz_cloud_database: str = "default"
    zilliz_cloud_collection: str = "rag_chunks"
    zilliz_cloud_timeout_seconds: float = Field(default=30, gt=0)
    zilliz_cloud_consistency_level: Literal["Strong", "Session", "Bounded", "Eventually"] = (
        "Bounded"
    )
    zilliz_cloud_security_consistency_level: Literal["Strong"] = "Strong"
    zilliz_cloud_metric_type: Literal["COSINE", "IP", "L2"] = "COSINE"
    zilliz_cloud_dimension: int = Field(default=1024, gt=0)
    zilliz_cloud_enable_bm25: bool = True
    zilliz_cloud_bm25_analyzer: str = "chinese"
    zilliz_cloud_dense_field: str = "dense_vector"
    zilliz_cloud_sparse_field: str = "sparse_vector"
    zilliz_write_batch_size: int = Field(default=256, gt=0)
    zilliz_write_max_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    zilliz_write_max_retries: int = Field(default=3, ge=0)
    vector_backend: Literal["local", "zilliz", "milvus"] = "zilliz"
    vector_uri: str = ""
    vector_token: SecretStr | None = None
    vector_database: str = "default"
    vector_collection: str = "rag_chunks"
    vector_dimension: int = Field(default=1024, gt=0)
    vector_metric_type: Literal["COSINE", "IP", "L2"] = "COSINE"
    vector_enable_bm25: bool = True
    vector_bm25_analyzer: str = "chinese"
    vector_dense_field: str = "dense_vector"
    vector_sparse_field: str = "sparse_vector"
    vector_timeout_seconds: float = Field(default=30, gt=0)
    vector_consistency_level: Literal["Strong", "Session", "Bounded", "Eventually"] = "Bounded"
    vector_security_consistency_level: Literal["Strong"] = "Strong"

    queue_database_path: Path = Path("./data/storage/queue/jobs.sqlite3")
    queue_poll_interval_seconds: float = Field(default=1, gt=0)
    queue_lease_seconds: float = Field(default=60, gt=0)
    queue_heartbeat_seconds: float = Field(default=15, gt=0)
    queue_max_retries: int = Field(default=3, ge=0)
    queue_retry_delay_seconds: float = Field(default=5, gt=0)
    worker_retry_max_delay_seconds: float = Field(default=300, gt=0)
    worker_retry_jitter_seconds: float = Field(default=1, ge=0)
    worker_transient_max_attempts: int = Field(default=5, gt=0)
    worker_dependency_failure_threshold: int = Field(default=3, gt=0)
    worker_dependency_cooldown_seconds: float = Field(default=30, gt=0)
    worker_failure_pause_seconds: float = Field(default=1, gt=0)

    mineru_base_url: str = "https://mineru.net/api/v4"
    mineru_tokens: tuple[SecretStr, ...] = ()
    mineru_token_strategy: Literal["round_robin"] = TOKEN_STRATEGY_ROUND_ROBIN
    mineru_token_max_failures: int = Field(default=3, gt=0)
    mineru_token_cooldown_seconds: float = Field(default=60, gt=0)
    mineru_max_concurrency_per_token: int = Field(default=1, gt=0)
    mineru_timeout_seconds: float = Field(default=300, gt=0)
    mineru_model_version: str = "vlm"
    mineru_enable_ocr: bool = True
    mineru_enable_table: bool = True
    mineru_enable_formula: bool = True
    mineru_failover_enabled: bool = True
    mineru_runtime_max_files: int = Field(default=100_000, gt=0)
    mineru_runtime_max_requests: int = Field(default=3_300_000, gt=0)
    mineru_runtime_max_polls_per_file: int = Field(default=60, gt=0)
    mineru_runtime_poll_interval_seconds: float = Field(default=2, gt=0)

    llm_base_url: str = ""
    llm_allow_http: bool = False
    llm_api_key: SecretStr | None = None
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=120, gt=0)
    llm_max_concurrency: int = Field(default=8, gt=0)
    llm_max_output_tokens: int = Field(default=2048, gt=0)
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_top_p: float = Field(default=1.0, gt=0, le=1)
    llm_prompt_revision: str = "grounded-qa:v1"
    llm_generation_cache_ttl_seconds: int = Field(default=3600, gt=0)
    llm_allowed_output_domains: tuple[str, ...] = ()
    verifier_base_url: str = ""
    verifier_api_key: SecretStr | None = None
    verifier_model: str = ""
    verifier_timeout_seconds: float = Field(default=30, gt=0)
    verifier_max_concurrency: int = Field(default=4, gt=0)
    model_http_connect_timeout_seconds: float = Field(default=10, gt=0)
    model_http_pool_timeout_seconds: float = Field(default=10, gt=0)
    model_http_max_connections: int = Field(default=100, gt=0)
    model_http_max_keepalive_connections: int = Field(default=20, gt=0)
    model_http_max_retries: int = Field(default=3, ge=0)
    model_http_backoff_seconds: float = Field(default=0.25, ge=0)
    model_http_circuit_failure_threshold: int = Field(default=5, gt=0)
    model_http_circuit_cooldown_seconds: float = Field(default=30, gt=0)

    embedding_base_url: str = ""
    embedding_api_key: SecretStr | None = None
    embedding_model: str = ""
    embedding_dimension: int = Field(default=1024, gt=0)
    embedding_normalize: bool = True
    embedding_batch_size: int = Field(default=32, gt=0)
    embedding_max_concurrency: int = Field(default=4, gt=0)

    reranker_base_url: str = ""
    reranker_api_key: SecretStr | None = None
    reranker_model: str = ""
    reranker_max_candidates: int = Field(default=40, gt=0)
    reranker_max_concurrency: int = Field(default=4, gt=0)
    reranker_timeout_seconds: float = Field(default=60, gt=0)

    asr_enabled: bool = False
    asr_base_url: str = ""
    asr_api_key: SecretStr | None = None
    asr_model: str = ""
    asr_language: str = "auto"
    asr_timeout_seconds: float = Field(default=600, gt=0)

    chunk_strategy: Literal["token", "structure", "semantic"] = "structure"
    chunk_target_tokens: int = Field(default=600, gt=0)
    chunk_overlap_tokens: int = Field(default=80, ge=0)
    chunk_min_tokens: int = Field(default=20, gt=0)
    chunk_max_tokens: int = Field(default=800, gt=0)
    chunk_semantic_threshold: float = Field(default=0.45, ge=-1, le=1)
    parent_chunk_max_tokens: int = Field(default=1200, gt=0)
    tokenizer_artifact_path: Path = Path("./config/tokenizer.json")
    tokenizer_artifact_sha256: str = ""
    tokenizer_id: str = ""
    retrieval_bm25_top_k: int = Field(default=50, gt=0)
    retrieval_active_generation_id: str = "local-g3-generation"
    retrieval_dense_top_k: int = Field(default=50, gt=0)
    retrieval_rrf_k: int = Field(default=60, gt=0)
    retrieval_bm25_weight: float = Field(default=1.0, gt=0)
    retrieval_dense_weight: float = Field(default=1.0, gt=0)
    retrieval_identifier_bm25_weight: float = Field(default=2.0, gt=0)
    retrieval_rerank_top_k: int = Field(default=40, gt=0)
    retrieval_final_evidence_count: int = Field(default=8, gt=0)
    retrieval_near_duplicate_threshold: float = Field(default=0.92, ge=0, le=1)
    retrieval_max_chunks_per_document: int = Field(default=3, gt=0)
    retrieval_max_chunks_per_section: int = Field(default=2, gt=0)

    upload_max_file_size_mb: int = Field(default=200, gt=0)
    upload_max_pages: int = Field(default=600, gt=0)
    upload_quarantine_max_gb: float = Field(default=5.0, gt=0)
    upload_max_concurrent_streams: int = Field(default=4, gt=0)
    upload_max_archive_uncompressed_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    upload_max_archive_entry_uncompressed_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    upload_max_archive_nesting_depth: int = Field(default=1, ge=0)
    upload_archive_validation_timeout_seconds: float = Field(default=10.0, gt=0)
    upload_allowed_extensions: tuple[str, ...] = (
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "doc",
        "docx",
        "ppt",
        "pptx",
        "md",
        "txt",
        "html",
        "xls",
        "xlsx",
        "csv",
        "wav",
        "mp3",
        "m4a",
    )
    malware_scanner: str = "windows_defender"
    ai_outbound_allowed: bool = True
    ai_outbound_allowed_classifications: tuple[str, ...] = (
        "public",
        "internal",
        "confidential",
    )
    ai_approved_processing_regions: tuple[str, ...] = ()
    ai_cross_border_transfer_allowed: bool = False
    ai_trusted_private_transport_services: tuple[str, ...] = ()
    ai_trusted_private_transport_evidence: str = ""

    auth_mode: Literal["local_single_user", "oidc"] = "local_single_user"
    auth_local_tenant: str = "local"
    auth_local_user_id: str = "local-admin"
    oidc_issuer_url: str = ""
    oidc_audience: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr | None = None
    oidc_discovery_timeout_seconds: float = Field(default=10, gt=0)
    oidc_jwks_cache_seconds: int = Field(default=3600, gt=0)
    oidc_allowed_algorithms: tuple[str, ...] = ("RS256", "ES256")
    oidc_clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    oidc_clearance_claim: str = "clearance_level"
    oidc_tenant_id: str = ""
    oidc_default_space_id: str = ""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: Path = Path("./data/storage/logs")
    log_max_file_size_mb: int = Field(default=50, gt=0)
    log_retention_days: int = Field(default=30, ge=0)
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""

    @field_validator(
        "cors_origins",
        "upload_allowed_extensions",
        "ai_outbound_allowed_classifications",
        "ai_approved_processing_regions",
        "ai_trusted_private_transport_services",
        "oidc_allowed_algorithms",
        "rag_acceptance_required_query_types",
        mode="before",
    )
    @classmethod
    def parse_csv_values(cls, value: Any) -> tuple[str, ...]:
        return _csv(value)

    @field_validator("mineru_tokens", mode="before")
    @classmethod
    def parse_secret_csv(cls, value: Any) -> tuple[str, ...]:
        return _csv(value)

    @model_validator(mode="after")
    def validate_resource_limits(self) -> Self:
        if (
            self.upload_max_archive_entry_uncompressed_bytes
            > self.upload_max_archive_uncompressed_bytes
        ):
            raise ValueError("archive entry limit cannot exceed total archive limit")
        if self.worker_retry_max_delay_seconds < self.queue_retry_delay_seconds:
            raise ValueError("worker retry maximum cannot be below retry base")
        return self


@dataclass(frozen=True)
class EnvIssue:
    key: str
    code: str
    blocking_gate: str


@dataclass(frozen=True)
class EnvLoadResult:
    repository_root: Path
    env_path: Path
    settings: EnvSettings | None
    issues: tuple[EnvIssue, ...]
    sources: dict[str, str]
    configured: dict[str, bool]


SECRET_KEYS = frozenset(
    {
        "APP_SECRET_KEY",
        "REFERENCE_SIGNING_KEYRING",
        "RAG_ACCEPTANCE_SIGNING_KEY",
        "MYSQL_PASSWORD",
        "REDIS_PASSWORD",
        "ZILLIZ_CLOUD_TOKEN",
        "MINERU_TOKENS",
        "LLM_API_KEY",
        "VERIFIER_API_KEY",
        "EMBEDDING_API_KEY",
        "RERANKER_API_KEY",
        "ASR_API_KEY",
        "VECTOR_TOKEN",
        "OIDC_CLIENT_SECRET",
    }
)


def known_env_keys() -> frozenset[str]:
    return frozenset(
        str(field.alias or name).upper() for name, field in EnvSettings.model_fields.items()
    )


def _parse_env_file(path: Path) -> tuple[dict[str, str], list[EnvIssue]]:
    values: dict[str, str] = {}
    issues: list[EnvIssue] = []
    if not path.is_file():
        return values, [EnvIssue("config/.env", "ENV_FILE_MISSING", "G0")]
    known = known_env_keys()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                issues.append(EnvIssue(f"line:{line_number}", "ENV_LINE_INVALID", "G0"))
                continue
            key, _, raw_value = line.partition("=")
            key = key.strip().upper()
            if key not in known:
                issues.append(EnvIssue(key, "ENV_KEY_UNKNOWN", "G0"))
                continue
            value = raw_value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
    return values, issues


def load_env(
    repository_root: Path | None = None,
    *,
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> EnvLoadResult:
    root = find_repository_root(repository_root)
    resolved_env = env_path or root / "config/.env"
    file_values, issues = _parse_env_file(resolved_env)
    process = os.environ if environ is None else environ
    known = known_env_keys()
    merged = dict(file_values)
    sources = {key: "config_env" for key in file_values}
    for key in known:
        if key in process:
            merged[key] = process[key]
            sources[key] = "process_environment"
    configured = {
        key: key in merged and not _placeholder(str(merged[key])) for key in sorted(known)
    }
    typed_input = {key: value for key, value in merged.items() if configured.get(key, False)}
    try:
        settings = EnvSettings.model_validate(typed_input)
    except ValidationError as error:
        settings = None
        for item in error.errors(include_input=False, include_context=False):
            key = str(item["loc"][0]).upper() if item["loc"] else "ENV"
            issues.append(EnvIssue(key, f"ENV_TYPE_{item['type'].upper()}", "G0"))
    return EnvLoadResult(
        repository_root=root,
        env_path=resolved_env,
        settings=settings,
        issues=tuple(issues),
        sources=sources,
        configured=configured,
    )
