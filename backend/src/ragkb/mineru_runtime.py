"""Native MinerU wrapper status entry for the G0 self-hosted-first contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ragkb.adapters.mineru import MinerUEndpoint, MinerURouter
from ragkb.config.loader import PLACEHOLDER, load_configuration


def _value_at(data: Mapping[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        value = value[part]
    return value


def check_mineru_runtime() -> dict[str, object]:
    loaded = load_configuration()
    config = loaded.effective
    self_hosted = MinerUEndpoint(
        "self_hosted",
        str(_value_at(config, "ai_services.mineru.self_hosted_endpoint")),
        True,
    )
    hosted_enabled = _value_at(config, "ai_services.mineru.hosted_api_enabled") is True
    hosted = MinerUEndpoint(
        "hosted",
        str(_value_at(config, "ai_services.mineru.hosted_api_endpoint")),
        hosted_enabled,
    )
    allowed = frozenset(
        str(item)
        for item in _value_at(config, "ai_services.mineru.hosted_api_allowed_data_classifications")
    )
    router = MinerURouter(self_hosted, hosted, allowed)
    confidential_route = router.select("confidential").kind
    version = _value_at(loaded.user, "ai_services.mineru.version")
    return {
        "runtime": "native_python_mineru_wrapper",
        "deployment_strategy": _value_at(config, "ai_services.mineru.deployment_strategy"),
        "self_hosted_primary": confidential_route == "self_hosted",
        "hosted_api_enabled": hosted_enabled,
        "hosted_region_approval_required": _value_at(
            config, "ai_services.mineru.hosted_api_requires_region_approval"
        )
        is True,
        "native_mineru_package_available": importlib.util.find_spec("mineru") is not None,
        "version_configured": version != PLACEHOLDER,
        "real_service_acceptance": False,
        "secret_values_in_output": False,
    }


def run_mineru(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or inspect the native MinerU wrapper")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    status = check_mineru_runtime()
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    if args.check:
        return 0
    if not status["native_mineru_package_available"] or not status["version_configured"]:
        print(
            "MinerU native service is blocked until its package/version and server "
            "command are frozen."
        )
        return 2
    print(
        "MinerU package detected; exact native server command still requires approved "
        "adapter config."
    )
    return 2
