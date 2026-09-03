"""Secret-safe conditional validation and report for EnvSettings."""

from __future__ import annotations

from urllib.parse import urlparse

from ragkb.config.env import (
    SECRET_KEYS,
    TOKEN_STRATEGY_ROUND_ROBIN,
    EnvIssue,
    EnvLoadResult,
    EnvSettings,
    known_env_keys,
)

GATE_ORDER = {f"G{index}": index for index in range(7)}


def _is_https_or_approved_private(settings: EnvSettings, service: str, url: str) -> bool:
    if urlparse(url).scheme.casefold() == "https":
        return True
    return service in settings.ai_trusted_private_transport_services and bool(
        settings.ai_trusted_private_transport_evidence.strip()
    )


def _configured(result: EnvLoadResult, key: str) -> bool:
    return result.configured.get(key, False)


def conditional_issues(result: EnvLoadResult) -> tuple[EnvIssue, ...]:
    if result.settings is None:
        return ()
    settings = result.settings
    issues: list[EnvIssue] = []

    def require(key: str, gate: str) -> None:
        if not _configured(result, key):
            issues.append(EnvIssue(key, "ENV_REQUIRED", gate))

    if settings.app_env == "production":
        if settings.app_debug:
            issues.append(EnvIssue("APP_DEBUG", "PRODUCTION_DEBUG_FORBIDDEN", "G4"))
        require("APP_SECRET_KEY", "G4")
        require("REFERENCE_SIGNING_KEYRING", "G4")
        require("REFERENCE_ACTIVE_KID", "G4")
        require("APP_REVISION", "G0")
        if settings.deployment_topology != "single_instance":
            issues.append(
                EnvIssue(
                    "DEPLOYMENT_TOPOLOGY",
                    "LOCAL_STORAGE_REQUIRES_SINGLE_INSTANCE",
                    "G0",
                )
            )
        if settings.app_revision and (
            len(settings.app_revision) != 40
            or any(character not in "0123456789abcdef" for character in settings.app_revision)
        ):
            issues.append(EnvIssue("APP_REVISION", "GIT_COMMIT_SHA_REQUIRED", "G0"))
        if settings.rag_runtime_profile != "production":
            issues.append(EnvIssue("RAG_RUNTIME_PROFILE", "PRODUCTION_PROFILE_REQUIRED", "G0"))
        if not settings.real_provider_calls_enabled:
            issues.append(
                EnvIssue("REAL_PROVIDER_CALLS_ENABLED", "PRODUCTION_PROVIDERS_NOT_ENABLED", "G0")
            )
        require("RAG_ACCEPTANCE_SIGNING_KEY", "G0")
        if not settings.external_lifecycle_mutations_enabled:
            issues.append(
                EnvIssue(
                    "EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED",
                    "PRODUCTION_LIFECYCLE_MUTATIONS_NOT_ENABLED",
                    "G0",
                )
            )
        if settings.retrieval_active_generation_id.startswith("local-"):
            issues.append(
                EnvIssue("RETRIEVAL_ACTIVE_GENERATION_ID", "PRODUCTION_GENERATION_REQUIRED", "G0")
            )
        if settings.vector_backend == "local":
            issues.append(EnvIssue("VECTOR_BACKEND", "PRODUCTION_VECTOR_BACKEND_REQUIRED", "G0"))
        if settings.llm_allow_http:
            issues.append(EnvIssue("LLM_ALLOW_HTTP", "PRODUCTION_HTTP_OVERRIDE_FORBIDDEN", "G0"))
        if urlparse(settings.llm_base_url).scheme.casefold() == "http":
            issues.append(EnvIssue("LLM_BASE_URL", "PRODUCTION_HTTPS_REQUIRED", "G0"))
    if settings.queue_heartbeat_seconds >= settings.queue_lease_seconds:
        issues.append(EnvIssue("QUEUE_HEARTBEAT_SECONDS", "QUEUE_HEARTBEAT_NOT_BELOW_LEASE", "G1"))
    configured_vector_dimension = (
        settings.vector_dimension
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_dimension
    )
    if settings.embedding_dimension != configured_vector_dimension:
        issues.append(EnvIssue("EMBEDDING_DIMENSION", "ZILLIZ_DIMENSION_MISMATCH", "G2"))
    embedding_url = urlparse(settings.embedding_base_url)
    dashscope_v4 = bool(
        (embedding_url.hostname or "").casefold()
        in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
        and "/compatible-mode/" in embedding_url.path.casefold()
        and settings.embedding_model.casefold() == "text-embedding-v4"
    )
    if dashscope_v4 and settings.embedding_batch_size > 10:
        issues.append(
            EnvIssue(
                "EMBEDDING_BATCH_SIZE",
                "DASHSCOPE_TEXT_EMBEDDING_V4_MAX_10",
                "G4",
            )
        )
    if dashscope_v4 and settings.embedding_dimension != 1024:
        issues.append(
            EnvIssue(
                "EMBEDDING_DIMENSION",
                "DASHSCOPE_TEXT_EMBEDDING_V4_DIMENSION_1024_REQUIRED",
                "G4",
            )
        )
    if settings.chunk_overlap_tokens >= settings.chunk_target_tokens:
        issues.append(EnvIssue("CHUNK_OVERLAP_TOKENS", "CHUNK_OVERLAP_NOT_BELOW_TARGET", "G1"))
    if settings.chunk_target_tokens > settings.chunk_max_tokens:
        issues.append(EnvIssue("CHUNK_MAX_TOKENS", "CHUNK_MAX_BELOW_TARGET", "G1"))
    if settings.chunk_min_tokens > settings.chunk_target_tokens:
        issues.append(EnvIssue("CHUNK_MIN_TOKENS", "CHUNK_MIN_ABOVE_TARGET", "G1"))
    if settings.app_env == "production":
        for key in ("TOKENIZER_ARTIFACT_PATH", "TOKENIZER_ARTIFACT_SHA256", "TOKENIZER_ID"):
            require(key, "G4")
    if settings.retrieval_rerank_top_k > (
        settings.retrieval_bm25_top_k + settings.retrieval_dense_top_k
    ):
        issues.append(EnvIssue("RETRIEVAL_RERANK_TOP_K", "RERANK_TOP_K_EXCEEDS_RECALL", "G2"))

    for key in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD"):
        require(key, "G2")
    require("REDIS_HOST", "G3")

    root = (
        settings.local_storage_root
        if settings.local_storage_root.is_absolute()
        else result.repository_root / settings.local_storage_root
    ).resolve()
    for key, path in (
        ("LOCAL_STORAGE_ORIGINAL_DIR", settings.local_storage_original_dir),
        ("LOCAL_STORAGE_ARTIFACTS_DIR", settings.local_storage_artifacts_dir),
        ("LOCAL_STORAGE_QUARANTINE_DIR", settings.local_storage_quarantine_dir),
        ("LOCAL_STORAGE_TEMP_DIR", settings.local_storage_temp_dir),
        ("LOCAL_STORAGE_AUDIT_DIR", settings.local_storage_audit_dir),
    ):
        resolved = (path if path.is_absolute() else result.repository_root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            issues.append(EnvIssue(key, "STORAGE_PATH_OUTSIDE_ROOT", "G0"))

    if settings.vector_backend == "zilliz":
        zilliz_host = (urlparse(settings.zilliz_cloud_uri).hostname or "").casefold()
        if not settings.zilliz_cloud_uri.startswith("https://") or not zilliz_host.endswith(
            ".zilliz.com.cn"
        ):
            issues.append(EnvIssue("ZILLIZ_CLOUD_URI", "ZILLIZ_CHINA_HTTPS_REQUIRED", "G2"))
        require("ZILLIZ_CLOUD_URI", "G2")
        require("ZILLIZ_CLOUD_TOKEN", "G2")
    elif settings.vector_backend == "milvus":
        require("VECTOR_URI", "G2")
    require("ZILLIZ_CLOUD_DIMENSION", "G2")
    require("EMBEDDING_DIMENSION", "G2")
    bm25_enabled = (
        settings.vector_enable_bm25
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_enable_bm25
    )
    if not bm25_enabled:
        issues.append(EnvIssue("ZILLIZ_CLOUD_ENABLE_BM25", "ZILLIZ_BM25_REQUIRED", "G2"))
    security_consistency = (
        settings.vector_security_consistency_level
        if settings.vector_backend == "milvus"
        else settings.zilliz_cloud_security_consistency_level
    )
    if security_consistency != "Strong":
        issues.append(
            EnvIssue(
                "ZILLIZ_CLOUD_SECURITY_CONSISTENCY_LEVEL",
                "ZILLIZ_SECURITY_STRONG_REQUIRED",
                "G2",
            )
        )

    require("MINERU_TOKENS", "G1")
    if settings.mineru_token_strategy != TOKEN_STRATEGY_ROUND_ROBIN:
        issues.append(EnvIssue("MINERU_TOKEN_STRATEGY", "MINERU_ROUND_ROBIN_REQUIRED", "G1"))

    sensitive = bool(
        {"internal", "confidential"}.intersection(settings.ai_outbound_allowed_classifications)
    )
    if "restricted" in settings.ai_outbound_allowed_classifications:
        issues.append(
            EnvIssue(
                "AI_OUTBOUND_ALLOWED_CLASSIFICATIONS",
                "RESTRICTED_OUTBOUND_FORBIDDEN",
                "G0",
            )
        )
    if settings.ai_outbound_allowed and not settings.ai_approved_processing_regions:
        issues.append(
            EnvIssue("AI_APPROVED_PROCESSING_REGIONS", "AI_REGION_APPROVAL_REQUIRED", "G1")
        )
    for service, url, key, gate in (
        ("mineru", settings.mineru_base_url, "MINERU_BASE_URL", "G1"),
        ("embedding", settings.embedding_base_url, "EMBEDDING_BASE_URL", "G2"),
        ("reranker", settings.reranker_base_url, "RERANKER_BASE_URL", "G2"),
        ("asr", settings.asr_base_url, "ASR_BASE_URL", "G4"),
        ("llm", settings.llm_base_url, "LLM_BASE_URL", "G3"),
    ):
        if (
            sensitive
            and _configured(result, key)
            and not (service == "asr" and not settings.asr_enabled)
            and not (service == "llm" and settings.llm_allow_http)
            and not _is_https_or_approved_private(settings, service, url)
        ):
            issues.append(EnvIssue(key, "SENSITIVE_TRANSPORT_ENCRYPTION_REQUIRED", gate))

    for key in (
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
        "RERANKER_BASE_URL",
        "RERANKER_API_KEY",
        "RERANKER_MODEL",
    ):
        require(key, "G2")
    if settings.asr_enabled:
        for key in ("ASR_BASE_URL", "ASR_API_KEY", "ASR_MODEL"):
            require(key, "G4")
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        require(key, "G3")
    if settings.app_env == "production":
        for key in ("VERIFIER_BASE_URL", "VERIFIER_API_KEY", "VERIFIER_MODEL"):
            require(key, "G4")
        for key in (
            "EMBEDDING_INPUT_COST_PER_MILLION_CNY",
            "RERANKER_INPUT_COST_PER_MILLION_CNY",
            "LLM_INPUT_COST_PER_MILLION_CNY",
            "LLM_OUTPUT_COST_PER_MILLION_CNY",
            "VERIFIER_INPUT_COST_PER_MILLION_CNY",
            "VERIFIER_OUTPUT_COST_PER_MILLION_CNY",
        ):
            require(key, "G4")
        if (
            min(
                settings.embedding_input_cost_per_million_cny,
                settings.reranker_input_cost_per_million_cny,
                settings.llm_input_cost_per_million_cny,
                settings.llm_output_cost_per_million_cny,
                settings.verifier_input_cost_per_million_cny,
                settings.verifier_output_cost_per_million_cny,
            )
            <= 0
        ):
            issues.append(EnvIssue("PROVIDER_PRICING", "REAL_COST_RATES_REQUIRED", "G4"))
        if settings.verifier_model and settings.verifier_model == settings.llm_model:
            issues.append(EnvIssue("VERIFIER_MODEL", "INDEPENDENT_VERIFIER_MODEL_REQUIRED", "G4"))
    require("APP_SECRET_KEY", "G3")
    if settings.app_secret_key is not None and len(settings.app_secret_key.get_secret_value()) < 16:
        issues.append(EnvIssue("APP_SECRET_KEY", "SECRET_TOO_SHORT_MIN_16", "G3"))
    if settings.auth_mode == "local_single_user":
        issues.append(EnvIssue("AUTH_MODE", "ENTERPRISE_IDP_DEFERRED", "G5"))
    else:
        for key in ("OIDC_ISSUER_URL", "OIDC_AUDIENCE", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"):
            require(key, "G3")
        if settings.app_env == "production":
            require("OIDC_TENANT_ID", "G3")
            require("OIDC_DEFAULT_SPACE_ID", "G3")
    if settings.otel_enabled:
        require("OTEL_EXPORTER_OTLP_ENDPOINT", "G4")
    return tuple(issues)


def build_env_report(result: EnvLoadResult, requested_gate: str = "G0") -> dict[str, object]:
    issues = (*result.issues, *conditional_issues(result))
    blocking = [
        issue
        for issue in issues
        if GATE_ORDER.get(issue.blocking_gate, 999) <= GATE_ORDER[requested_gate]
    ]
    enterprise_auth_issue_keys = {
        "AUTH_MODE",
        "APP_SECRET_KEY",
        "OIDC_ISSUER_URL",
        "OIDC_AUDIENCE",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET",
    }
    local_blocking = [
        issue for issue in issues if GATE_ORDER.get(issue.blocking_gate, 999) <= GATE_ORDER["G0"]
    ]
    settings = result.settings
    local_development_ready = settings is not None and not local_blocking
    enterprise_oidc_acceptance = bool(
        settings is not None
        and settings.auth_mode == "oidc"
        and not any(issue.key in enterprise_auth_issue_keys for issue in blocking)
    )
    variables = [
        {
            "name": key,
            "configured": result.configured.get(key, False),
            "source": result.sources.get(key, "default_or_missing"),
            "secret": key in SECRET_KEYS,
            "type": str(EnvSettings.model_fields[key.casefold()].annotation),
        }
        for key in sorted(known_env_keys())
    ]
    return {
        "report_schema_version": 1,
        "requested_gate": requested_gate,
        "env_file": "config/.env",
        "precedence": ["process_environment", "config/.env", "typed_defaults"],
        "safe_output_contract": "variable names and status only; values are never returned",
        "summary": {
            "known_key_count": len(variables),
            "configured_count": sum(1 for item in variables if item["configured"]),
            "secret_configured_count": sum(
                1 for item in variables if item["secret"] and item["configured"]
            ),
            "issue_count": len(issues),
            "blocking_issue_count": len(blocking),
            "gate_ready": not blocking,
            "local_development_ready": local_development_ready,
            "enterprise_oidc_acceptance": enterprise_oidc_acceptance,
            "asr_scope_enabled": settings.asr_enabled if settings is not None else False,
            "current_validation_scope": "full_with_asr"
            if settings and settings.asr_enabled
            else "non_asr",
        },
        "g3_auth_readiness": {
            "mode": settings.auth_mode if settings is not None else "invalid_config",
            "local_development_ready": local_development_ready,
            "enterprise_oidc_acceptance": enterprise_oidc_acceptance,
            "enterprise_blocker_reason": (
                None if enterprise_oidc_acceptance else "ENTERPRISE_IDP_DEFERRED"
            ),
        },
        "variables": variables,
        "issues": [
            {"key": item.key, "code": item.code, "blocking_gate": item.blocking_gate}
            for item in issues
        ],
        "gate_blockers": [f"{item.key}:{item.code}" for item in blocking],
    }
