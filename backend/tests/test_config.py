from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import yaml
from ragkb.config.loader import load_configuration
from ragkb.config.validation import build_validation_report


def test_current_user_config_is_schema_valid_and_stub_ready() -> None:
    loaded = load_configuration()
    report = build_validation_report(loaded, "G0")

    assert loaded.schema_errors == ()
    assert report["summary"]["stub_development_ready"] is True
    assert report["readiness_profiles"]["basic_stub_startup"]["ready"] is True
    assert report["summary"]["gate_ready"] is True
    assert report["summary"]["user_blocker_count_for_requested_gate"] == 0
    assert report["summary"]["gate_blocker_count"] == 0
    placeholder_items = [
        item for item in report["missing_inputs"] if item["effective_source"] != "unresolved"
    ]
    assert placeholder_items
    assert all(item["effective_source"] == "stub" for item in placeholder_items)


def test_secret_values_never_enter_report(tmp_path: Path) -> None:
    sentinel = "DO_NOT_EXPOSE_SECRET_SENTINEL"
    local_env = tmp_path / ".env.user.local"
    local_env.write_text(f"LLM_API_KEY={sentinel}\n", encoding="utf-8")

    loaded = load_configuration(env_file=local_env, environ={})
    serialized = json.dumps(build_validation_report(loaded, "G3"), sort_keys=True)

    assert sentinel not in serialized
    assert "LLM_API_KEY" in serialized
    assert "local_env_file" in serialized


def test_fill_me_secret_is_not_configured(tmp_path: Path) -> None:
    local_env = tmp_path / ".env.user.local"
    local_env.write_text("MINERU_TOKEN=__FILL_ME__\n", encoding="utf-8")

    report = build_validation_report(load_configuration(env_file=local_env, environ={}), "G1")
    by_name = {item["name"]: item for item in report["secret_environment"]}

    assert by_name["MINERU_TOKEN"]["configured"] is False
    assert by_name["MINERU_TOKEN"]["required_for_current_mode"] is True
    assert by_name["MYSQL_PASSWORD"]["required_for_current_mode"] is False
    assert by_name["MILVUS_TOKEN"]["required_for_current_mode"] is False


def test_process_environment_placeholders_are_not_configured(tmp_path: Path) -> None:
    local_env = tmp_path / ".env.user.local"
    local_env.write_text("", encoding="utf-8")
    markers = ["", "__FILL_ME__", '"__FILL_ME__"', "deferred", "not_applicable"]

    for marker in markers:
        report = build_validation_report(
            load_configuration(
                env_file=local_env,
                environ={
                    "LLM_API_KEY": marker,
                    "EMBEDDING_API_KEY": marker,
                },
            ),
            "G3",
        )
        by_name = {item["name"]: item for item in report["secret_environment"]}
        assert by_name["LLM_API_KEY"]["configured"] is False
        assert by_name["EMBEDDING_API_KEY"]["configured"] is False


def test_real_process_environment_secret_is_only_reported_as_present(tmp_path: Path) -> None:
    local_env = tmp_path / ".env.user.local"
    local_env.write_text("", encoding="utf-8")
    sentinel = "PROCESS_SECRET_MUST_NOT_LEAK"

    report = build_validation_report(
        load_configuration(env_file=local_env, environ={"LLM_API_KEY": sentinel}), "G3"
    )
    serialized = json.dumps(report, sort_keys=True)
    llm = next(item for item in report["secret_environment"] if item["name"] == "LLM_API_KEY")

    assert llm["configured"] is True
    assert llm["source"] == "process_environment"
    assert sentinel not in serialized


def test_local_storage_paths_must_remain_under_configured_root(tmp_path: Path) -> None:
    loaded = load_configuration()
    user = deepcopy(loaded.user)
    user["infrastructure"]["local_storage"]["artifact_path"] = "../outside"
    config_path = tmp_path / "project-inputs.yaml"
    config_path.write_text(yaml.safe_dump(user, allow_unicode=True), encoding="utf-8")

    invalid = load_configuration(user_path=config_path)

    assert any(
        item["path"] == "infrastructure.local_storage.artifact_path"
        for item in invalid.schema_errors
    )


