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
    assert len(template_keys) == 122
    assert config_entries == {".env", ".env.example"}
    assert (root / "config/.env").is_file()
    assert "config/.env" in (root / ".gitignore").read_text(encoding="utf-8")
    template = load_env(root, env_path=root / "config/.env.example", environ={})
    assert all(not template.configured[key] for key in SECRET_KEYS)


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


def test_trusted_private_transport_exception_requires_service_and_evidence(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    _write(
        env_file,
        {
            "LLM_BASE_URL": "http://trusted.internal/v1",
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


def test_actual_config_parses_without_exposing_values() -> None:
    root = Path(__file__).resolve().parents[2]
    loaded = load_env(root)
    report = build_env_report(loaded, "G0")

    assert loaded.settings is not None
    assert report["summary"]["known_key_count"] == 122
    assert report["safe_output_contract"].startswith("variable names and status only")
