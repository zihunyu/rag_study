"""Shared helpers for safe G0 evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def value_at(data: Mapping[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        value = value[part]
    return value


def is_stubbed(stubbed_paths: frozenset[str], path: str) -> bool:
    return path in stubbed_paths or any(item.startswith(f"{path}.") for item in stubbed_paths)


def result(
    spike: str,
    assertions: list[dict[str, object]],
    blockers: list[str],
    metrics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    harness_passed = all(bool(item["passed"]) for item in assertions)
    return {
        "report_schema_version": 1,
        "spike": spike,
        "harness_passed": harness_passed,
        "real_gate_status": "BLOCKED" if blockers else "READY_FOR_REAL_REVIEW",
        "real_acceptance": False,
        "assertions": assertions,
        "metrics": dict(metrics or {}),
        "blockers": sorted(set(blockers)),
        "attestation": "Harness or Stub results are not real-service Gate acceptance evidence.",
    }