def test_priority_and_gate_mapping_is_explicit(tmp_path: Path) -> None:
    loaded = load_configuration()
    user = deepcopy(loaded.user)
    user["project"]["owners"]["technical_architect"] = "__FILL_ME__"
    user["infrastructure"]["milvus"]["uri"] = "__FILL_ME__"
    user["infrastructure"]["local_storage"]["backup_path"] = "__FILL_ME__"
    user["ai_services"]["asr"]["model_id"] = "__FILL_ME__"
    config_path = tmp_path / "project-inputs.yaml"
    config_path.write_text(yaml.safe_dump(user, allow_unicode=True), encoding="utf-8")
    report = build_validation_report(load_configuration(user_path=config_path), "G0")
    by_path = {item["path"]: item for item in report["missing_inputs"]}

    assert by_path["project.owners.technical_architect"]["responsibility"] == "user"
    assert by_path["project.owners.technical_architect"]["blocking_gate"] == "G4"
    assert by_path["infrastructure.milvus.uri"]["blocking_gate"] == "G2"
    assert by_path["infrastructure.local_storage.backup_path"]["blocking_gate"] == "G4"
    assert by_path["ai_services.asr.model_id"]["blocking_gate"] == "G2"


def test_readiness_profiles_separate_stub_and_real_integrations() -> None:
    report = build_validation_report(load_configuration(), "G0")
    profiles = report["readiness_profiles"]

    assert profiles["basic_stub_startup"]["ready"] is True
    assert profiles["real_integrations"]["mysql"]["status"] == "NOT_SELECTED"
    assert profiles["real_integrations"]["milvus"]["status"] == "NOT_SELECTED"
    assert profiles["real_integrations"]["llm"]["status"] == "BLOCKED"
    assert "ai_services.llm.endpoint" in profiles["real_integrations"]["llm"]["blockers"]
    assert profiles["real_integrations"]["mineru_hosted"]["status"] == "BLOCKED"


def _load_real_integration_gate_fixture(tmp_path: Path):
    user = deepcopy(load_configuration().user)
    user["infrastructure"]["milvus"]["provision_mode"] = "existing_native"
    user["infrastructure"]["milvus"]["uri"] = "__FILL_ME__"
    user["ai_services"]["asr"]["provider"] = "openai_compatible_third_party"
    for service in ("asr", "embedding", "reranker", "llm"):
        user["ai_services"][service]["endpoint"] = "__FILL_ME__"
        user["ai_services"][service]["model_id"] = "__FILL_ME__"
    for path in (
        ("embedding", "model_revision"),
        ("embedding", "max_concurrency"),
        ("reranker", "model_revision"),
        ("reranker", "max_concurrency"),
        ("llm", "model_revision"),
        ("llm", "timeout_seconds"),
        ("llm", "max_concurrency"),
    ):
        user["ai_services"][path[0]][path[1]] = "__FILL_ME__"
    config_path = tmp_path / "project-inputs.yaml"
    config_path.write_text(yaml.safe_dump(user, allow_unicode=True), encoding="utf-8")
    env_path = tmp_path / ".env.user.local"
    env_path.write_text("", encoding="utf-8")
    return load_configuration(user_path=config_path, env_file=env_path, environ={})


def test_g1_report_does_not_block_on_asr_or_llm(tmp_path: Path) -> None:
    report = build_validation_report(_load_real_integration_gate_fixture(tmp_path), "G1")
    blockers = set(report["gate_blockers"])

    assert not any("ai_services.asr" in item or "ASR_API_KEY" in item for item in blockers)
    assert not any("ai_services.llm" in item or "LLM_API_KEY" in item for item in blockers)


def test_g2_report_includes_retrieval_audio_and_milvus_but_not_llm(
    tmp_path: Path,
) -> None:
    report = build_validation_report(_load_real_integration_gate_fixture(tmp_path), "G2")
    blockers = set(report["gate_blockers"])

    expected = {
        "ai_services.asr.endpoint",
        "ai_services.asr.model_id",
        "env:ASR_API_KEY",
        "ai_services.embedding.endpoint",
        "env:EMBEDDING_API_KEY",
        "ai_services.reranker.endpoint",
        "env:RERANKER_API_KEY",
        "infrastructure.milvus.uri",
        "env:MILVUS_TOKEN",
    }
    assert expected.issubset(blockers)
    assert not any("ai_services.llm" in item or "LLM_API_KEY" in item for item in blockers)


def test_g3_report_first_includes_all_llm_conditions(tmp_path: Path) -> None:
    report = build_validation_report(_load_real_integration_gate_fixture(tmp_path), "G3")
    blockers = set(report["gate_blockers"])

    assert {
        "ai_services.llm.endpoint",
        "ai_services.llm.model_id",
        "ai_services.llm.model_revision",
        "ai_services.llm.timeout_seconds",
        "ai_services.llm.max_concurrency",
        "env:LLM_API_KEY",
    }.issubset(blockers)
