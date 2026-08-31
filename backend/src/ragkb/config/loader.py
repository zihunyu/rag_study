"""Load project configuration while keeping secrets opaque."""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ragkb.config.models import LoadedConfiguration, SecretStatus

PLACEHOLDER = "__FILL_ME__"
UNCONFIGURED_SECRET_MARKERS = frozenset({"", "__fill_me__", "deferred", "not_applicable"})


def find_repository_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "完整开发计划.md").is_file():
            return candidate
    raise FileNotFoundError("repository root marker 完整开发计划.md was not found")


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"configuration must be a mapping: {path}")
    return loaded


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(data: MutableMapping[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: MutableMapping[str, Any] = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, MutableMapping):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def _leaf_paths(data: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(data, Mapping):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                paths.extend(_leaf_paths(value, path))
            else:
                paths.append(path)
    return paths


def _merge_stub_values(
    user: Mapping[str, Any], stubs: Mapping[str, Any]
) -> tuple[dict[str, Any], frozenset[str]]:
    effective: dict[str, Any] = copy.deepcopy(dict(user))
    stubbed: set[str] = set()
    for path in _leaf_paths(stubs):
        user_value = _get_path(user, path)
        if user_value in (None, PLACEHOLDER):
            _set_path(effective, path, _get_path(stubs, path))
            stubbed.add(path)
    return effective, frozenset(stubbed)


def _validate_schema(user: Mapping[str, Any], schema_path: Path) -> tuple[dict[str, str], ...]:
    schema = __import__("json").loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors: list[dict[str, str]] = []
    for error in sorted(validator.iter_errors(user), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append({"path": path, "message": error.message})
    return tuple(errors)


def _validate_local_storage_paths(
    effective: Mapping[str, Any], repository_root: Path
) -> tuple[dict[str, str], ...]:
    storage = effective.get("infrastructure", {}).get("local_storage", {})
    if not isinstance(storage, Mapping):
        return ({"path": "infrastructure.local_storage", "message": "must be a mapping"},)
    configured_root = Path(str(storage.get("root_path", "")))
    resolved_root = (
        configured_root if configured_root.is_absolute() else repository_root / configured_root
    ).resolve()
    errors: list[dict[str, str]] = []
    for name in (
        "original_path",
        "artifact_path",
        "quarantine_path",
        "temp_path",
        "audit_path",
    ):
        configured_path = Path(str(storage.get(name, "")))
        resolved = (
            configured_path if configured_path.is_absolute() else repository_root / configured_path
        ).resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            errors.append(
                {
                    "path": f"infrastructure.local_storage.{name}",
                    "message": "must remain inside infrastructure.local_storage.root_path",
                }
            )
    return tuple(errors)


def _secret_value_is_configured(opaque_value: str | None) -> bool:
    if opaque_value is None:
        return False
    normalized = opaque_value.strip().strip('"').strip("'").casefold()
    return normalized not in UNCONFIGURED_SECRET_MARKERS and not normalized.startswith(
        "__fill_me__ #"
    )


def _configured_names_from_local_env(path: Path, expected_names: set[str]) -> set[str]:
    """Return configured names without retaining or returning any values."""

    configured: set[str] = set()
    if not path.is_file():
        return configured
    with path.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, opaque_value = line.partition("=")
            normalized_name = name.strip()
            if normalized_name in expected_names and _secret_value_is_configured(opaque_value):
                configured.add(normalized_name)
            opaque_value = ""  # minimize lifetime; never stored, logged, or returned
    return configured


def _load_secret_statuses(
    requirements: Mapping[str, Any], env_file: Path, environ: Mapping[str, str]
) -> tuple[SecretStatus, ...]:
    items = requirements.get("secret_environment", [])
    expected = {str(item["name"]) for item in items}
    local_configured = _configured_names_from_local_env(env_file, expected)
    statuses: list[SecretStatus] = []
    for item in items:
        name = str(item["name"])
        process_configured = _secret_value_is_configured(environ.get(name))
        if process_configured:
            configured, source = True, "process_environment"
        elif name in local_configured:
            configured, source = True, "local_env_file"
        else:
            configured, source = False, "missing"
        statuses.append(
            SecretStatus(
                name=name,
                configured=configured,
                source=source,
                priority=str(item["priority"]),
                blocking_gate=str(item["blocking_gate"]),
                required_when=str(item.get("required_when", "always")),
            )
        )
    return tuple(statuses)


def load_configuration(
    repository_root: Path | None = None,
    user_path: Path | None = None,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LoadedConfiguration:
    """Load, validate and overlay safe stubs without exposing secret values."""

    root = find_repository_root(repository_root)
    resolved_user_path = user_path or root / "config/user-input/project-inputs.yaml"
    resolved_env_file = env_file or root / "config/user-input/.env.user.local"
    user = _load_yaml_mapping(resolved_user_path)
    stubs = _load_yaml_mapping(root / "config/defaults/stub-defaults.yaml")
    requirements = _load_yaml_mapping(root / "config/gates/field-requirements.yaml")
    effective, stubbed_paths = _merge_stub_values(user, stubs)
    schema_errors = _validate_schema(user, root / "config/schema/project-inputs.schema.json")
    schema_errors += _validate_local_storage_paths(effective, root)
    secret_statuses = _load_secret_statuses(
        requirements, resolved_env_file, environ if environ is not None else os.environ
    )
    return LoadedConfiguration(
        repository_root=root,
        user_path=resolved_user_path,
        user=user,
        effective=effective,
        stubbed_paths=stubbed_paths,
        schema_errors=schema_errors,
        secret_statuses=secret_statuses,
    )
