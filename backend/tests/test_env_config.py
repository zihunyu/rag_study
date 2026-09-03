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
    assert len(template_keys) == 124
    assert config_entries == {".env", ".env.example"}
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
    assert report["summary"]["known_key_count"] == 124
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
