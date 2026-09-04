from __future__ import annotations

import json
from pathlib import Path

from ragkb.config import build_env_report, load_env
from ragkb.config.env import SECRET_KEYS, known_env_keys


def _write(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8"
    )


def test_env_example_is_the_only_complete_template() -> None:
    root = Path(__file__).resolve().parents[2]
    config_entries = {path.name for path in (root / "config").iterdir()}
    template_keys = {
        line.split("=", 1)[0]
        for line in (root / "config/.env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert template_keys == known_env_keys()
    assert len(template_keys) == len(known_env_keys())
    assert config_entries == {".env", ".env.example", "rag-quality-thresholds.json"}
    assert (root / "config/.env").is_file()
    assert "config/.env" in (root / ".gitignore").read_text(encoding="utf-8")
    template = load_env(root, env_path=root / "config/.env.example", environ={})
    assert all(not template.configured[key] for key in SECRET_KEYS)
    assert template.settings is not None
    assert template.settings.embedding_batch_size == 10


def test_typed_env_parsing_and_process_precedence_without_secret_leak(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "APP_PORT": "8010",
            "APP_DEBUG": "false",
            "CORS_ORIGINS": "https://a.example,https://b.example",
            "MINERU_TOKENS": "file-token-a,file-token-b",
        },
    )
    process_secret = "PROCESS_SECRET_MUST_NOT_LEAK"  # noqa: S105
    loaded = load_env(
        Path(__file__).resolve().parents[2],
        env_path=env_file,
        environ={"APP_PORT": "8020", "MINERU_TOKENS": process_secret},
    )
    assert loaded.settings is not None
    assert loaded.settings.app_port == 8020
    assert loaded.settings.app_debug is False
    assert loaded.settings.cors_origins == ("https://a.example", "https://b.example")
    assert loaded.sources["APP_PORT"] == "process_environment"
    report_text = json.dumps(build_env_report(loaded, "G1"), sort_keys=True)
    assert process_secret not in report_text
    assert "file-token-a" not in report_text


def test_type_errors_are_reported_by_key_and_code_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(env_file, {"APP_PORT": "not-a-number"})

    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={})
    report = build_env_report(loaded, "G0")

    assert loaded.settings is None
    assert report["summary"]["gate_ready"] is False
    serialized = json.dumps(report)
    assert "not-a-number" not in serialized
    assert any(item["key"] == "APP_PORT" for item in report["issues"])


def test_cross_resource_limits_fail_configuration_before_runtime(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "UPLOAD_MAX_ARCHIVE_UNCOMPRESSED_BYTES": "100",
            "UPLOAD_MAX_ARCHIVE_ENTRY_UNCOMPRESSED_BYTES": "101",
            "QUEUE_RETRY_DELAY_SECONDS": "10",
            "WORKER_RETRY_MAX_DELAY_SECONDS": "5",
        },
    )

    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={})

    assert loaded.settings is None
    assert any(issue.code.startswith("ENV_TYPE_VALUE_ERROR") for issue in loaded.issues)


def test_condition_validation_for_queue_dimension_zilliz_and_https(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "QUEUE_LEASE_SECONDS": "10",
            "QUEUE_HEARTBEAT_SECONDS": "10",
            "EMBEDDING_DIMENSION": "768",
            "ZILLIZ_CLOUD_DIMENSION": "1024",
            "ZILLIZ_CLOUD_URI": "http://outside.example.com:19530",
            "LLM_BASE_URL": "http://llm.example/v1",
            "LLM_ALLOW_HTTP": "false",
            "AI_OUTBOUND_ALLOWED_CLASSIFICATIONS": "internal,confidential",
        },
    )
    report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={}), "G3"
    )
    codes = {item["code"] for item in report["issues"]}

    assert "QUEUE_HEARTBEAT_NOT_BELOW_LEASE" in codes
    assert "ZILLIZ_DIMENSION_MISMATCH" in codes
    assert "ZILLIZ_CHINA_HTTPS_REQUIRED" in codes
    assert "SENSITIVE_TRANSPORT_ENCRYPTION_REQUIRED" in codes
    assert any(
        item["key"] == "LLM_BASE_URL" and item["code"] == "SENSITIVE_TRANSPORT_ENCRYPTION_REQUIRED"
        for item in report["issues"]
    )


