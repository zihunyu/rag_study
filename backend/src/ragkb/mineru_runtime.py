"""Native MinerU multi-token pool status entry without real service calls."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.config import build_env_report, load_env


def check_mineru_runtime() -> dict[str, object]:
    loaded = load_env()
    report = build_env_report(loaded, "G1")
    settings = loaded.settings
    if settings is None:
        return {
            "runtime": "mineru_token_pool",
            "configuration_valid": False,
            "real_service_acceptance": False,
            "secret_values_in_output": False,
        }
    token_configured = loaded.configured.get("MINERU_TOKENS", False)
    pool_status: dict[str, object] = {
        "strategy": settings.mineru_token_strategy,
        "token_count": 0,
        "available_count": 0,
        "secret_values_in_status": False,
    }
    if token_configured:
        pool = MinerUTokenPool[object](
            settings.mineru_tokens,
            max_concurrency_per_token=settings.mineru_max_concurrency_per_token,
            max_failures=settings.mineru_token_max_failures,
            cooldown_seconds=settings.mineru_token_cooldown_seconds,
            failover_enabled=settings.mineru_failover_enabled,
        )
        pool_status = pool.status()
    return {
        "runtime": "mineru_token_pool",
        "configuration_valid": True,
        "g1_gate_ready": report["summary"]["gate_ready"],  # type: ignore[index]
        "pool": pool_status,
        "real_service_acceptance": False,
        "secret_values_in_output": False,
    }


def run_mineru(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the MinerU multi-token pool")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    status = check_mineru_runtime()
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    if not status.get("configuration_valid"):
        return 2
    if not args.check:
        print("MinerU token pool contract is ready; no real request was sent.")
    return 0
