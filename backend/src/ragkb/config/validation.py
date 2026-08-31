"""Generate deterministic, secret-safe Gate reports."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from ragkb.config.loader import PLACEHOLDER
from ragkb.config.models import LoadedConfiguration

GATE_ORDER = {f"G{index}": index for index in range(7)}


def _load_requirements(root: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(
        (root / "config/gates/field-requirements.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(loaded, dict):
        raise ValueError("field requirements must be a mapping")
    return loaded


def _walk(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    if isinstance(data, Mapping):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                leaves.extend(_walk(value, path))
            else:
                leaves.append((path, value))
    return leaves


def _rule_for(path: str, requirements: Mapping[str, Any]) -> Mapping[str, Any]:
    default = cast(Mapping[str, Any], requirements["default"])
    rules = requirements.get("rules", [])
    if not isinstance(rules, list):
        return default
    for candidate in rules:
        if not isinstance(candidate, Mapping):
            continue
        rule = cast(Mapping[str, Any], candidate)
        if fnmatch.fnmatchcase(path, str(rule["match"])):
            return rule
    return default


def _is_gate_blocking(blocking_gate: str, requested_gate: str) -> bool:
    return GATE_ORDER.get(blocking_gate, 999) <= GATE_ORDER.get(requested_gate, -1)


def _value_at(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return default
        value = value[part]
    return value


def _is_external_provider(value: Any) -> bool:
    return str(value).casefold() not in {
        "",
        "__fill_me__",
        "deferred",
        "disabled",
        "local",
        "mineru",
        "deterministic_fake",
    }


def _secret_required(condition: str, user: Mapping[str, Any]) -> bool:
    modes = {
        "mysql_real": _value_at(user, "infrastructure.mysql.provision_mode")
        in {"native_local", "existing_native"},
        "milvus_authenticated_real": _value_at(user, "infrastructure.milvus.provision_mode")
        in {"native_local", "existing_native"},
        "rabbitmq_real": _value_at(user, "infrastructure.rabbitmq.provision_mode")
        in {"native_local", "existing_native"},
        "redis_real": _value_at(user, "infrastructure.redis.provision_mode")
        in {"native_local", "existing_native"},
        "oidc_real": _value_at(user, "infrastructure.oidc.provider")
        in {"enterprise_idp", "keycloak"},
        "mineru_hosted_enabled": _value_at(user, "ai_services.mineru.hosted_api_enabled", False)
        is True,
        "llm_external": _is_external_provider(_value_at(user, "ai_services.llm.provider")),
        "embedding_external": _is_external_provider(
            _value_at(user, "ai_services.embedding.provider")
        ),
        "reranker_external": _is_external_provider(
            _value_at(user, "ai_services.reranker.provider")
        ),
        "ocr_external": _is_external_provider(_value_at(user, "ai_services.ocr.provider")),
        "asr_external": _is_external_provider(_value_at(user, "ai_services.asr.provider")),
    }
    return modes.get(condition, True)


def _missing_value(value: Any) -> bool:
    return value in {None, "", PLACEHOLDER, "deferred", "not_applicable"}


def _integration_profiles(
    loaded: LoadedConfiguration, secret_environment: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    user = loaded.user
    secrets = {item["name"]: item for item in secret_environment}
    approved_regions = _value_at(user, "security_compliance.approved_ai_processing_regions", [])

    def build(
        *,
        selected: bool,
        fields: tuple[str, ...] = (),
        secret_names: tuple[str, ...] = (),
        region_approval: bool = False,
        extra: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        blockers = [path for path in fields if _missing_value(_value_at(user, path))]
        blockers.extend(
            f"env:{name}"
            for name in secret_names
            if secrets.get(name, {}).get("required_for_current_mode")
            and not secrets.get(name, {}).get("configured")
        )
        if selected and region_approval and not approved_regions:
            blockers.append("security_compliance.approved_ai_processing_regions")
        blockers.extend(extra if selected else ())
        return {
            "selected": selected,
            "ready": selected and not blockers,
            "status": "NOT_SELECTED" if not selected else ("READY" if not blockers else "BLOCKED"),
            "blockers": sorted(set(blockers)) if selected else [],
        }

    mysql_real = _value_at(user, "infrastructure.mysql.provision_mode") in {
        "native_local",
        "existing_native",
    }
    milvus_real = _value_at(user, "infrastructure.milvus.provision_mode") in {
        "native_local",
        "existing_native",
    }
    audio_required = bool(
        {"wav", "mp3", "m4a"}.intersection(set(_value_at(user, "scope.r1_required_formats", [])))
    )
    return {
        "mysql": build(
            selected=mysql_real,
            fields=("infrastructure.mysql.host",),
            secret_names=("MYSQL_PASSWORD",),
        ),
        "milvus": build(
            selected=milvus_real,
            fields=("infrastructure.milvus.uri",),
            secret_names=("MILVUS_TOKEN",),
        ),
        "mineru_self_hosted": build(
            selected=True,
            fields=(
                "ai_services.mineru.self_hosted_endpoint",
                "ai_services.mineru.version",
            ),
            extra=("native_mineru_package_and_health_probe",),
        ),
        "mineru_hosted": build(
            selected=_value_at(user, "ai_services.mineru.hosted_api_enabled") is True,
            fields=("ai_services.mineru.hosted_api_endpoint",),
            secret_names=("MINERU_TOKEN",),
            region_approval=True,
        ),
        "llm": build(
            selected=_is_external_provider(_value_at(user, "ai_services.llm.provider")),
            fields=("ai_services.llm.endpoint", "ai_services.llm.model_id"),
            secret_names=("LLM_API_KEY",),
            region_approval=True,
        ),
        "embedding": build(
            selected=_is_external_provider(_value_at(user, "ai_services.embedding.provider")),
            fields=("ai_services.embedding.endpoint", "ai_services.embedding.model_id"),
            secret_names=("EMBEDDING_API_KEY",),
            region_approval=True,
        ),
        "reranker": build(
            selected=_is_external_provider(_value_at(user, "ai_services.reranker.provider")),
            fields=("ai_services.reranker.endpoint", "ai_services.reranker.model_id"),
            secret_names=("RERANKER_API_KEY",),
            region_approval=True,
        ),
        "asr": build(
            selected=audio_required,
            fields=(
                "ai_services.asr.provider",
                "ai_services.asr.endpoint",
                "ai_services.asr.model_id",
            ),
            secret_names=("ASR_API_KEY",),
            region_approval=True,
        ),
    }


def build_validation_report(
    loaded: LoadedConfiguration, requested_gate: str = "G0"
) -> dict[str, Any]:
    requirements = _load_requirements(loaded.repository_root)
    missing_inputs: list[dict[str, Any]] = []
    for path, value in _walk(loaded.user):
        rule = _rule_for(path, requirements)
        empty_is_missing = bool(rule.get("empty_is_missing", False))
        if value != PLACEHOLDER and not (empty_is_missing and value in ("", [])):
            continue
        priority = str(rule["priority"])
        blocking_gate = str(rule["blocking_gate"])
        responsibility = str(rule.get("responsibility", "technical_review"))
        missing_inputs.append(
            {
                "path": path,
                "priority": priority,
                "blocking_gate": blocking_gate,
                "blocks_requested_gate": _is_gate_blocking(blocking_gate, requested_gate),
                "effective_source": "stub" if path in loaded.stubbed_paths else "unresolved",
                "responsibility": responsibility,
            }
        )

    secret_environment: list[dict[str, Any]] = []
    for item in loaded.secret_statuses:
        required_for_current_mode = _secret_required(item.required_when, loaded.user)
        secret_environment.append(
            {
                "name": item.name,
                "configured": item.configured,
                "source": item.source,
                "priority": item.priority,
                "blocking_gate": item.blocking_gate,
                "required_when": item.required_when,
                "required_for_current_mode": required_for_current_mode,
                "responsibility": "user",
                "blocks_requested_gate": (
                    required_for_current_mode
                    and not item.configured
                    and _is_gate_blocking(item.blocking_gate, requested_gate)
                ),
            }
        )

    decision_blockers: list[dict[str, str]] = []
    approvals = loaded.user.get("adr_approvals", {})
    if isinstance(approvals, Mapping):
        for name, status in approvals.items():
            if status not in {"approve", PLACEHOLDER}:
                decision_blockers.append(
                    {
                        "path": f"adr_approvals.{name}",
                        "status": str(status),
                        "blocking_gate": "G0",
                    }
                )
    gate_blockers = [item["path"] for item in missing_inputs if item["blocks_requested_gate"]]
    gate_blockers.extend(
        f"env:{item['name']}" for item in secret_environment if item["blocks_requested_gate"]
    )
    if _is_gate_blocking("G0", requested_gate):
        gate_blockers.extend(
            f"decision:{item['path']}:{item['status']}" for item in decision_blockers
        )
    if loaded.schema_errors:
        gate_blockers.extend(f"schema:{item['path']}" for item in loaded.schema_errors)

    integrations = _integration_profiles(loaded, secret_environment)
    user_action_required = [
        item["path"] for item in missing_inputs if item["responsibility"] == "user"
    ]
    user_action_required.extend(
        f"env:{item['name']}"
        for item in secret_environment
        if item["responsibility"] == "user"
        and item["required_for_current_mode"]
        and not item["configured"]
    )
    user_gate_blockers = [
        item["path"]
        for item in missing_inputs
        if item["responsibility"] == "user" and item["blocks_requested_gate"]
    ]

    return {
        "report_schema_version": 1,
        "requested_gate": requested_gate,
        "source_precedence": {
            "non_sensitive": ["user_yaml", "stub_defaults"],
            "secrets": ["process_environment", "local_env_file"],
        },
        "safe_output_contract": "secret names and presence only; values are never returned",
        "schema_errors": list(loaded.schema_errors),
        "missing_inputs": sorted(missing_inputs, key=lambda item: item["path"]),
        "secret_environment": secret_environment,
        "decision_blockers": decision_blockers,
        "readiness_profiles": {
            "basic_stub_startup": {
                "ready": not loaded.schema_errors,
                "blockers": [f"schema:{item['path']}" for item in loaded.schema_errors],
                "attestation": (
                    "Ready means local Stub startup only, not real integration acceptance."
                ),
            },
            "real_integrations": integrations,
        },
        "user_action_required": sorted(set(user_action_required)),
        "user_blockers_for_requested_gate": sorted(set(user_gate_blockers)),
        "stubbed_paths": sorted(loaded.stubbed_paths),
        "summary": {
            "missing_input_count": len(missing_inputs),
            "stubbed_input_count": len(loaded.stubbed_paths),
            "configured_secret_name_count": sum(
                1 for item in secret_environment if item["configured"]
            ),
            "missing_secret_name_count": sum(
                1 for item in secret_environment if not item["configured"]
            ),
            "required_missing_secret_name_count": sum(
                1
                for item in secret_environment
                if item["required_for_current_mode"] and not item["configured"]
            ),
            "decision_blocker_count": len(decision_blockers),
            "user_action_required_count": len(set(user_action_required)),
            "user_blocker_count_for_requested_gate": len(set(user_gate_blockers)),
            "real_integration_ready_count": sum(
                1 for profile in integrations.values() if profile["ready"]
            ),
            "schema_error_count": len(loaded.schema_errors),
            "gate_blocker_count": len(gate_blockers),
            "gate_ready": not gate_blockers,
            "stub_development_ready": not loaded.schema_errors,
        },
        "gate_blockers": sorted(gate_blockers),
        "attestation": "Stub results are not real-service or Gate acceptance evidence.",
    }
