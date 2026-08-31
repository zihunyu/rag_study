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
    if settings.queue_heartbeat_seconds >= settings.queue_lease_seconds:
        issues.append(EnvIssue("QUEUE_HEARTBEAT_SECONDS", "QUEUE_HEARTBEAT_NOT_BELOW_LEASE", "G1"))
    if settings.embedding_dimension != settings.zilliz_cloud_dimension:
        issues.append(EnvIssue("EMBEDDING_DIMENSION", "ZILLIZ_DIMENSION_MISMATCH", "G2"))
    if settings.chunk_overlap_tokens >= settings.chunk_target_tokens:
        issues.append(EnvIssue("CHUNK_OVERLAP_TOKENS", "CHUNK_OVERLAP_NOT_BELOW_TARGET", "G1"))
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

    zilliz_host = (urlparse(settings.zilliz_cloud_uri).hostname or "").casefold()
    if not settings.zilliz_cloud_uri.startswith("https://") or not zilliz_host.endswith(
        ".zilliz.com.cn"
    ):
        issues.append(EnvIssue("ZILLIZ_CLOUD_URI", "ZILLIZ_CHINA_HTTPS_REQUIRED", "G2"))
    require("ZILLIZ_CLOUD_URI", "G2")
    require("ZILLIZ_CLOUD_TOKEN", "G2")
    require("ZILLIZ_CLOUD_DIMENSION", "G2")
    require("EMBEDDING_DIMENSION", "G2")
    if not settings.zilliz_cloud_enable_bm25:
        issues.append(EnvIssue("ZILLIZ_CLOUD_ENABLE_BM25", "ZILLIZ_BM25_REQUIRED", "G2"))
    if settings.zilliz_cloud_security_consistency_level != "Strong":
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
        ("asr", settings.asr_base_url, "ASR_BASE_URL", "G2"),
        ("llm", settings.llm_base_url, "LLM_BASE_URL", "G3"),
    ):
        if (
            sensitive
            and _configured(result, key)
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
    for key in ("ASR_BASE_URL", "ASR_API_KEY", "ASR_MODEL"):
        require(key, "G2")
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        require(key, "G3")
    if settings.auth_mode == "oidc":
        for key in ("OIDC_ISSUER_URL", "OIDC_AUDIENCE", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"):
            require(key, "G3")
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
        },
        "variables": variables,
        "issues": [
            {"key": item.key, "code": item.code, "blocking_gate": item.blocking_gate}
            for item in issues
        ],
        "gate_blockers": [f"{item.key}:{item.code}" for item in blocking],
    }
