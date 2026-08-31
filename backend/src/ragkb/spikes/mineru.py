"""MinerU token-pool and real-sample coverage harness."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from ragkb.adapters.mineru_pool import MinerUTokenPool
from ragkb.config import EnvLoadResult
from ragkb.config.env import TOKEN_STRATEGY_ROUND_ROBIN
from ragkb.spikes.common import result

REQUIRED_FORMAT_COUNTS = {
    "pdf_text": 10,
    "pdf_scanned_or_image": 10,
    "docx": 10,
    "pptx": 10,
    "spreadsheet": 10,
    "audio": 10,
}


def _manifest(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        return {"samples": []}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, Mapping) else {"samples": []}


def run_mineru_spike(loaded: EnvLoadResult, manifest_path: Path) -> dict[str, object]:
    settings = loaded.settings
    if settings is None:
        return result(
            "mineru_pool_and_formats",
            [{"name": "typed_env_available", "passed": False}],
            ["config/.env:typed_validation_failed"],
        )
    manifest = _manifest(manifest_path)
    samples = manifest.get("samples", [])
    samples = samples if isinstance(samples, list) else []
    plan = manifest.get("collection_plan", [])
    plan = plan if isinstance(plan, list) else []
    planned = {
        str(item.get("format")): int(item.get("required_count", 0))
        for item in plan
        if isinstance(item, Mapping)
    }
    counts = Counter(
        str(item.get("format"))
        for item in samples
        if isinstance(item, Mapping) and item.get("real_sample") is True
    )
    schema = json.loads(
        (
            loaded.repository_root
            / "backend/src/ragkb/contracts/schemas/canonical-document-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    pool_ready = loaded.configured["MINERU_TOKENS"]
    pool_status: dict[str, object] = {"token_count": 0, "secret_values_in_status": False}
    if pool_ready:
        pool = MinerUTokenPool[object](
            settings.mineru_tokens,
            max_concurrency_per_token=settings.mineru_max_concurrency_per_token,
            max_failures=settings.mineru_token_max_failures,
            cooldown_seconds=settings.mineru_token_cooldown_seconds,
            failover_enabled=settings.mineru_failover_enabled,
        )
        pool_status = pool.status()
    assertions = [
        {
            "name": "sixty_slot_plan",
            "passed": all(
                planned.get(key) == value for key, value in REQUIRED_FORMAT_COUNTS.items()
            ),
        },
        {"name": "canonical_schema", "passed": bool(schema.get("$schema"))},
        {
            "name": "round_robin_strategy",
            "passed": settings.mineru_token_strategy == TOKEN_STRATEGY_ROUND_ROBIN,
        },
        {
            "name": "secret_safe_status",
            "passed": pool_status.get("secret_values_in_status") is False,
        },
    ]
    blockers = [
        f"real_samples.{name}:need_{required}_found_{counts[name]}"
        for name, required in REQUIRED_FORMAT_COUNTS.items()
        if counts[name] < required
    ]
    if not pool_ready:
        blockers.append("MINERU_TOKENS:not_configured")
    return result(
        "mineru_pool_and_formats",
        assertions,
        blockers,
        {
            "planned_sample_slots": sum(planned.values()),
            "provided_real_samples": sum(counts.values()),
            "pool": pool_status,
            "real_request_sent": False,
        },
    )