def test_dashscope_text_embedding_v4_batch_limit_is_reported_without_values(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "EMBEDDING_MODEL": "text-embedding-v4",
            "EMBEDDING_DIMENSION": "1024",
            "ZILLIZ_CLOUD_DIMENSION": "1024",
            "EMBEDDING_BATCH_SIZE": "11",
        },
    )
    report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={}), "G4"
    )
    assert "EMBEDDING_BATCH_SIZE:DASHSCOPE_TEXT_EMBEDDING_V4_MAX_10" in report["gate_blockers"]
    assert "compatible-mode" not in json.dumps(report)


def test_trusted_private_transport_exception_requires_service_and_evidence(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "LLM_BASE_URL": "http://trusted.internal/v1",
            "LLM_ALLOW_HTTP": "false",
            "AI_OUTBOUND_ALLOWED_CLASSIFICATIONS": "internal,confidential",
            "AI_TRUSTED_PRIVATE_TRANSPORT_SERVICES": "llm",
            "AI_TRUSTED_PRIVATE_TRANSPORT_EVIDENCE": "vpn-review-2026-001",
        },
    )
    report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={}), "G3"
    )

    assert not any(
        item["key"] == "LLM_BASE_URL" and item["code"] == "SENSITIVE_TRANSPORT_ENCRYPTION_REQUIRED"
        for item in report["issues"]
    )


def test_llm_allow_http_accepts_http_without_private_transport_evidence(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "LLM_BASE_URL": "http://llm.example/v1",
            "LLM_ALLOW_HTTP": "true",
            "AI_OUTBOUND_ALLOWED_CLASSIFICATIONS": "internal,confidential",
        },
    )

    report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={}), "G3"
    )

    assert not any(
        item["key"] == "LLM_BASE_URL" and item["code"] == "SENSITIVE_TRANSPORT_ENCRYPTION_REQUIRED"
        for item in report["issues"]
    )


def test_llm_allow_http_false_accepts_https(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_ALLOW_HTTP": "false",
            "AI_OUTBOUND_ALLOWED_CLASSIFICATIONS": "internal,confidential",
        },
    )

    report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={}), "G3"
    )

    assert not any(
        item["key"] == "LLM_BASE_URL" and item["code"] == "SENSITIVE_TRANSPORT_ENCRYPTION_REQUIRED"
        for item in report["issues"]
    )


def test_production_accepts_explicit_http_but_still_rejects_local_runtime(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "APP_ENV": "production",
            "APP_DEBUG": "false",
            "RAG_RUNTIME_PROFILE": "local",
            "LLM_BASE_URL": "http://llm.internal/v1",
            "LLM_ALLOW_HTTP": "true",
        },
    )

    report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={}), "G0"
    )

    blockers = set(report["gate_blockers"])
    assert "RAG_RUNTIME_PROFILE:PRODUCTION_PROFILE_REQUIRED" in blockers
    assert "LLM_ALLOW_HTTP:PRODUCTION_HTTP_OVERRIDE_FORBIDDEN" not in blockers
    assert "LLM_BASE_URL:PRODUCTION_HTTPS_REQUIRED" not in blockers
    assert "LLM_BASE_URL:SENSITIVE_TRANSPORT_ENCRYPTION_REQUIRED" not in blockers


def test_production_reports_missing_or_mismatched_tokenizer_artifact(tmp_path: Path) -> None:
    missing_env = tmp_path / "missing.env"
    _write(
        missing_env,
        {
            "APP_ENV": "production",
            "TOKENIZER_ARTIFACT_PATH": str(tmp_path / "missing-tokenizer.json"),
            "TOKENIZER_ARTIFACT_SHA256": "0" * 64,
            "TOKENIZER_ID": "provider-model-v1",
        },
    )
    missing_report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=missing_env, environ={}), "G4"
    )
    assert "TOKENIZER_ARTIFACT_PATH:TOKENIZER_ARTIFACT_MISSING" in missing_report["gate_blockers"]

    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")
    mismatch_env = tmp_path / "mismatch.env"
    _write(
        mismatch_env,
        {
            "APP_ENV": "production",
            "TOKENIZER_ARTIFACT_PATH": str(tokenizer),
            "TOKENIZER_ARTIFACT_SHA256": "0" * 64,
            "TOKENIZER_ID": "provider-model-v1",
        },
    )
    mismatch_report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=mismatch_env, environ={}), "G4"
    )
    assert (
        "TOKENIZER_ARTIFACT_SHA256:TOKENIZER_ARTIFACT_SHA256_MISMATCH"
        in mismatch_report["gate_blockers"]
    )


def test_asr_disabled_is_nonblocking_and_reenabled_scope_requires_three_keys(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    _write(env_file, {})
    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={})

    for gate in ("G1", "G2", "G3", "G4"):
        report = build_env_report(loaded, gate)
        assert not any(blocker.startswith("ASR_") for blocker in report["gate_blockers"])
        assert report["summary"]["asr_scope_enabled"] is False

    enabled_env = tmp_path / "enabled.env"
    _write(enabled_env, {"ASR_ENABLED": "true"})
    g4_report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=enabled_env, environ={}), "G4"
    )
    assert {
        "ASR_BASE_URL:ENV_REQUIRED",
        "ASR_API_KEY:ENV_REQUIRED",
        "ASR_MODEL:ENV_REQUIRED",
    }.issubset(set(g4_report["gate_blockers"]))


def test_local_single_user_and_missing_signing_secret_block_real_g3_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(env_file, {})
    loaded = load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={})

    g2 = build_env_report(loaded, "G2")
    g3 = build_env_report(loaded, "G3")

    assert "AUTH_MODE:ENTERPRISE_IDP_NOT_AVAILABLE" not in g2["gate_blockers"]
    assert "APP_SECRET_KEY:ENV_REQUIRED" not in g2["gate_blockers"]
    assert "AUTH_MODE:ENTERPRISE_IDP_DEFERRED" not in g3["gate_blockers"]
    assert "APP_SECRET_KEY:ENV_REQUIRED" in g3["gate_blockers"]
    assert g3["summary"]["gate_ready"] is False
    assert g3["summary"]["local_development_ready"] is True
    assert g3["summary"]["enterprise_oidc_acceptance"] is False
    assert g3["g3_auth_readiness"]["enterprise_blocker_reason"] == ("ENTERPRISE_IDP_DEFERRED")


def test_actual_config_parses_without_exposing_values() -> None:
    root = Path(__file__).resolve().parents[2]
    loaded = load_env(root)
    report = build_env_report(loaded, "G0")

    assert loaded.settings is not None
    assert report["summary"]["known_key_count"] == len(known_env_keys())
    assert report["safe_output_contract"].startswith("variable names and status only")
    variables = {item["name"]: item for item in report["variables"]}
    assert variables["APP_SECRET_KEY"]["configured"] is True
    assert variables["APP_SECRET_KEY"]["secret"] is True


def test_short_app_secret_is_reported_by_code_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write(env_file, {"APP_SECRET_KEY": "short-value"})

    report = build_env_report(
        load_env(Path(__file__).resolve().parents[2], env_path=env_file, environ={}), "G4"
    )

    assert "APP_SECRET_KEY:SECRET_TOO_SHORT_MIN_16" in report["gate_blockers"]
    assert "short-value" not in json.dumps(report)
